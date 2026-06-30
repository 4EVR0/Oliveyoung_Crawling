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
import logging
import sys
from typing import Optional

from oliveyoung_common.batch import build_run_id
from oliveyoung_common.logging import job_unit, log_dq
from oliveyoung_common.logging import setup_logging

from config.Categories import CATEGORIES
from config.Settings import CATEGORY_RETRY_COUNT, CATEGORY_RETRY_DELAY, S3_BUCKET
from crawler.Browser import BrowserManager
from crawler.Product_Fetcher import CategoryNavigationError, ProductFetcher
from storage.checkpoint import CheckpointManager
from storage.S3_Uploader import S3Uploader
from storage.FileWriter import save_json, save_csv

setup_logging("oliveyoung-crawl")

logger = logging.getLogger(__name__)

CODE_VERSION = "20260624-nav-domcontentloaded"


async def _crawl_categories(
    fetcher: ProductFetcher,
    browser: BrowserManager,
    target_categories: dict,
) -> tuple[list[dict], bool]:
    """카테고리 순회 후 이동 실패 카테고리만 모아 재시도한다."""
    all_products: list[dict] = []
    success = True
    navigation_failures: list[tuple[str, str]] = []
    category_counts: dict[str, int] = {}

    for main_cat, sub_cats in target_categories.items():
        for sub_cat in sub_cats:
            try:
                products = await fetcher.fetch_subcategory(main_cat, sub_cat)
                all_products.extend(products)
                category_counts[f"{main_cat}>{sub_cat}"] = len(products)
            except CategoryNavigationError:
                navigation_failures.append((main_cat, sub_cat))
                logger.warning(
                    "카테고리 이동 실패 기억: %s > %s (전체 순회 후 재시도)",
                    main_cat,
                    sub_cat,
                )
            except KeyboardInterrupt:
                print("\n⛔ 사용자 중단 감지 (Ctrl+C)")
                success = False
                raise
            except Exception as e:
                print(f"  ❌ '{main_cat} > {sub_cat}' 오류: {e}")
                success = False
                if any(k in str(e).lower() for k in ("crashed", "closed", "net::err")):
                    await browser.restart()

    for retry_round in range(1, CATEGORY_RETRY_COUNT + 1):
        if not navigation_failures:
            break

        logger.warning(
            "카테고리 이동 실패 %d개 재시도 시작 (%d/%d)",
            len(navigation_failures),
            retry_round,
            CATEGORY_RETRY_COUNT,
        )
        await asyncio.sleep(CATEGORY_RETRY_DELAY)

        retry_targets = navigation_failures
        navigation_failures = []

        for main_cat, sub_cat in retry_targets:
            try:
                products = await fetcher.fetch_subcategory(main_cat, sub_cat)
                all_products.extend(products)
                category_counts[f"{main_cat}>{sub_cat}"] = len(products)
                logger.info("카테고리 재시도 성공: %s > %s", main_cat, sub_cat)
            except CategoryNavigationError:
                navigation_failures.append((main_cat, sub_cat))
                logger.warning(
                    "카테고리 재시도 실패: %s > %s (%d/%d)",
                    main_cat,
                    sub_cat,
                    retry_round,
                    CATEGORY_RETRY_COUNT,
                )
            except KeyboardInterrupt:
                print("\n⛔ 사용자 중단 감지 (Ctrl+C)")
                success = False
                raise
            except Exception as e:
                print(f"  ❌ '{main_cat} > {sub_cat}' 재시도 오류: {e}")
                success = False
                if any(
                    k in str(e).lower()
                    for k in ("crashed", "closed", "net::err")
                ):
                    await browser.restart()

    if navigation_failures:
        success = False
        failed_names = ", ".join(
            f"{main_cat} > {sub_cat}" for main_cat, sub_cat in navigation_failures
        )
        logger.error(
            "카테고리 이동 최종 실패 %d개: %s",
            len(navigation_failures),
            failed_names,
        )

    return all_products, success, navigation_failures, category_counts


async def run_crawl(
    target_categories: dict,
    s3_bucket: Optional[str],
    headless: bool,
    person: str | None = None,
):
    checkpoint = CheckpointManager(person=person, bucket=s3_bucket)
    run_id = checkpoint._state["run_id"]

    with job_unit(logger, job="oliveyoung_crawl", run_id=run_id):
        s3 = (
            S3Uploader(bucket=s3_bucket, run_id=run_id)
            if s3_bucket else None
        )

        browser = BrowserManager(headless=headless)
        await browser.start()
        fetcher = ProductFetcher(browser=browser, checkpoint=checkpoint, s3=s3)

        all_products: list[dict] = []
        success = True
        nav_failures: list[tuple[str, str]] = []
        category_counts: dict[str, int] = {}

        try:
            all_products, success, nav_failures, category_counts = await _crawl_categories(fetcher, browser, target_categories)
        finally:
            await browser.close()
            if s3:
                s3.finalize(success=success)

        # 정합성 메트릭 — 수집 안정성 + 적재 보존(crawl쪽)
        categories_total = sum(len(subs) for subs in target_categories.values())
        log_dq(
            logger,
            stage="crawl",
            run_id=run_id,
            products_total=len(all_products),
            categories_total=categories_total,
            categories_failed=len(nav_failures),
            categories_zero=sum(1 for c in category_counts.values() if c == 0),
        )

        return all_products


def _main_impl():
    parser = argparse.ArgumentParser(description="올리브영 화장품 크롤러")
    parser.add_argument("--s3-bucket", default=S3_BUCKET, help="S3 버킷 이름")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()

    print("=" * 60)
    print("올리브영 크롤러 | 전체 크롤링")
    print(f"코드 버전: {CODE_VERSION}")
    if args.s3_bucket:
        print(f"S3 버킷: {args.s3_bucket}")
    else:
        print("S3 미사용 (로컬 저장만)")
    print("=" * 60)

    try:
        products = asyncio.run(
            run_crawl(
                target_categories=CATEGORIES,
                s3_bucket=args.s3_bucket or None,
                headless=args.headless,
                person=None,
            )
        )
    except KeyboardInterrupt:
        print("\n🛑 크롤링이 사용자에 의해 중단되었습니다.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 크롤링 실패: {e}")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"완료 — 총 {len(products)}개 상품 수집")
    print(f"{'=' * 60}")

    # S3 없을 때만 로컬 저장 (로컬 개발/테스트용)
    if not args.s3_bucket:
        ts = build_run_id("oliveyoung_crawl")
        save_json(products, f"oliveyoung_products_{ts}.json")
        save_csv(products, f"oliveyoung_products_{ts}.csv")


def main():
    _main_impl()


if __name__ == "__main__":
    main()
