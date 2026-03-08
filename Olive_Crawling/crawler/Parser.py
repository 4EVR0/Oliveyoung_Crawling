import re
import time
from datetime import datetime
from playwright.sync_api import Page
from config.Settings import BASE_URL
from config.Categories import COSMETIC_KEYWORDS
from crawler.Utils import canonicalize_goods_url


class Parser:
    """
    현재 page 에서 상품 URL 목록 추출 및 상세 정보 파싱을 담당.
    브라우저 이동(Navigator)과 분리되어 '읽기' 역할만 수행한다.
    """

    def __init__(self, page: Page):
        self.page = page

    # ------------------------------------------------------------------ #
    #  목록 페이지 → URL 수집
    # ------------------------------------------------------------------ #

    def get_product_urls(self) -> list[str]:
        """현재 목록 페이지에서 상품 상세 URL 을 추출해 정규화된 목록으로 반환"""
        urls = []
        selectors = [
            "a.prd_thumb[data-ref-goodsno]",
            "a[href*='getGoodsDetail']",
            ".prd_info a[href*='goodsNo']",
        ]
        for selector in selectors:
            links = self.page.locator(selector).all()
            if not links:
                continue
            for link in links:
                try:
                    href = link.get_attribute("href") or ""
                    if "getGoodsDetail" not in href:
                        continue
                    if not href.startswith("http"):
                        href = BASE_URL + href
                    urls.append(canonicalize_goods_url(href))
                except Exception:
                    pass
            break   # 첫 번째로 매칭된 selector 만 사용

        # 중복 제거 (순서 유지)
        return list(dict.fromkeys(urls))

    # ------------------------------------------------------------------ #
    #  상세 페이지 → 상품 정보 파싱
    # ------------------------------------------------------------------ #

    def parse_product(self, url: str, main_cat: str, sub_cat: str) -> dict:
        """
        상세 페이지가 이미 열려 있다고 가정하고 상품 정보를 파싱한다.
        (페이지 이동은 호출부 ProductFetcher 에서 처리)
        """
        product = {
            "url":           canonicalize_goods_url(url),
            "main_category": main_cat,
            "sub_category":  sub_cat,
            "name":          "",
            "brand":         "",
            "price":         "",
            "ingredients":   "",
            "product_info":  {},
            "crawled_at":    datetime.now().isoformat(),
        }

        # 상품명 (타이틀에서 ' | 올리브영' 제거)
        raw_title = self.page.title()
        product["name"] = (
            raw_title.rsplit(" | 올리브영", 1)[0].strip()
            if " | 올리브영" in raw_title
            else raw_title.strip()
        )

        try:
            product["brand"] = (
                self.page.locator("[class*='brand']").first.inner_text().strip()
            )
        except Exception:
            pass

        try:
            product["price"] = (
                self.page.locator(".price").first.inner_text().strip()
            )
        except Exception:
            pass

        self._parse_disclosure(product)
        return product

    # ------------------------------------------------------------------ #
    #  내부: 상품정보제공고시 파싱
    # ------------------------------------------------------------------ #

    def _parse_disclosure(self, product: dict):
        """상품정보제공고시 테이블에서 화장품법 관련 항목을 파싱해 product 에 채운다."""
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)

            try:
                btn = self.page.locator("button:has-text('상품정보 제공고시')").first
                if btn.is_visible(timeout=3_000):
                    btn.click()
                    time.sleep(1.5)
            except Exception:
                pass

            for table in self.page.locator("table").all():
                for row in table.locator("tr").all():
                    try:
                        key   = row.locator("th").first.inner_text().strip()
                        value = row.locator("td").first.inner_text().strip()
                        if key and value and any(kw in key for kw in COSMETIC_KEYWORDS):
                            product["product_info"][key] = value
                            if "화장품법" in key and "성분" in key:
                                product["ingredients"] = value
                    except Exception:
                        continue
        except Exception:
            pass
