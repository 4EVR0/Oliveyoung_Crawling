import re
import time
from config.Settings import PAGE_TIMEOUT, CRAWL_DELAY, RETRY_COUNT, TEMP_DIR, BASE_URL
from crawler.Browser import BrowserManager
from crawler.Navigator import Navigator
from crawler.Parser import Parser
from crawler.Utils import canonicalize_goods_url
from storage.checkpoint import CheckpointManager
from storage.S3_Uploader import S3Uploader
import os, json


class ProductFetcher:
    """
    단일 서브카테고리의 상품 수집을 담당.

    흐름
    ────
    1. URL 수집 (캐시 or 전체 페이지 순회)
    2. 이미 완료된 URL 은 체크포인트로 건너뜀
    3. 상세 페이지 → Parser 로 파싱
    4. S3 업로드 성공 후 checkpoint.mark_page_done() 호출
    5. Page Crashed 등 치명적 에러 → BrowserManager.restart() 후 같은 URL 재시도
    """

    def __init__(
        self,
        browser: BrowserManager,
        checkpoint: CheckpointManager,
        s3: S3Uploader | None,
    ):
        self.browser    = browser
        self.checkpoint = checkpoint
        self.s3         = s3

    # ------------------------------------------------------------------ #
    #  공개 인터페이스
    # ------------------------------------------------------------------ #

    def fetch_subcategory(self, main_cat: str, sub_cat: str) -> list[dict]:
        """
        서브카테고리 전체 상품을 수집하고 수집된 상품 목록을 반환.
        체크포인트에 따라 완료된 URL 은 건너뛴다.
        """
        print(f"\n{'='*60}")
        print(f"수집 시작: {main_cat} > {sub_cat}")

        # 1) 이미 완전히 끝난 서브카테고리는 스킵
        if self.checkpoint.is_subcategory_done(main_cat, sub_cat):
            print(f"  ⏭️  이미 완료된 서브카테고리 — 스킵")
            return []

        # 2) URL 목록 확보
        product_urls = self._get_product_urls(main_cat, sub_cat)
        if not product_urls:
            print(f"  ⚠️ 수집할 URL 없음")
            return []

        # 3) 이미 완료된 URL 제외
        completed_pages = self.checkpoint.get_completed_pages(main_cat, sub_cat)
        # URL 을 페이지 단위로 묶어서 처리
        pages = self._group_urls_by_page(product_urls)
        all_products = []

        for page_num, page_urls in pages.items():
            if page_num in completed_pages:
                print(f"  ⏭️  페이지 {page_num} 스킵 (이미 완료)")
                continue

            page_products = self._fetch_page_products(page_urls, main_cat, sub_cat, page_num)

            # S3 업로드 성공 후에만 페이지 완료 기록
            if self.s3 and page_products:
                self.s3.add_products(main_cat, sub_cat, page_products)
                self.s3.flush_subcategory(main_cat, sub_cat)

            self.checkpoint.mark_page_done(main_cat, sub_cat, page_num)
            all_products.extend(page_products)
            print(f"  ✅ 페이지 {page_num} 완료 ({len(page_products)}개)")

        # 4) 서브카테고리 전체 완료
        self.checkpoint.mark_subcategory_done(main_cat, sub_cat)
        if self.s3:
            self.s3.save_manifest_checkpoint()

        print(f"\n✓ '{main_cat} > {sub_cat}' 완료 — 총 {len(all_products)}개")
        return all_products

    # ------------------------------------------------------------------ #
    #  URL 수집
    # ------------------------------------------------------------------ #
    def _get_product_urls(self, main_cat: str, sub_cat: str) -> list[str]:
        #"""체크포인트 캐시 → 로컬 파일 → 브라우저 탐색 순으로 URL 목록을 확보"""
        # 체크포인트 캐시
        cached = self.checkpoint.get_cached_urls(main_cat, sub_cat)
        if cached:
            print(f"  📂 URL 캐시 사용: {len(cached)}개")
            return cached

        # 브라우저로 수집
        nav    = Navigator(self.browser.page)
        parser = Parser(self.browser.page)

        nav.go_home()
        if not nav.go_to_subcategory(main_cat, sub_cat):
            return []

        all_urls   = []
        current_url = self.browser.page.url
        page_num   = 1
        prev_urls  = None


        while page_num <= 100:
            target_url = (
                re.sub(r"pageIdx=\d+", f"pageIdx={page_num}", current_url)
                if "pageIdx=" in current_url
                else f"{current_url}&pageIdx={page_num}"
            )
            try:
                nav.goto_url(target_url)

                new_urls = parser.get_product_urls()
                if not new_urls or new_urls == prev_urls:
                    break
                all_urls.extend(new_urls)
                prev_urls = new_urls
                page_num += 1
            except Exception:
                break

        all_urls = list(dict.fromkeys(all_urls))
        self.checkpoint.set_cached_urls(main_cat, sub_cat, all_urls)
        print(f"  🔎 URL 수집 완료: {len(all_urls)}개")
        return all_urls


    # ------------------------------------------------------------------ #
    #  페이지 단위 상세 수집
    # ------------------------------------------------------------------ #

    def _group_urls_by_page(self, urls: list[str], page_size: int = 20) -> dict[int, list[str]]:
        """
        URL 목록을 페이지 단위(page_size 개)로 묶는다.
        체크포인트의 페이지 번호와 1:1 대응하기 위한 논리적 분할.
        """
        pages = {}
        for i, url in enumerate(urls):
            page_num = i // page_size + 1
            pages.setdefault(page_num, []).append(url)
        return pages

    def _fetch_page_products(
        self,
        urls: list[str],
        main_cat: str,
        sub_cat: str,
        page_num: int,
    ) -> list[dict]:
        """URL 목록에 대해 상세 정보를 수집. 크래시 발생 시 브라우저 재시작 후 재시도."""
        products = []
        i = 0
        while i < len(urls):
            url = urls[i]
            print(f"    [{page_num}-{i+1}/{len(urls)}] {url[-30:]}")
            try:
                if not self.browser.is_alive:
                    self.browser.restart()

                product = self._fetch_single(url, main_cat, sub_cat)
                if product and product.get("name"):
                    products.append(product)
                i += 1
                time.sleep(CRAWL_DELAY)

            except Exception as e:
                err = str(e).lower()
                if any(k in err for k in ("crashed", "closed", "context")):
                    print(f"    🚨 브라우저 크래시 감지 → 재시작 후 재시도: {str(e)[:60]}")
                    self.browser.restart()
                    # i 를 올리지 않으므로 같은 URL 재시도
                else:
                    print(f"    ✗ 수집 실패 (스킵): {str(e)[:60]}")
                    i += 1

        return products

    def _fetch_single(self, url: str, main_cat: str, sub_cat: str) -> dict | None:
        """단일 상품 상세 페이지를 열고 파싱. 타임아웃은 RETRY_COUNT 만큼 재시도."""
        parser = Parser(self.browser.page)

        for attempt in range(RETRY_COUNT):
            try:
                print(f"      ⏳ 상세 페이지 로딩 중... {url[-30:]}")  # ← 추가
                self.browser.page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                time.sleep(2)
                self.browser.close_popups()
                product = parser.parse_product(url, main_cat, sub_cat)
                if product.get("name"):
                    print(f"      ✓ {product.get('brand','')} - {product['name'][:25]}")
                return product

            except Exception as e:
                err = str(e).lower()
                # 치명적 에러는 상위로 전파
                if any(k in err for k in ("crashed", "closed", "context")):
                    raise
                if attempt < RETRY_COUNT - 1:
                    print(f"      ⚠️ 재시도 {attempt+1}/{RETRY_COUNT}: {str(e)[:50]}")
                    time.sleep(5)

        return None
