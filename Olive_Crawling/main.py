"""
올리브영 화장품 크롤러 — 진입점

실행 예시
─────────
python main.py --person 재원 --s3-bucket my-bucket
python main.py --s3-bucket my-bucket          # 전체 크롤링
python main.py                                # 로컬 저장만
"""

import argparse
import csv
import json
import os
from datetime import datetime
from typing import Optional

from config.Categories import CATEGORIES, PERSON_CATEGORIES
from config.Settings import S3_BUCKET, PERSON
from crawler.Browser import BrowserManager
from crawler.Product_Fetcher import ProductFetcher
from storage.checkpoint import CheckpointManager
from storage.S3_Uploader import S3Uploader


# ──────────────────────────────────────────────────────────────────────────── #
#  오케스트레이터
# ──────────────────────────────────────────────────────────────────────────── #

def run_crawl(
    target_categories: dict,
    s3_bucket: Optional[str],
    headless: bool,
    run_id: str,
):
    """카테고리 딕셔너리를 받아 전체 크롤링을 수행하고 상품 목록을 반환"""
    checkpoint = CheckpointManager(run_id=run_id)
    s3         = S3Uploader(bucket=s3_bucket, run_id=run_id) if s3_bucket else None
    browser    = BrowserManager(headless=headless)
    browser.start()

    fetcher    = ProductFetcher(browser=browser, checkpoint=checkpoint, s3=s3)
    all_products: list[dict] = []
    success = True

    try:
        for main_cat, sub_cats in target_categories.items():
            for sub_cat in sub_cats:
                try:
                    products = fetcher.fetch_subcategory(main_cat, sub_cat)
                    all_products.extend(products)
                except Exception as e:
                    print(f"  ❌ '{main_cat} > {sub_cat}' 오류: {e}")
                    success = False
                    # 브라우저 상태 이상이면 재시작 후 다음 서브카테고리 진행
                    if any(k in str(e).lower() for k in ("crashed", "closed", "net::err")):
                        browser.restart()
    finally:
        browser.close()
        if s3:
            s3.finalize(success=success)

    return all_products


# ──────────────────────────────────────────────────────────────────────────── #
#  저장 유틸
# ──────────────────────────────────────────────────────────────────────────── #

def save_json(products: list, filename: str):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)
    print(f"💾 JSON 저장: {filename} ({len(products)}개)")


def save_csv(products: list, filename: str):
    if not products:
        print("저장할 상품이 없습니다.")
        return

    info_keys    = sorted({k for p in products for k in p.get("product_info", {})})
    base_headers = ["name", "brand", "main_category", "sub_category",
                    "price", "ingredients", "url", "crawled_at"]

    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(base_headers + info_keys)
        for p in products:
            row = [p.get(h, "") for h in base_headers]
            row += [p.get("product_info", {}).get(k, "") for k in info_keys]
            writer.writerow(row)

    print(f"💾 CSV 저장: {filename} ({len(products)}개)")


# ──────────────────────────────────────────────────────────────────────────── #
#  CLI
# ──────────────────────────────────────────────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser(description="올리브영 화장품 크롤러")
    parser.add_argument("--s3-bucket", default=S3_BUCKET,  help="S3 버킷 이름")
    parser.add_argument("--person",    default=PERSON,
                        choices=list(PERSON_CATEGORIES.keys()) + [""],
                        help="담당자 이름")
    parser.add_argument("--headless",  action="store_true", default=True)
    parser.add_argument("--run-id",    default=None, help="재시작 시 동일 run_id 지정")
    args = parser.parse_args()

    # 대상 카테고리 결정
    if args.person:
        target = PERSON_CATEGORIES[args.person]
        label  = f"담당자: {args.person}"
    else:
        target = CATEGORIES
        label  = "전체 크롤링"

    run_id = args.run_id or datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print(f"올리브영 크롤러 | {label} | run_id={run_id}")
    if args.s3_bucket:
        print(f"S3 버킷: {args.s3_bucket}")
    else:
        print("S3 미사용 (로컬 저장만)")
    print("=" * 60)

    products = run_crawl(
        target_categories=target,
        s3_bucket=args.s3_bucket or None,
        headless=args.headless,
        run_id=run_id,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_json(products, f"oliveyoung_products_{ts}.json")
    save_csv(products,  f"oliveyoung_products_{ts}.csv")

    print(f"\n{'='*60}")
    print(f"완료 — 총 {len(products)}개 상품 수집")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()