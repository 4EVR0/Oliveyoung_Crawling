import os
from datetime import datetime
from typing import Optional

from config.Categories import CATEGORIES, PERSON_CATEGORIES
from config.Settings import TEMP_DIR, PERSON
from crawler.Browser import BrowserManager
from crawler.Product_Fetcher import ProductFetcher
from storage.checkpoint import CheckpointManager
from storage.FileWriter import save_json, save_csv


class SampleProductFetcher(ProductFetcher):
    """기존 ProductFetcher를 그대로 쓰되, 상세 수집만 최대 N개에서 끊는 테스트용"""

    def __init__(self, browser, checkpoint, s3=None, max_products: int = 10):
        super().__init__(browser=browser, checkpoint=checkpoint, s3=s3)
        self.max_products = max_products
        self.collected_count = 0

    def _fetch_page_products(
        self,
        urls: list[str],
        main_cat: str,
        sub_cat: str,
        page_num: int,
    ) -> list[dict]:
        products = []
        i = 0

        while i < len(urls):
            if self.collected_count >= self.max_products:
                print(f"    ✅ 테스트 제한 도달: {self.max_products}개 수집 완료")
                break

            url = urls[i]
            print(f"    [{page_num}-{i+1}/{len(urls)}] {url[-30:]}")

            try:
                if not self.browser.is_alive:
                    self.browser.restart()

                product = self._fetch_single(url, main_cat, sub_cat)
                if product and product.get("name"):
                    products.append(product)
                    self.collected_count += 1

                i += 1

            except Exception as e:
                err = str(e).lower()
                if any(k in err for k in ("crashed", "closed", "context")):
                    print(f"    🚨 브라우저 크래시 감지 → 재시작 후 재시도: {str(e)[:60]}")
                    self.browser.restart()
                else:
                    print(f"    ✗ 수집 실패 (스킵): {str(e)[:60]}")
                    i += 1

        return products


def run_sample_crawl(
    target_categories: dict,
    headless: bool,
    person: Optional[str] = None,
    max_products: int = 10,
):
    os.makedirs(TEMP_DIR, exist_ok=True)

    checkpoint = CheckpointManager(person=person)
    browser = BrowserManager(headless=headless)
    browser.start()

    fetcher = SampleProductFetcher(
        browser=browser,
        checkpoint=checkpoint,
        s3=None,
        max_products=max_products,
    )

    all_products: list[dict] = []

    try:
        for main_cat, sub_cats in target_categories.items():
            for sub_cat in sub_cats:
                if fetcher.collected_count >= max_products:
                    break

                try:
                    print(f"\n{'='*60}")
                    print(f"[TEST] 수집 시작: {main_cat} > {sub_cat}")

                    products = fetcher.fetch_subcategory(main_cat, sub_cat)
                    all_products.extend(products)

                    print(f"[TEST] 누적 수집: {len(all_products)}개")

                    if fetcher.collected_count >= max_products:
                        break

                except Exception as e:
                    print(f"  ❌ '{main_cat} > {sub_cat}' 오류: {e}")
                    if any(k in str(e).lower() for k in ("crashed", "closed", "net::err")):
                        browser.restart()

            if fetcher.collected_count >= max_products:
                break

    finally:
        browser.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(TEMP_DIR, f"sample_products_{ts}.json")
    csv_path = os.path.join(TEMP_DIR, f"sample_products_{ts}.csv")

    save_json(all_products, json_path)
    save_csv(all_products, csv_path)

    print(f"\n{'='*60}")
    print(f"테스트 완료 — 총 {len(all_products)}개 상품 수집")
    print(f"JSON: {json_path}")
    print(f"CSV : {csv_path}")
    print(f"{'='*60}")

    return all_products


def main():
    person = PERSON if PERSON else None

    if person:
        target = PERSON_CATEGORIES[person]
        label = f"담당자: {person}"
    else:
        target = CATEGORIES
        label = "전체 크롤링"

    print("=" * 60)
    print(f"샘플 테스트 크롤러 | {label}")
    print("S3 미사용 / temp_crawl 저장 / 최대 10개")
    print("=" * 60)

    run_sample_crawl(
        target_categories=target,
        headless=False,
        person=person,
        max_products=10,
    )


if __name__ == "__main__":
    main()
