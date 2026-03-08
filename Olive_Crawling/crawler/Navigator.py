import re
import time
from playwright.sync_api import Page
from config.Settings import BASE_URL


class Navigator:
    """
    올리브영 사이트 내 카테고리 이동과 페이지네이션을 담당.
    Page 객체를 주입받아 사용하므로 BrowserManager 와 느슨하게 결합된다.
    """

    def __init__(self, page: Page):
        self.page = page

    # ------------------------------------------------------------------ #
    #  카테고리 이동
    # ------------------------------------------------------------------ #

    def go_home(self):
        """올리브영 메인 페이지로 이동"""
        self.page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)
        time.sleep(3)

    def go_to_subcategory(self, main_cat: str, sub_cat: str) -> bool:
        """
        메인카테고리 호버 → 서브카테고리 클릭으로 해당 목록 페이지로 이동.
        성공하면 True, 실패하면 False 반환.
        """
        print(f"\n  '{main_cat} > {sub_cat}' 페이지로 이동 중...")
        try:
            cat_btn = self.page.locator(
                "button:has-text('카테고리'), a:has-text('카테고리')"
            ).first
            if cat_btn.is_visible(timeout=3_000):
                cat_btn.click()
                time.sleep(1)

            main_link = self.page.locator(f"a:has-text('{main_cat}')").first
            if main_link.is_visible(timeout=3_000):
                main_link.hover()
                time.sleep(0.5)

            sub_link = self.page.locator(f"a:has-text('{sub_cat}')").first
            if sub_link.is_visible(timeout=3_000):
                sub_link.click()
                time.sleep(3)
                print(f"    ✅ 이동 완료: {self.page.title()}")
                return True

        except Exception as e:
            print(f"    ❌ 카테고리 이동 실패: {e}")

        return False

    # ------------------------------------------------------------------ #
    #  페이지네이션
    # ------------------------------------------------------------------ #

    def get_total_pages(self) -> int:
        """
        현재 페이지에서 전체 페이지 수를 파악한다.
        '끝' 버튼 href → '다음 그룹' 버튼 존재 → 현재 노출 번호 중 최대값 순으로 시도.
        """
        # 1) '끝' 버튼 href 에서 pageIdx 추출
        for selector in ["a.last", 
                         "a:has-text('끝')",
                         "a:has-text('맨끝')",
                         "a[title*='마지막']", 
                         "a[title*='끝']"]:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=500):
                    href  = btn.get_attribute("href") or ""
                    match = re.search(r"pageIdx=(\d+)", href)
                    if match:
                        total = int(match.group(1))
                        print(f"    (총 {total}페이지)")
                        return total
            except Exception:
                continue

        # 2) '다음 그룹' 버튼이 보이면 → 순회 모드
        for selector in ["a:has-text('>>')", 
                         "a.next", 
                         "a:has-text('다음')",
                         ".pageing a.next", 
                         ".paging a.next", 
                         "a[class*='next']"]:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=500):
                    print("    (다음 그룹 버튼 발견 → 전체 순회 모드)")
                    return 999
            except Exception:
                continue

        # 3) 현재 보이는 숫자 중 최대값
        max_page = 1
        for el in self.page.locator(".pageing a, .paging a").all():
            try:
                text = el.inner_text().strip()
                if text.isdigit():
                    max_page = max(max_page, int(text))
            except Exception:
                pass
        return max_page

    def go_to_page(self, page_num: int) -> bool:
        """
        특정 페이지 번호로 이동한다.
        현재 그룹에 없으면 '다음 그룹(>>)' 버튼으로 그룹을 넘긴 뒤 다시 탐색한다.
        """
        for _ in range(10):   # 무한루프 방지
            # 현재 그룹에서 번호 찾기
            for link in self.page.locator(".pageing a, .paging a").all():
                try:
                    if link.inner_text().strip() == str(page_num):
                        link.click()
                        time.sleep(2)
                        return True
                except Exception:
                    continue

            # 다음 그룹으로 이동
            moved = False
            for selector in ["a:has-text('>>')", ".pageing a.next", ".paging a.next",
                             "a[class*='next']", "a:has-text('다음')"]:
                try:
                    btn = self.page.locator(selector).first
                    if btn.is_visible(timeout=500):
                        btn.click()
                        time.sleep(2)
                        moved = True
                        break
                except Exception:
                    continue

            if not moved:
                print(f"      페이지 {page_num}: 더 이상 이동 불가")
                return False

        return False

    def goto_url(self, url: str, wait: str = "networkidle", timeout: int = 30_000):
        """URL 직접 이동 (URL 기반 페이지 이동용)"""
        self.page.goto(url, wait_until=wait, timeout=timeout)
        time.sleep(1)
