import re
import time
from datetime import datetime
from playwright.sync_api import Page
from config.Settings import BASE_URL
from config.Categories import (
    COSMETIC_KEYWORDS,
    REVIEW_PATTERNS
)
from crawler.Utils import canonicalize_goods_url
from Model.Products import make_product_dict


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

                    if href.startswith("javascript:"):
                        # javascript: href 에서 실제 URL 추출
                        match = re.search(r"'(https://[^']+)'", href)
                        if match:
                            href = match.group(1)
                        else:
                            continue  # 추출 실패하면 스킵
                    elif not href.startswith("http"):
                        href = BASE_URL + href

                    urls.append(canonicalize_goods_url(href))
                except Exception:
                    pass
            break

        return list(dict.fromkeys(urls))
    # ------------------------------------------------------------------ #
    #  상세 페이지 → 상품 정보 파싱
    # ------------------------------------------------------------------ #

    def parse_product(self, url: str, main_cat: str, sub_cat: str) -> dict:
        """
        상세 페이지가 이미 열려 있다고 가정하고 상품 정보를 파싱한다.
        (페이지 이동은 호출부 ProductFetcher 에서 처리)
        """
        product = make_product_dict(url, main_cat, sub_cat)  

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
        self._parse_review_stats(product)

        stats_found = bool(product.get("review_stats"))        
        review_status = "✓" if stats_found else "△"
        print(f"      {review_status} {product['brand']} - {product['name'][:25]}...")
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

    # ------------------------------------------------------------------ #
    #  내부: 리뷰 통계 파싱
    # ------------------------------------------------------------------ #

    def _parse_review_stats(self, product: dict):
        """리뷰&셔터 탭에서 평점, 리뷰수, 피부타입/고민/자극도 통계를 파싱한다."""
        try:
            # 리뷰&셔터 탭 클릭
            review_tab = self.page.locator("button:has-text('리뷰&셔터')").first
            if review_tab.is_visible(timeout=3_000):
                review_tab.click()
                time.sleep(2)

            # 평점 및 리뷰 수
            try:
                review_area = self.page.locator("[class*='ReviewArea']").first
                if review_area.is_visible(timeout=2_000):
                    area_text = review_area.inner_text()
                    rating_match = re.search(r'(\d+\.?\d*)', area_text)
                    if rating_match:
                        product["rating"] = rating_match.group(1)
                    count_match = re.search(r'(\d[\d,]*)\s*건', area_text)
                    if count_match:
                        product["review_count"] = count_match.group(1).replace(',', '')
            except Exception:
                pass

            # 자세히 보기 → Shadow DOM 통계 추출
            try:
                detail_btn = self.page.locator("text=자세히 보기").first
                if detail_btn.is_visible(timeout=3_000):
                    detail_btn.click()
                    time.sleep(3)
                    self._parse_shadow_dom_stats(product)

                    # 팝업 닫기
                    try:
                        close_btn = self.page.locator("text=닫기").first
                        if close_btn.is_visible(timeout=1_000):
                            close_btn.click()
                            time.sleep(0.5)
                    except Exception:
                        pass
            except Exception:
                pass

        except Exception:
            pass

    def _parse_shadow_dom_stats(self, product: dict):
        """Shadow DOM(oy-review-modal-component) 에서 리뷰 통계 퍼센트를 추출한다."""
        try:
            shadow_text = self.page.evaluate("""
                () => {
                    function getAllText(element) {
                        let text = '';
                        if (element.shadowRoot) {
                            text += getAllText(element.shadowRoot);
                        }
                        for (const child of element.childNodes) {
                            if (child.nodeType === Node.TEXT_NODE) {
                                text += child.textContent + ' ';
                            } else if (child.nodeType === Node.ELEMENT_NODE) {
                                text += getAllText(child);
                            }
                        }
                        return text;
                    }
                    const modal = document.querySelector('oy-review-modal-component');
                    return modal ? getAllText(modal) : '';
                }
            """)

            if not shadow_text:
                return

            text = re.sub(r'\s+', ' ', shadow_text)

            
            for pattern, category, key in REVIEW_PATTERNS:
                match = re.search(pattern, text)
                if match:
                    product["review_stats"].setdefault(category, {})[key] = f"{match.group(1)}%"
        except Exception as e:
            print(f"      ⚠️ Shadow DOM 추출 실패: {e}")
