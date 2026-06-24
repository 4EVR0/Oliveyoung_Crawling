import asyncio
import re
from urllib.parse import urljoin
from playwright.async_api import Page
from config.Settings import BASE_URL
from config.Categories import COSMETIC_KEYWORDS
from crawler.Utils import canonicalize_goods_url
from Model.Products import make_product_dict


class Parser:
    def __init__(self, page: Page):
        self.page = page

    async def get_product_urls(self) -> list[str]:
        urls = []
        selectors = [
            "a.prd_thumb[data-ref-goodsno]",
            "a[href*='getGoodsDetail']",
            ".prd_info a[href*='goodsNo']",
        ]

        for selector in selectors:
            links = self.page.locator(selector)
            count = await links.count()
            if count == 0:
                continue

            for i in range(count):
                link = links.nth(i)
                try:
                    href = await link.get_attribute("href") or ""
                    if "getGoodsDetail" not in href:
                        continue

                    if href.startswith("javascript:"):
                        match = re.search(r"'(https://[^']+)'", href)
                        if match:
                            href = match.group(1)
                        else:
                            continue
                    elif not href.startswith("http"):
                        href = BASE_URL + href

                    urls.append(canonicalize_goods_url(href))
                except Exception:
                    pass
            break

        return list(dict.fromkeys(urls))

    async def parse_product(self, url: str, main_cat: str, sub_cat: str) -> dict:
        product = make_product_dict(url, main_cat, sub_cat)

        raw_title = await self.page.title()
        product["name"] = (
            raw_title.rsplit(" | 올리브영", 1)[0].strip()
            if " | 올리브영" in raw_title
            else raw_title.strip()
        )

        try:
            product["brand"] = (
                await self.page.locator("[class*='brand']").first.inner_text()
            ).strip()
        except Exception:
            pass

        try:
            product["price"] = (
                await self.page.locator(".price").first.inner_text()
            ).strip()
        except Exception:
            pass

        product["image_url"] = await self._parse_image_url()

        await self._parse_disclosure(product)
        await self._parse_review_stats(product)

        return product

    async def _parse_image_url(self) -> str:
        selectors = [
            "meta[property='og:image']",
            "meta[name='twitter:image']",
            "#mainImg",
            ".prd_img img",
            ".prd_thumb img",
            "img[src*='goods']",
        ]

        for selector in selectors:
            try:
                locator = self.page.locator(selector).first
                image_url = ""
                if selector.startswith("meta"):
                    image_url = await locator.get_attribute("content") or ""
                else:
                    image_url = (
                        await locator.get_attribute("src")
                        or await locator.get_attribute("data-src")
                        or ""
                    )
                image_url = image_url.strip()
                if image_url:
                    return self._normalize_url(image_url)
            except Exception:
                continue
        return ""

    def _normalize_url(self, url: str) -> str:
        if url.startswith("//"):
            return f"https:{url}"
        return urljoin(BASE_URL, url)

    async def _parse_disclosure(self, product: dict):
        try:
            await self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)

            try:
                btn = self.page.locator("button:has-text('상품정보 제공고시')").first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    await asyncio.sleep(0.8)
            except Exception:
                pass

            tables = self.page.locator("table")
            table_count = await tables.count()
            for ti in range(table_count):
                rows = tables.nth(ti).locator("tr")
                row_count = await rows.count()

                for ri in range(row_count):
                    row = rows.nth(ri)
                    try:
                        key = (await row.locator("th").first.inner_text()).strip()
                        value = (await row.locator("td").first.inner_text()).strip()
                        if key and value and any(kw in key for kw in COSMETIC_KEYWORDS):
                            product["product_info"][key] = value
                            if "화장품법" in key and "성분" in key:
                                product["ingredients"] = value
                    except Exception:
                        continue
        except Exception:
            pass

    async def _parse_review_stats(self, product: dict):
        try:
            review_tab = self.page.locator("button:has-text('리뷰&셔터')").first
            if await review_tab.is_visible(timeout=3000):
                await review_tab.click()
                await asyncio.sleep(1)
            else:
                return

            review_area_locator = self.page.locator("[class*='ReviewArea']")
            count = await review_area_locator.count()

            review_area = None
            area_text = ""

            for i in range(count):
                area = review_area_locator.nth(i)
                try:
                    text = await area.inner_text(timeout=2000)
                    if "평점" in text and "리뷰" in text:
                        review_area = area
                        area_text = text
                        break
                except Exception:
                    continue

            if review_area is None:
                return

            rating_match = re.search(r'(\d+\.?\d*)', area_text)
            if rating_match:
                product["rating"] = rating_match.group(1)

            count_match = re.search(r'(\d[\d,]*)\s*건', area_text)
            if count_match:
                product["review_count"] = count_match.group(1).replace(",", "")

            detail_btn = self.page.locator("text=자세히 보기").first
            if await detail_btn.is_visible(timeout=3000):
                await detail_btn.click()
                await self.page.wait_for_selector(
                    "oy-review-modal-component",
                    state="attached",
                    timeout=5000,
                )
                await asyncio.sleep(0.5)
                await self._parse_shadow_dom_stats(product)

                try:
                    close_btn = self.page.locator("text=닫기").first
                    if await close_btn.is_visible(timeout=1000):
                        await close_btn.click()
                except Exception:
                    pass

        except Exception as e:
            print(f"      ⚠️ 리뷰 통계 파싱 실패: {e}")

    async def _parse_shadow_dom_stats(self, product: dict):
        try:
            stats = await self.page.evaluate("""
                () => {
                    const host = document.querySelector('oy-review-modal-component');
                    if (!host || !host.shadowRoot) return {};

                    const root = host.shadowRoot;
                    const result = {};

                    const detailHosts = Array.from(
                        root.querySelectorAll('oy-review-attribute-detail')
                    );

                    detailHosts.forEach((detailHost) => {
                        const detailRoot = detailHost.shadowRoot;
                        if (!detailRoot) return;

                        const titleEl = detailRoot.querySelector('h3.title');
                        if (!titleEl) return;

                        const category = titleEl.textContent.trim();
                        if (!category) return;

                        const items = Array.from(
                            detailRoot.querySelectorAll('li.feature-item')
                        );
                        if (!items.length) return;

                        result[category] = {};

                        items.forEach((item) => {
                            const labelEl = item.querySelector('span.label');
                            const percentEl = item.querySelector('span.percentage');

                            const label = labelEl ? labelEl.textContent.trim() : '';
                            const percent = percentEl
                                ? percentEl.textContent.replace(/\\s+/g, '')
                                : '';

                            if (label && percent) {
                                result[category][label] = percent;
                            }
                        });

                        if (!Object.keys(result[category]).length) {
                            delete result[category];
                        }
                    });

                    return result;
                }
            """)

            if stats:
                product["review_stats"] = stats

        except Exception as e:
            print(f"      ⚠️ Shadow DOM 추출 실패: {e}")
