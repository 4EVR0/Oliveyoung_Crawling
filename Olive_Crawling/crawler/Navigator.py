import asyncio
import logging
import re
from playwright.async_api import Page
from config.Settings import BASE_URL

logger = logging.getLogger(__name__)


class Navigator:
    def __init__(self, page: Page):
        self.page = page

    async def go_home(self):
        await self.page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30_000)

        ready_selectors = [
            "button:has-text('카테고리')",
            "a:has-text('카테고리')",
            "a:has-text('스킨케어')",
            "header",
            "#Header",
        ]
        for selector in ready_selectors:
            try:
                ready = self.page.locator(selector).first
                if await ready.is_visible(timeout=5_000):
                    logger.info("홈 진입 완료: selector=%s title=%s", selector, await self.page.title())
                    return
            except Exception:
                continue

        logger.warning(
            "홈 진입 후 주요 selector 미확인: url=%s title=%s",
            self.page.url,
            await self.page.title(),
        )

    async def get_total_pages(self) -> int:
        """
        현재 페이지에서 전체 페이지 수를 파악한다.
        sync 버전과 동일하게:
        1) '끝' 버튼 href → pageIdx 추출
        2) '다음 그룹' 버튼 존재 시 999 반환
        3) 현재 보이는 페이지 숫자 중 최대값 반환
        """
        for selector in [
            "a.last",
            "a:has-text('끝')",
            "a:has-text('맨끝')",
            "a[title*='마지막']",
            "a[title*='끝']",
        ]:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    href = await btn.get_attribute("href") or ""
                    match = re.search(r"pageIdx=(\d+)", href)
                    if match:
                        total = int(match.group(1))
                        logger.debug("총 %d페이지", total)
                        return total
            except Exception:
                continue

        for selector in [
            "a:has-text('>>')",
            "a.next",
            "a:has-text('다음')",
            ".pageing a.next",
            ".paging a.next",
            "a[class*='next']",
        ]:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    logger.debug("다음 그룹 버튼 발견 → 전체 순회 모드")
                    return 999
            except Exception:
                continue

        max_page = 1
        links = self.page.locator(".pageing a, .paging a")
        count = await links.count()

        for i in range(count):
            try:
                text = (await links.nth(i).inner_text()).strip()
                if text.isdigit():
                    max_page = max(max_page, int(text))
            except Exception:
                pass

        return max_page

    async def go_to_subcategory(self, main_cat: str, sub_cat: str) -> bool:
        logger.info("'%s > %s' 페이지로 이동 중...", main_cat, sub_cat)
        try:
            cat_btn = self.page.locator(
                "button:has-text('카테고리'), a:has-text('카테고리')"
            ).first
            if await cat_btn.is_visible(timeout=3000):
                await cat_btn.click()
                await asyncio.sleep(1)

            main_link = self.page.locator(f"a:has-text('{main_cat}')").first
            if not await main_link.is_visible(timeout=3000):
                logger.warning("메인 카테고리 '%s' 못 찾음", main_cat)
                return False

            # headless 서버에서 hover()만으로 CSS :hover 미발동 → dispatch_event 병행
            await main_link.hover()
            await main_link.dispatch_event("mouseenter")
            await asyncio.sleep(1.5)

            # DOM 구조에 따라 XPath 깊이가 다를 수 있어 1~4단계 순차 탐색
            for depth in ["xpath=..", "xpath=../..", "xpath=../../..", "xpath=../../../.."]:
                try:
                    sub_link = self.page.locator(
                        f"a:has-text('{main_cat}') >> {depth} >> a:has-text('{sub_cat}')"
                    ).first
                    if await sub_link.is_visible(timeout=1000):
                        await sub_link.click()
                        await asyncio.sleep(3)
                        logger.info("이동 완료 (hover): %s", await self.page.title())
                        return True
                except Exception:
                    continue

            # fallback: 메인 카테고리 페이지로 직접 이동 후 LNB에서 서브카테고리 탐색
            logger.info("hover 방식 실패 → 메인 카테고리 직접 이동으로 재시도")
            main_href = await main_link.get_attribute("href")
            if main_href:
                nav_url = main_href if main_href.startswith("http") else f"https://www.oliveyoung.co.kr{main_href}"
                await self.page.goto(nav_url, wait_until="domcontentloaded", timeout=20_000)
            else:
                await main_link.click()
                await self.page.wait_for_load_state("domcontentloaded", timeout=15_000)
            await asyncio.sleep(2)

            for lnb_sel in [
                f".lnbMenu a:has-text('{sub_cat}')",
                f".cateList a:has-text('{sub_cat}')",
                f"#lnbMenu a:has-text('{sub_cat}')",
                f".leftMenu a:has-text('{sub_cat}')",
                f"aside a:has-text('{sub_cat}')",
                f"nav a:has-text('{sub_cat}')",
                f"a:has-text('{sub_cat}')",
            ]:
                try:
                    sub_link = self.page.locator(lnb_sel).first
                    if await sub_link.is_visible(timeout=1000):
                        await sub_link.click()
                        await asyncio.sleep(3)
                        logger.info("이동 완료 (LNB fallback): %s", await self.page.title())
                        return True
                except Exception:
                    continue

            logger.warning("서브메뉴에서 '%s' 못 찾음", sub_cat)
            return False

        except Exception as e:
            logger.error("카테고리 이동 실패: %s", e)
            return False

    async def goto_url(self, url: str, wait: str = "networkidle", timeout: int = 30_000):
        await self.page.goto(url, wait_until=wait, timeout=timeout)
        await asyncio.sleep(1)
