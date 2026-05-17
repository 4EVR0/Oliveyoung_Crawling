import asyncio
import logging
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Async Playwright 브라우저 관리자.
    - 기본 context 하나 유지
    - 필요 시 상세 수집용 page를 여러 개 생성
    """

    def __init__(self, headless: bool = True):
        self.headless = headless
        self._playwright = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def start(self):
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self.context = await self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        self.page = await self.context.new_page()
        await self.page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        logger.info("브라우저 시작 완료")

    async def close(self):
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass

        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass

        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

        self.browser = self.context = self.page = self._playwright = None
        logger.info("브라우저 종료")

    async def restart(self, delay: float = 5.0):
        logger.info("브라우저 재시작 중...")
        await self.close()
        await asyncio.sleep(delay)
        await self.start()

    async def new_page(self) -> Page:
        if not self.context:
            raise RuntimeError("Browser context is not initialized")
        page = await self.context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        return page

    async def close_popups(self, page: Page):
        selectors = [
            "button.btnClose", ".popup-close", ".btn-close",
            "#layerClose", "button:has-text('닫기')", ".layer_close",
        ]
        for selector in selectors:
            try:
                popup = page.locator(selector).first
                if await popup.is_visible(timeout=500):
                    await popup.click()
                    await asyncio.sleep(0.2)
            except Exception:
                pass

    @property
    def is_alive(self) -> bool:
        return bool(self.page and not self.page.is_closed())
