"""
올리브영 베스트 화장품 크롤러 (Playwright 버전)
- 서브카테고리별 모든 상품 크롤링
- 페이지네이션 처리
- 상품명, 카테고리, 화장품법에 따른 전성분 정보 수집
"""

import time
import json
import csv
import uuid
import os
import boto3
from io import BytesIO
from datetime import datetime
from typing import Optional
from playwright.sync_api import sync_playwright

# 임시 저장 디렉토리
TEMP_DIR = "temp_crawl"


class S3Uploader:
    """
    S3 업로더 - 4가지 원칙 준수:
    1. category/subcategory 기반 prefix
    2. run_id(또는 dt) 필수 포함
    3. 작은 파일 난사 대신 part로 묶기
    4. manifest.json로 스냅샷 완성도 보장
    """

    PART_SIZE = 100  # 한 part 파일당 최대 상품 수

    def __init__(self, bucket: str, run_id: Optional[str] = None):
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.manifest = {
            "run_id": self.run_id,
            "created_at": datetime.now().isoformat(),
            "status": "in_progress",
            "categories": {},
            "total_products": 0,
            "parts": []
        }
        self._buffer = {}  # {(main_cat, sub_cat): [products]}

    def _make_prefix(self, main_cat: str, sub_cat: str) -> str:
        """category/subcategory/run_id 기반 S3 prefix 생성"""
        # 공백/특수문자 처리
        main_safe = main_cat.replace(" ", "_").replace("/", "-")
        sub_safe = sub_cat.replace(" ", "_").replace("/", "-")
        return f"oliveyoung/{main_safe}/{sub_safe}/run_id={self.run_id}"

    def add_products(self, main_cat: str, sub_cat: str, products: list):
        """상품 데이터를 버퍼에 추가 (part로 묶기 위해)"""
        key = (main_cat, sub_cat)
        if key not in self._buffer:
            self._buffer[key] = []
        self._buffer[key].extend(products)

        # PART_SIZE 이상이면 flush
        while len(self._buffer[key]) >= self.PART_SIZE:
            chunk = self._buffer[key][:self.PART_SIZE]
            self._buffer[key] = self._buffer[key][self.PART_SIZE:]
            self._upload_part(main_cat, sub_cat, chunk)

    def _upload_part(self, main_cat: str, sub_cat: str, products: list, max_retries: int = 3):
        """part 파일 업로드 (재시도 로직 포함)"""
        prefix = self._make_prefix(main_cat, sub_cat)

        # 기존 part 수 확인해서 다음 번호 결정
        cat_key = f"{main_cat}/{sub_cat}"
        if cat_key not in self.manifest["categories"]:
            self.manifest["categories"][cat_key] = {"parts": [], "product_count": 0}

        part_num = len(self.manifest["categories"][cat_key]["parts"])
        part_key = f"{prefix}/part_{part_num:04d}.json"

        # S3 업로드 (재시도 로직)
        data = json.dumps(products, ensure_ascii=False, indent=2).encode("utf-8")

        for attempt in range(max_retries):
            try:
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=part_key,
                    Body=BytesIO(data),
                    ContentType="application/json"
                )
                break  # 성공하면 루프 탈출
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  ⚠️ S3 업로드 실패 (시도 {attempt + 1}/{max_retries}): {e}")
                    time.sleep(2 ** attempt)  # 지수 백오프: 1초, 2초, 4초
                else:
                    print(f"  ❌ S3 업로드 최종 실패: {part_key}")
                    raise  # 마지막 시도도 실패하면 예외 발생

        # manifest 업데이트
        part_info = {
            "key": part_key,
            "product_count": len(products),
            "uploaded_at": datetime.now().isoformat()
        }
        self.manifest["categories"][cat_key]["parts"].append(part_info)
        self.manifest["categories"][cat_key]["product_count"] += len(products)
        self.manifest["total_products"] += len(products)
        self.manifest["parts"].append(part_info)

        print(f"  📤 S3 업로드: {part_key} ({len(products)}개 상품)")

    def flush_category(self, main_cat: str, sub_cat: str):
        """특정 카테고리의 버퍼만 flush"""
        key = (main_cat, sub_cat)
        if key in self._buffer and self._buffer[key]:
            self._upload_part(main_cat, sub_cat, self._buffer[key])
            self._buffer[key] = []
            print(f"  ✅ '{main_cat} > {sub_cat}' 버퍼 flush 완료")

    def save_checkpoint(self):
        """중간 manifest 저장 (체크포인트)"""
        self.manifest["last_checkpoint"] = datetime.now().isoformat()
        self.manifest["status"] = "in_progress"
        manifest_key = f"oliveyoung/_manifests/run_id={self.run_id}/manifest.json"
        data = json.dumps(self.manifest, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=manifest_key,
                Body=BytesIO(data),
                ContentType="application/json"
            )
            print(f"  💾 체크포인트 저장: {self.manifest['total_products']}개 상품")
        except Exception as e:
            print(f"  ⚠️ 체크포인트 저장 실패: {e}")

    def flush(self):
        """버퍼에 남은 모든 데이터 업로드"""
        for (main_cat, sub_cat), products in self._buffer.items():
            if products:
                self._upload_part(main_cat, sub_cat, products)
        self._buffer.clear()

    def finalize(self, success: bool = True):
        """크롤링 완료 후 manifest.json 업로드"""
        # 남은 버퍼 flush
        self.flush()

        # manifest 완성
        self.manifest["status"] = "completed" if success else "failed"
        self.manifest["finished_at"] = datetime.now().isoformat()

        # manifest 업로드
        manifest_key = f"oliveyoung/_manifests/run_id={self.run_id}/manifest.json"
        data = json.dumps(self.manifest, ensure_ascii=False, indent=2).encode("utf-8")
        self.s3.put_object(
            Bucket=self.bucket,
            Key=manifest_key,
            Body=BytesIO(data),
            ContentType="application/json"
        )

        print(f"\n✅ Manifest 업로드: {manifest_key}")
        print(f"   총 {self.manifest['total_products']}개 상품, {len(self.manifest['parts'])}개 part 파일")

        return self.manifest


