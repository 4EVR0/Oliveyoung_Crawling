import asyncio
import re
from playwright.async_api import Page
from config.Settings import BASE_URL


class Navigator:
    def __init__(self, page: Page):
        self.page = page

    async def go_home(self):
        await self.page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(3)

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
                        print(f"    (총 {total}페이지)")
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
                    print("    (다음 그룹 버튼 발견 → 전체 순회 모드)")
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
        print(f"\n  '{main_cat} > {sub_cat}' 페이지로 이동 중...")
        try:
            cat_btn = self.page.locator(
                "button:has-text('카테고리'), a:has-text('카테고리')"
            ).first
            if await cat_btn.is_visible(timeout=3000):
                await cat_btn.click()
                await asyncio.sleep(1)

            main_link = self.page.locator(f"a:has-text('{main_cat}')").first
            if await main_link.is_visible(timeout=3000):
                await main_link.hover()
                await asyncio.sleep(1)

            sub_link = self.page.locator(
                f"a:has-text('{main_cat}') >> xpath=../../.. >> a:has-text('{sub_cat}')"
            ).first

            if not await sub_link.is_visible(timeout=3000):
                print(f"    ⚠️ 서브메뉴에서 '{sub_cat}' 못 찾음")
                return False

            await sub_link.click()
            await asyncio.sleep(3)
            print(f"    ✅ 이동 완료: {await self.page.title()}")
            return True

        except Exception as e:
            print(f"    ❌ 카테고리 이동 실패: {e}")
            return False

    async def goto_url(self, url: str, wait: str = "networkidle", timeout: int = 30_000):
        await self.page.goto(url, wait_until=wait, timeout=timeout)
        await asyncio.sleep(1)
