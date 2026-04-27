import asyncio
from unittest.mock import AsyncMock

import crawler.Product_Fetcher as product_fetcher_module
from crawler.Product_Fetcher import ProductFetcher


class DummyCheckpoint:
    def __init__(self):
        self.marked_pages = []
        self.done_subcategories = []

    def is_subcategory_done(self, main_cat, sub_cat):
        return False

    def get_completed_pages(self, main_cat, sub_cat):
        return {1}

    def mark_page_done(self, main_cat, sub_cat, page_num):
        self.marked_pages.append((main_cat, sub_cat, page_num))

    def mark_subcategory_done(self, main_cat, sub_cat):
        self.done_subcategories.append((main_cat, sub_cat))

    def get_cached_urls(self, main_cat, sub_cat):
        return None

    def set_cached_urls(self, main_cat, sub_cat, urls):
        return None


class DummyPage:
    def __init__(self):
        self.closed = False

    async def goto(self, *args, **kwargs):
        return None

    async def close(self):
        self.closed = True


def test_fetch_single_with_retry_restarts_on_context_crash(monkeypatch):
    checkpoint = DummyCheckpoint()

    browser = type("Browser", (), {})()
    browser.new_page = AsyncMock(side_effect=[DummyPage(), DummyPage()])
    browser.close_popups = AsyncMock()
    browser.restart = AsyncMock()

    fetcher = ProductFetcher(browser=browser, checkpoint=checkpoint, s3=None)

    class FakeParser:
        call_count = 0

        def __init__(self, page):
            self.page = page

        async def parse_product(self, url, main_cat, sub_cat):
            FakeParser.call_count += 1
            if FakeParser.call_count == 1:
                raise Exception("browser context closed")
            return {"name": "테스트 상품", "brand": "브랜드"}

    monkeypatch.setattr(product_fetcher_module, "Parser", FakeParser)

    product = asyncio.run(
        fetcher._fetch_single_with_retry(
            "https://www.oliveyoung.co.kr/store/goods/getGoodsDetail.do?goodsNo=A0001",
            "스킨케어",
            "크림",
        )
    )

    assert product["name"] == "테스트 상품"
    assert "crawled_at" in product
    browser.restart.assert_awaited_once()


def test_fetch_subcategory_resumes_from_checkpoint(monkeypatch):
    checkpoint = DummyCheckpoint()

    browser = type("Browser", (), {})()
    fetcher = ProductFetcher(browser=browser, checkpoint=checkpoint, s3=None)

    monkeypatch.setattr(
        fetcher,
        "_get_product_urls",
        AsyncMock(return_value=[f"https://example.com/{i}" for i in range(40)]),
    )

    async def fake_fetch_page_products(urls, main_cat, sub_cat, page_num):
        return [{"name": f"상품-{page_num}", "url": urls[0]}]

    monkeypatch.setattr(fetcher, "_fetch_page_products", fake_fetch_page_products)

    products = asyncio.run(fetcher.fetch_subcategory("스킨케어", "크림"))

    assert len(products) == 1
    assert products[0]["name"] == "상품-2"
    assert checkpoint.marked_pages == [("스킨케어", "크림", 2)]
    assert checkpoint.done_subcategories == [("스킨케어", "크림")]