# 크롤링할 카테고리 구조 (메인카테고리 > 서브카테고리)
CATEGORIES = {
    "스킨케어": [
        "스킨/토너", "에센스/세럼/앰플", "크림", "로션","미스트/오일"
    ],
    "마스크팩": [
        "시트팩", "패드", "페이셜팩", "코팩", "패치"
    ],
    "클렌징": [
        "클렌징폼/젤", "오일/밤", "워터/밀크", "필링&스크럽"
    ],
    "더모 코스메틱": [
        "스킨케어", "바디케어", "클렌징", "선케어", "마스크팩"
    ],
    "맨즈케어": [
        "스킨케어"
    ]
}

# 담당자별 크롤링 카테고리 (총 20개 서브카테고리 / 4명)
PERSON_CATEGORIES = {
    "혁준": {
        "스킨케어": ["스킨/토너", "에센스/세럼/앰플", "크림", "로션", "미스트/오일"]
    },
    "지우": {
        "마스크팩": ["시트팩", "패드", "페이셜팩", "코팩", "패치"]
    },
    "서연": {
        "클렌징": ["클렌징폼/젤", "오일/밤", "워터/밀크", "필링&스크럽"],
        "맨즈케어": ["스킨케어"]
    },
    "재원": {
        "더모 코스메틱": ["스킨케어", "바디케어", "클렌징", "선케어", "마스크팩"]
    }
}

# 화장품 정보 키워드
COSMETIC_KEYWORDS = [
    "화장품법", "성분", "제조", "판매", "용량", "중량",
    "사용기한", "개봉", "사용방법", "제조국", "책임판매",
    "주의사항", "품질보증", "소비자상담"
]


