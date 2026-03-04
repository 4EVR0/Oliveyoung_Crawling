import time
from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from config.Settings import NAV_TIMEOUT


class BrowserManager:
    """
    Playwright 브라우저 시작/종료/팝업 닫기를 담당.
    크래시 복구는 호출부(orchestrator)에서 이 클래스를 재생성해 처리한다.
    """

    def __init__(self, headless: bool = True):
        self.headless   = headless
        self._playwright = None
        self.browser:  Browser        = None
        self.context:  BrowserContext = None
        self.page:     Page           = None

    # ------------------------------------------------------------------ #
    #  공개 인터페이스
    # ------------------------------------------------------------------ #

    def start(self):
        """브라우저를 시작하고 page 객체를 준비한다."""
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        self.page = self.context.new_page()
        self.page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        print("🌐 브라우저 시작 완료")

    def close(self):
        """브라우저와 Playwright 인스턴스를 안전하게 종료한다."""
        try:
            if self.browser:
                self.browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self.browser = self.context = self.page = self._playwright = None
        print("🔒 브라우저 종료")

    def restart(self, delay: float = 5.0):
        """크래시 복구용: 브라우저를 닫고 다시 시작한다."""
        print("♻️  브라우저 재시작 중...")
        self.close()
        time.sleep(delay)
        self.start()

    def close_popups(self):
        """화면에 떠 있는 팝업을 닫는다."""
        selectors = [
            "button.btnClose", ".popup-close", ".btn-close",
            "#layerClose", "button:has-text('닫기')", ".layer_close",
        ]
        for selector in selectors:
            try:
                popup = self.page.locator(selector).first
                if popup.is_visible(timeout=500):
                    popup.click()
                    time.sleep(0.3)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    #  상태 조회
    # ------------------------------------------------------------------ #

    @property
    def is_alive(self) -> bool:
        """페이지가 살아 있는지 확인"""
        return bool(self.page and not self.page.is_closed())
