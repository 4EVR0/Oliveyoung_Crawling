"""
올리브영 화장품 크롤러 — 진입점

실행 예시
─────────
python main.py --person 재원 --s3-bucket my-bucket
python main.py --s3-bucket my-bucket          # 전체 크롤링
python main.py                                # 로컬 저장만
"""

import argparse
import asyncio
from typing import Optional

from cosme_common.batch import build_batch_id

from config.Categories import CATEGORIES, PERSON_CATEGORIES
from config.Settings import S3_BUCKET, PERSON
from crawler.Browser import BrowserManager
from crawler.Product_Fetcher import ProductFetcher
from storage.checkpoint import CheckpointManager
from storage.S3_Uploader import S3Uploader
from storage.FileWriter import save_json, save_csv


async def _crawl_categories(
    fetcher: ProductFetcher,
    browser: BrowserManager,
    target_categories: dict,
) -> tuple[list[dict], bool]:
    """카테고리 순회 + 수집. (all_products, success) 반환"""
    all_products: list[dict] = []
    success = True

    for main_cat, sub_cats in target_categories.items():
        for sub_cat in sub_cats:
            try:
                products = await fetcher.fetch_subcategory(main_cat, sub_cat)
                all_products.extend(products)
            except KeyboardInterrupt:
                print("\n⛔ 사용자 중단 감지 (Ctrl+C)")
                success = False
                raise
            except Exception as e:
                print(f"  ❌ '{main_cat} > {sub_cat}' 오류: {e}")
                success = False
                if any(k in str(e).lower() for k in ("crashed", "closed", "net::err")):
                    await browser.restart()

    return all_products, success


async def run_crawl(
    target_categories: dict,
    s3_bucket: Optional[str],
    headless: bool,
    person: str = None,
):
    checkpoint = CheckpointManager(person=person)
    s3 = (
        S3Uploader(bucket=s3_bucket, run_id=checkpoint._state["run_id"])
        if s3_bucket else None
    )

    browser = BrowserManager(headless=headless)
    await browser.start()
    fetcher = ProductFetcher(browser=browser, checkpoint=checkpoint, s3=s3)

    all_products: list[dict] = []
    success = True

    try:
        all_products, success = await _crawl_categories(fetcher, browser, target_categories)
    finally:
        await browser.close()
        if s3:
            s3.finalize(success=success)

    return all_products


def main():
    parser = argparse.ArgumentParser(description="올리브영 화장품 크롤러")
    parser.add_argument("--s3-bucket", default=S3_BUCKET, help="S3 버킷 이름")
    parser.add_argument(
        "--person",
        default=PERSON,
        choices=list(PERSON_CATEGORIES.keys()) + [""],
        help="담당자 이름",
    )
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()

    if args.person:
        target = PERSON_CATEGORIES[args.person]
        label = f"담당자: {args.person}"
    else:
        target = CATEGORIES
        label = "전체 크롤링"

    print("=" * 60)
    print(f"올리브영 크롤러 | {label}")
    if args.s3_bucket:
        print(f"S3 버킷: {args.s3_bucket}")
    else:
        print("S3 미사용 (로컬 저장만)")
    print("=" * 60)

    try:
        products = asyncio.run(
            run_crawl(
                target_categories=target,
                s3_bucket=args.s3_bucket or None,
                headless=args.headless,
                person=args.person,
            )
        )
    except KeyboardInterrupt:
        print("\n🛑 크롤링이 사용자에 의해 중단되었습니다.")
        return

    ts = build_batch_id()
    save_json(products, f"oliveyoung_products_{ts}.json")
    save_csv(products, f"oliveyoung_products_{ts}.csv")

    print(f"\n{'=' * 60}")
    print(f"완료 — 총 {len(products)}개 상품 수집")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