class OliveYoungCrawler:
    BASE_URL = "https://www.oliveyoung.co.kr"
    CATEGORY_URL = "https://www.oliveyoung.co.kr/store/display/getMCategoryList.do"

    def __init__(self, headless=True, s3_bucket: Optional[str] = None):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        self.products = []

        # S3 업로더 (버킷 지정 시 활성화)
        if s3_bucket:
            self.s3_uploader = S3Uploader(bucket=s3_bucket)
        else:
            self.s3_uploader = None

        # 임시 저장 디렉토리 생성
        os.makedirs(TEMP_DIR, exist_ok=True)

    def _get_temp_file_path(self, main_cat: str, sub_cat: str) -> str:
        """서브카테고리별 임시 파일 경로 생성"""
        safe_main = main_cat.replace(" ", "_").replace("/", "-")
        safe_sub = sub_cat.replace(" ", "_").replace("/", "-")
        return os.path.join(TEMP_DIR, f"{safe_main}_{safe_sub}.json")

    def _save_products_to_temp(self, main_cat: str, sub_cat: str, products: list):
        """상품 리스트 전체를 임시 파일에 저장"""
        temp_path = self._get_temp_file_path(main_cat, sub_cat)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)

    def _load_temp_products(self, main_cat: str, sub_cat: str) -> list:
        """임시 파일에서 기존 크롤링 데이터 로드"""
        temp_path = self._get_temp_file_path(main_cat, sub_cat)
        if os.path.exists(temp_path):
            try:
                with open(temp_path, "r", encoding="utf-8") as f:
                    products = json.load(f)
                    print(f"    📂 임시파일에서 {len(products)}개 상품 복구")
                    return products
            except:
                pass
        return []

    def _get_crawled_urls(self, products: list) -> set:
        """이미 크롤링된 상품 URL 목록 반환"""
        return {p.get("url") for p in products if p.get("url")}

    def _clear_temp_file(self, main_cat: str, sub_cat: str):
        """서브카테고리 완료 후 임시 파일 삭제"""
        temp_path = self._get_temp_file_path(main_cat, sub_cat)
        if os.path.exists(temp_path):
            os.remove(temp_path)
            print(f"    🗑️  임시파일 삭제: {temp_path}")

    def start_browser(self):
        """브라우저 시작"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
            ]
        )
        self.context = self.browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        self.page = self.context.new_page()
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
        print("브라우저 시작 완료")

    def close_browser(self):
        """브라우저 종료"""
        if self.browser:
            self.browser.close()
            self.playwright.stop()
            print("브라우저 종료")

    def close_popups(self):
        """팝업 닫기"""
        try:
            popup_selectors = [
                "button.btnClose", ".popup-close", ".btn-close",
                "#layerClose", "button:has-text('닫기')", ".layer_close"
            ]
            for selector in popup_selectors:
                try:
                    popup = self.page.locator(selector).first
                    if popup.is_visible(timeout=500):
                        popup.click()
                        time.sleep(0.3)
                except Exception:
                    pass
        except Exception:
            pass

    def go_to_category_page(self):
        """카테고리 페이지로 이동"""
        print(f"\n카테고리 페이지로 이동...")
        self.page.goto(self.BASE_URL, wait_until="networkidle", timeout=60000)
        time.sleep(3)
        self.close_popups()

    def navigate_to_subcategory(self, main_cat, sub_cat):
        """서브카테고리 페이지로 이동"""
        print(f"\n  '{main_cat} > {sub_cat}' 페이지로 이동 중...")

        try:
            # 카테고리 버튼 클릭
            cat_btn = self.page.locator("button:has-text('카테고리'), a:has-text('카테고리')").first
            if cat_btn.is_visible(timeout=3000):
                cat_btn.click()
                time.sleep(1)

            # 메인 카테고리 호버/클릭
            main_link = self.page.locator(f"a:has-text('{main_cat}')").first
            if main_link.is_visible(timeout=3000):
                main_link.hover()
                time.sleep(0.5)

            # 서브 카테고리 클릭
            sub_link = self.page.locator(f"a:has-text('{sub_cat}')").first
            if sub_link.is_visible(timeout=3000):
                sub_link.click()
                time.sleep(3)
                self.close_popups()
                print(f"    페이지 이동 완료: {self.page.title()}")
                return True

        except Exception as e:
            print(f"    페이지 이동 실패: {e}")

        return False

    def get_total_pages(self):
        """총 페이지 수 확인 (끝 버튼 또는 >> 버튼 확인)"""
        import re
        try:
            # 1. "끝" 버튼에서 마지막 페이지 번호 추출
            end_selectors = [
                "a.last",
                "a:has-text('끝')",
                "a:has-text('맨끝')",
                "a[title*='마지막']",
                "a[title*='끝']",
            ]
            for selector in end_selectors:
                try:
                    end_btn = self.page.locator(selector).first
                    if end_btn.is_visible(timeout=500):
                        href = end_btn.get_attribute("href") or ""
                        match = re.search(r'pageIdx=(\d+)', href)
                        if match:
                            total = int(match.group(1))
                            print(f"    (끝 버튼에서 총 {total}페이지 확인)")
                            return total
                except:
                    continue

            # 2. ">>" 버튼이 있으면 10페이지 이상
            next_group_btn = self.page.locator("a:has-text('>>')").first
            if next_group_btn.is_visible(timeout=500):
                print(f"    (>> 버튼 발견, 전체 페이지 순회 모드)")
                return 999  # go_to_page에서 더 이상 못 갈 때까지 순회

            # 3. 현재 보이는 페이지 번호 중 최대값
            page_nums = self.page.locator(".pageing a, .paging a").all()
            max_page = 1
            for pn in page_nums:
                try:
                    text = pn.inner_text().strip()
                    if text.isdigit():
                        max_page = max(max_page, int(text))
                except:
                    pass
            return max_page
        except Exception:
            pass
        return 1

    def go_to_page(self, page_num):
        """특정 페이지로 이동 (10페이지 그룹 단위 지원)"""
        max_attempts = 10  # 무한루프 방지

        for _ in range(max_attempts):
            try:
                # 1. 현재 그룹에서 해당 페이지 번호 찾기
                page_link = self.page.locator(
                    f".pageing a:has-text('{page_num}'), .paging a:has-text('{page_num}')"
                ).first
                if page_link.is_visible(timeout=1000):
                    page_link.click()
                    time.sleep(2)
                    return True

                # 2. 해당 페이지가 없으면 >> 버튼으로 다음 그룹 이동
                next_group_selectors = [
                    "a:has-text('>>')",
                    ".pageing a.next",
                    ".paging a.next",
                    "a[class*='next']",
                    "a:has-text('다음')",
                ]

                clicked = False
                for selector in next_group_selectors:
                    try:
                        next_btn = self.page.locator(selector).first
                        if next_btn.is_visible(timeout=500):
                            next_btn.click()
                            time.sleep(2)
                            clicked = True
                            break
                    except:
                        continue

                if not clicked:
                    print(f"      페이지 {page_num}: 더 이상 이동 불가")
                    return False

            except Exception as e:
                print(f"      페이지 {page_num} 이동 실패: {e}")
                return False

        return False

    def get_product_urls_from_page(self):
        """현재 페이지에서 상품 URL 추출"""
        product_urls = []

        # 상품 링크 찾기
        selectors = [
            "a.prd_thumb[data-ref-goodsno]",
            "a[href*='getGoodsDetail']",
            ".prd_info a[href*='goodsNo']"
        ]

        for selector in selectors:
            links = self.page.locator(selector).all()
            if links:
                for link in links:
                    try:
                        href = link.get_attribute("href")
                        if href and "getGoodsDetail" in href:
                            if not href.startswith("http"):
                                href = self.BASE_URL + href
                            product_urls.append(href)
                    except:
                        pass
                break

        return list(dict.fromkeys(product_urls))

    def get_all_product_urls(self, main_cat, sub_cat):
        """모든 페이지에서 상품 URL 수집"""
        all_urls = []

        # 첫 페이지 URL 수집
        urls = self.get_product_urls_from_page()
        all_urls.extend(urls)
        print(f"    페이지 1: {len(urls)}개 상품")

        # 총 페이지 수 확인
        total_pages = self.get_total_pages()
        print(f"    총 {total_pages} 페이지")

        # 나머지 페이지 순회
        for page_num in range(2, total_pages + 1):
            if self.go_to_page(page_num):
                urls = self.get_product_urls_from_page()
                all_urls.extend(urls)
                print(f"    페이지 {page_num}: {len(urls)}개 상품")
            else:
                break

        all_urls = list(dict.fromkeys(all_urls))
        print(f"    총 {len(all_urls)}개 상품 URL 수집")
        return all_urls

    def get_product_detail(self, url, main_cat, sub_cat):
        """상품 상세 정보 크롤링"""
        product = {
            "url": url,
            "main_category": main_cat,
            "sub_category": sub_cat,
            "name": "",
            "brand": "",
            "price": "",
            "ingredients": "",
            "product_info": {},
            "crawled_at": datetime.now().isoformat(),
        }

        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            self.close_popups()

            # 상품명 (실제 요소에서 추출)
            try:
                name_selectors = [
                    ".prd_name",
                    "p.prd_name",
                    ".goods_name",
                    "h1.product-name",
                    "[class*='product'] [class*='name']",
                ]
                for selector in name_selectors:
                    try:
                        name_el = self.page.locator(selector).first
                        if name_el.is_visible(timeout=500):
                            product["name"] = name_el.inner_text().strip()
                            break
                    except:
                        continue
                # fallback: title에서 추출
                if not product["name"]:
                    title = self.page.title()
                    if "|" in title:
                        product["name"] = title.split("|")[0].strip()
            except:
                pass

            # 브랜드명
            try:
                brand_selectors = [
                    ".prd_brand",
                    "p.prd_brand",
                    ".brand_name",
                    "[class*='brand']",
                ]
                for selector in brand_selectors:
                    try:
                        brand_el = self.page.locator(selector).first
                        if brand_el.is_visible(timeout=500):
                            product["brand"] = brand_el.inner_text().strip()
                            break
                    except:
                        continue
            except:
                pass

            # 가격
            try:
                product["price"] = self.page.locator(".price").first.inner_text().strip()
            except:
                pass

            # 상품정보제공고시
            self.get_disclosure_info(product)

            if product["name"]:
                print(f"      ✓ {product['brand']} - {product['name'][:25]}...")

        except Exception as e:
            print(f"      ✗ 크롤링 실패: {str(e)[:40]}")

        return product

    def get_disclosure_info(self, product):
        """상품정보제공고시에서 화장품법 정보 가져오기"""
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1)

            try:
                btn = self.page.locator("button:has-text('상품정보 제공고시')").first
                if btn.is_visible(timeout=3000):
                    btn.click()
                    time.sleep(1.5)
            except:
                pass

            tables = self.page.locator("table").all()
            for table in tables:
                try:
                    rows = table.locator("tr").all()
                    for row in rows:
                        try:
                            th = row.locator("th").first
                            td = row.locator("td").first
                            key = th.inner_text().strip()
                            value = td.inner_text().strip()

                            if key and value and any(kw in key for kw in COSMETIC_KEYWORDS):
                                product["product_info"][key] = value
                                if "화장품법" in key and "성분" in key:
                                    product["ingredients"] = value
                        except:
                            continue
                except:
                    continue
        except:
            pass

    def crawl_subcategory(self, main_cat, sub_cat):
        """서브카테고리의 모든 상품 크롤링 (중간 저장 지원)"""
        print(f"\n{'='*60}")
        print(f"크롤링: {main_cat} > {sub_cat}")
        print(f"{'='*60}")

        # 기존 크롤링 데이터 로드 (재시작 지원)
        category_products = self._load_temp_products(main_cat, sub_cat)
        crawled_urls = self._get_crawled_urls(category_products)
        if crawled_urls:
            print(f"    ♻️  이전에 {len(crawled_urls)}개 상품 크롤링됨, 이어서 진행")

        # 메인 페이지에서 시작
        self.go_to_category_page()

        # 서브카테고리로 이동
        if not self.navigate_to_subcategory(main_cat, sub_cat):
            print(f"  '{sub_cat}' 카테고리를 찾을 수 없습니다.")
            return category_products  # 기존 데이터라도 반환

        # 모든 페이지에서 상품 URL 수집
        product_urls = self.get_all_product_urls(main_cat, sub_cat)

        if not product_urls:
            print(f"  상품이 없습니다.")
            return category_products

        # 이미 크롤링된 URL 제외
        remaining_urls = [url for url in product_urls if url not in crawled_urls]
        skipped = len(product_urls) - len(remaining_urls)
        if skipped > 0:
            print(f"    ⏭️  {skipped}개 상품 스킵 (이미 크롤링됨)")

        total = len(remaining_urls)

        # 각 상품 상세 정보 크롤링
        for i, url in enumerate(remaining_urls, 1):
            print(f"    [{i}/{total}] 크롤링 중...")
            product = self.get_product_detail(url, main_cat, sub_cat)
            if product["name"]:
                category_products.append(product)
                # 상품마다 로컬 임시파일에 저장
                self._save_products_to_temp(main_cat, sub_cat, category_products)
            time.sleep(0.3)

        print(f"\n✓ '{main_cat} > {sub_cat}' 완료: {len(category_products)}개 상품")
        return category_products

    def crawl_all_categories(self, target_categories: dict = None):
        """모든 카테고리 크롤링 (target_categories 지정 시 해당 카테고리만)"""
        all_products = []
        success = True

        # target_categories가 없으면 전체 CATEGORIES 사용
        categories_to_crawl = target_categories if target_categories else CATEGORIES

        self.start_browser()

        try:
            for main_cat, sub_cats in categories_to_crawl.items():
                for sub_cat in sub_cats:
                    products = self.crawl_subcategory(main_cat, sub_cat)
                    all_products.extend(products)

                    # S3 업로드 (활성화된 경우)
                    if self.s3_uploader and products:
                        self.s3_uploader.add_products(main_cat, sub_cat, products)
                        # 서브카테고리 완료 후 남은 버퍼도 flush (100개 미만 손실 방지)
                        self.s3_uploader.flush_category(main_cat, sub_cat)
                        # 체크포인트 저장 (중간에 끊겨도 manifest 보존)
                        self.s3_uploader.save_checkpoint()
                        # S3 업로드 성공 후 임시파일 삭제
                        self._clear_temp_file(main_cat, sub_cat)

                    # 로컬 중간 저장
                    self.save_to_json(all_products, "oliveyoung_temp.json")

        except Exception as e:
            print(f"크롤링 중 오류 발생: {e}")
            success = False
        finally:
            self.close_browser()
            # S3 manifest 업로드
            if self.s3_uploader:
                self.s3_uploader.finalize(success=success)

        self.products = all_products
        return all_products

    def save_to_json(self, products, filename):
        """JSON 파일로 저장"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False, indent=2)
        print(f"💾 JSON 저장: {filename} ({len(products)}개 상품)")

    def save_to_csv(self, products, filename):
        """CSV 파일로 저장"""
        if not products:
            print("저장할 상품이 없습니다.")
            return

        all_info_keys = set()
        for product in products:
            if product.get("product_info"):
                all_info_keys.update(product["product_info"].keys())

        base_headers = ["name", "brand", "main_category", "sub_category", "price", "ingredients", "url", "crawled_at"]
        info_headers = sorted(list(all_info_keys))

        with open(filename, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(base_headers + info_headers)

            for product in products:
                row = [
                    product.get("name", ""),
                    product.get("brand", ""),
                    product.get("main_category", ""),
                    product.get("sub_category", ""),
                    product.get("price", ""),
                    product.get("ingredients", ""),
                    product.get("url", ""),
                    product.get("crawled_at", ""),
                ]
                product_info = product.get("product_info", {})
                for key in info_headers:
                    row.append(product_info.get(key, ""))

                writer.writerow(row)

        print(f"💾 CSV 저장: {filename} ({len(products)}개 상품)")


def main():
    """메인 실행 함수"""
    import argparse

    parser = argparse.ArgumentParser(description="올리브영 화장품 크롤러")
    parser.add_argument("--s3-bucket", type=str, help="S3 버킷 이름 (없으면 로컬만 저장)")
    parser.add_argument("--headless", action="store_true", default=True, help="헤드리스 모드")
    parser.add_argument("--person", type=str, choices=["혁준", "지우", "서연", "재원"],
                        help="담당자 이름 (혁준/지우/서연/재원)")
    args = parser.parse_args()

    # 환경변수 또는 인자에서 버킷 이름 가져오기
    s3_bucket = args.s3_bucket or os.environ.get("S3_BUCKET")

    # 담당자별 카테고리 선택
    if args.person:
        target_categories = PERSON_CATEGORIES[args.person]
        print("=" * 60)
        print(f"올리브영 화장품 크롤러 - 담당자: {args.person}")
        print("=" * 60)
    else:
        target_categories = CATEGORIES
        print("=" * 60)
        print("올리브영 화장품 크롤러 - 전체 크롤링")
        print("=" * 60)

    total_subcats = sum(len(subs) for subs in target_categories.values())
    print(f"크롤링 대상: {len(target_categories)}개 메인 카테고리, {total_subcats}개 서브카테고리")

    for main_cat, sub_cats in target_categories.items():
        print(f"  {main_cat}: {', '.join(sub_cats)}")

    if s3_bucket:
        print(f"\nS3 버킷: {s3_bucket}")
    else:
        print(f"\nS3 미사용 (로컬 저장만)")

    print("=" * 60)

    crawler = OliveYoungCrawler(headless=args.headless, s3_bucket=s3_bucket)
    products = crawler.crawl_all_categories(target_categories=target_categories)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    crawler.save_to_json(products, f"oliveyoung_products_{timestamp}.json")
    crawler.save_to_csv(products, f"oliveyoung_products_{timestamp}.csv")

    print(f"\n{'='*60}")
    print(f"크롤링 완료! 총 {len(products)}개 상품 수집")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()

