import asyncio
import re
from datetime import datetime, timezone

from config.Settings import (
    PAGE_TIMEOUT,
    CRAWL_DELAY,
    RETRY_COUNT,
    DETAIL_CONCURRENCY,
    PAGE_SIZE,
)
from crawler.Browser import BrowserManager
from crawler.Navigator import Navigator
from crawler.Parser import Parser
from storage.checkpoint import CheckpointManager
from storage.S3_Uploader import S3Uploader


class ProductFetcher:
    def __init__(
        self,
        browser: BrowserManager,
        checkpoint: CheckpointManager,
        s3: S3Uploader | None,
    ):
        self.browser = browser
        self.checkpoint = checkpoint
        self.s3 = s3

    async def fetch_subcategory(self, main_cat: str, sub_cat: str) -> list[dict]:
        print(f"\n{'=' * 60}")
        print(f"수집 시작: {main_cat} > {sub_cat}")

        if self.checkpoint.is_subcategory_done(main_cat, sub_cat):
            print("  ⏭️ 이미 완료된 서브카테고리 — 스킵")
            return []

        if self.s3 and self.s3.is_subcategory_uploaded(main_cat, sub_cat):
            print("  ⏭️ S3 manifest 기준 이미 업로드 완료 — 스킵")
            return []

        product_urls = await self._get_product_urls(main_cat, sub_cat)
        if not product_urls:
            print("  ⚠️ 수집할 URL 없음")
            return []

        completed_pages = self.checkpoint.get_completed_pages(main_cat, sub_cat)
        pages = self._group_urls_by_page(product_urls, PAGE_SIZE)
        all_products = []

        await asyncio.sleep(2)

        for page_num, page_urls in pages.items():
            if page_num in completed_pages:
                print(f"  ⏭️ 페이지 {page_num} 스킵 (이미 완료)")
                continue

            page_products = await self._fetch_page_products(
                page_urls, main_cat, sub_cat, page_num
            )

            if self.s3 and page_products:
                self.s3.add_products(main_cat, sub_cat, page_products)
                self.s3.flush_subcategory(main_cat, sub_cat)

            self.checkpoint.mark_page_done(main_cat, sub_cat, page_num)
            all_products.extend(page_products)
            print(f"  ✅ 페이지 {page_num} 완료 ({len(page_products)}개)")

        self.checkpoint.mark_subcategory_done(main_cat, sub_cat)
        if self.s3:
            self.s3.save_manifest_checkpoint()

        print(f"\n✓ '{main_cat} > {sub_cat}' 완료 — 총 {len(all_products)}개")
        return all_products

    async def _get_product_urls(self, main_cat: str, sub_cat: str) -> list[str]:
        cached = self.checkpoint.get_cached_urls(main_cat, sub_cat)
        if cached:
            print(f"  📂 URL 캐시 사용: {len(cached)}개")
            return cached

        nav = Navigator(self.browser.page)
        parser = Parser(self.browser.page)

        await nav.go_home()
        if not await nav.go_to_subcategory(main_cat, sub_cat):
            return []

        all_urls = []
        current_url = self.browser.page.url
        page_num = 1
        prev_urls = None

        while page_num <= 100:
            target_url = (
                re.sub(r"pageIdx=\d+", f"pageIdx={page_num}", current_url)
                if "pageIdx=" in current_url
                else f"{current_url}&pageIdx={page_num}"
            )

            try:
                await nav.goto_url(target_url, wait="domcontentloaded")
                await asyncio.sleep(1.2)

                new_urls = await parser.get_product_urls()

                if not new_urls:
                    break

                if new_urls == prev_urls:
                    break

                all_urls.extend(new_urls)
                prev_urls = new_urls
                page_num += 1

            except Exception as e:
                print(f"  ⚠️ URL 수집 중단: {str(e)[:80]}")
                break

        all_urls = list(dict.fromkeys(all_urls))
        self.checkpoint.set_cached_urls(main_cat, sub_cat, all_urls)
        print(f"  🔎 URL 수집 완료: {len(all_urls)}개")
        return all_urls

    def _group_urls_by_page(self, urls: list[str], page_size: int) -> dict[int, list[str]]:
        pages = {}
        for i, url in enumerate(urls):
            page_num = i // page_size + 1
            pages.setdefault(page_num, []).append(url)
        return pages

    async def _fetch_page_products(
        self,
        urls: list[str],
        main_cat: str,
        sub_cat: str,
        page_num: int,
    ) -> list[dict]:
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def worker(idx: int, url: str):
            async with sem:
                print(f"    [{page_num}-{idx + 1}/{len(urls)}] {url[-30:]}")
                return await self._fetch_single_with_retry(url, main_cat, sub_cat)

        tasks = [worker(i, url) for i, url in enumerate(urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        products = []
        for result in results:
            if isinstance(result, Exception):
                print(f"    ✗ 수집 실패(스킵): {str(result)[:80]}")
                continue
            if result and result.get("name"):
                products.append(result)

        return products

    async def _fetch_single_with_retry(self, url: str, main_cat: str, sub_cat: str):
        for attempt in range(RETRY_COUNT):
            page = None
            try:
                page = await self.browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await asyncio.sleep(1.5)

                await self.browser.close_popups(page)

                parser = Parser(page)
                product = await parser.parse_product(url, main_cat, sub_cat)

                if product:
                    product["crawled_at"] = datetime.now(timezone.utc).isoformat()

                if CRAWL_DELAY > 0:
                    await asyncio.sleep(CRAWL_DELAY)

                await page.close()
                return product

            except Exception as e:
                err = str(e).lower()

                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass

                if any(k in err for k in ("crashed", "closed", "context")):
                    if attempt == RETRY_COUNT - 1:
                        raise
                    await self.browser.restart()

                if attempt < RETRY_COUNT - 1:
                    print(f"      ⚠️ 재시도 {attempt + 1}/{RETRY_COUNT}: {str(e)[:60]}")
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
