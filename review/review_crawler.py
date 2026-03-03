"""
올리브영 리뷰 통계 크롤러 (Playwright 버전)
- 피부타입, 피부고민, 자극도 데이터 수집
- 카테고리별 상품 리뷰 통계 크롤링
"""

import time
import json
import csv
import os
import re
import boto3
from io import BytesIO
from datetime import datetime
from typing import Optional
from playwright.sync_api import sync_playwright

# 임시 저장 디렉토리
TEMP_DIR = "temp_review_crawl"


class S3Uploader:
    """
    리뷰 통계용 S3 업로더
    1. category/subcategory 기반 prefix
    2. run_id 필수 포함
    3. part 파일 단위 업로드
    4. manifest.json으로 실행 스냅샷 관리
    """

    PART_SIZE = 100

    def __init__(self, bucket: str, run_id: Optional[str] = None):
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.manifest = {
            "run_id": self.run_id,
            "created_at": datetime.now().isoformat(),
            "status": "in_progress",
            "categories": {},
            "total_reviews": 0,
            "parts": []
        }
        self._buffer = {}

    def _make_prefix(self, main_cat: str, sub_cat: str) -> str:
        main_safe = main_cat.replace(" ", "_").replace("/", "-")
        sub_safe = sub_cat.replace(" ", "_").replace("/", "-")
        return f"oliveyoung_review/{main_safe}/{sub_safe}/run_id={self.run_id}"

    def add_reviews(self, main_cat: str, sub_cat: str, reviews: list):
        key = (main_cat, sub_cat)
        if key not in self._buffer:
            self._buffer[key] = []
        self._buffer[key].extend(reviews)

        while len(self._buffer[key]) >= self.PART_SIZE:
            chunk = self._buffer[key][:self.PART_SIZE]
            self._buffer[key] = self._buffer[key][self.PART_SIZE:]
            self._upload_part(main_cat, sub_cat, chunk)

    def _upload_part(self, main_cat: str, sub_cat: str, reviews: list, max_retries: int = 3):
        prefix = self._make_prefix(main_cat, sub_cat)
        cat_key = f"{main_cat}/{sub_cat}"

        if cat_key not in self.manifest["categories"]:
            self.manifest["categories"][cat_key] = {"parts": [], "review_count": 0}

        part_num = len(self.manifest["categories"][cat_key]["parts"])
        part_key = f"{prefix}/part_{part_num:04d}.json"
        data = json.dumps(reviews, ensure_ascii=False, indent=2).encode("utf-8")

        for attempt in range(max_retries):
            try:
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=part_key,
                    Body=BytesIO(data),
                    ContentType="application/json"
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  ⚠️ S3 업로드 실패 (시도 {attempt + 1}/{max_retries}): {e}")
                    time.sleep(2 ** attempt)
                else:
                    print(f"  ❌ S3 업로드 최종 실패: {part_key}")
                    raise

        part_info = {
            "key": part_key,
            "review_count": len(reviews),
            "uploaded_at": datetime.now().isoformat()
        }
        self.manifest["categories"][cat_key]["parts"].append(part_info)
        self.manifest["categories"][cat_key]["review_count"] += len(reviews)
        self.manifest["total_reviews"] += len(reviews)
        self.manifest["parts"].append(part_info)

        print(f"  📤 S3 업로드: {part_key} ({len(reviews)}개 리뷰)")

    def flush_category(self, main_cat: str, sub_cat: str):
        key = (main_cat, sub_cat)
        if key in self._buffer and self._buffer[key]:
            self._upload_part(main_cat, sub_cat, self._buffer[key])
            self._buffer[key] = []
            print(f"  ✅ '{main_cat} > {sub_cat}' 버퍼 flush 완료")

    def save_checkpoint(self):
        self.manifest["last_checkpoint"] = datetime.now().isoformat()
        self.manifest["status"] = "in_progress"
        manifest_key = f"oliveyoung_review/_manifests/run_id={self.run_id}/manifest.json"
        data = json.dumps(self.manifest, ensure_ascii=False, indent=2).encode("utf-8")
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=manifest_key,
                Body=BytesIO(data),
                ContentType="application/json"
            )
            print(f"  💾 체크포인트 저장: {self.manifest['total_reviews']}개 리뷰")
        except Exception as e:
            print(f"  ⚠️ 체크포인트 저장 실패: {e}")

    def flush(self):
        for (main_cat, sub_cat), reviews in self._buffer.items():
            if reviews:
                self._upload_part(main_cat, sub_cat, reviews)
        self._buffer.clear()

    def finalize(self, success: bool = True):
        self.flush()
        self.manifest["status"] = "completed" if success else "failed"
        self.manifest["finished_at"] = datetime.now().isoformat()

        manifest_key = f"oliveyoung_review/_manifests/run_id={self.run_id}/manifest.json"
        data = json.dumps(self.manifest, ensure_ascii=False, indent=2).encode("utf-8")
        self.s3.put_object(
            Bucket=self.bucket,
            Key=manifest_key,
            Body=BytesIO(data),
            ContentType="application/json"
        )

        print(f"\n✅ Manifest 업로드: {manifest_key}")
        print(f"   총 {self.manifest['total_reviews']}개 리뷰, {len(self.manifest['parts'])}개 part 파일")
        return self.manifest


# 크롤링할 카테고리 구조
CATEGORIES = {
    "스킨케어": [
        "스킨/토너", "에센스/세럼/앰플", "크림", "로션", "미스트/오일"
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


class ReviewStatsCrawler:
    """올리브영 리뷰 통계 크롤러"""

    BASE_URL = "https://www.oliveyoung.co.kr"

    def __init__(self, headless=True, s3_bucket: Optional[str] = None):
        self.headless = headless
        self.browser = None
        self.context = None
        self.page = None
        self.products = []
        self.s3_uploader = S3Uploader(bucket=s3_bucket) if s3_bucket else None

        os.makedirs(TEMP_DIR, exist_ok=True)

    # ===== 임시 저장/복구 메서드 =====
    def _get_temp_file_path(self, main_cat: str, sub_cat: str) -> str:
        """서브카테고리별 임시 파일 경로 생성"""
        safe_main = main_cat.replace(" ", "_").replace("/", "-")
        safe_sub = sub_cat.replace(" ", "_").replace("/", "-")
        return os.path.join(TEMP_DIR, f"{safe_main}_{safe_sub}.json")

    def _get_url_file_path(self, main_cat: str, sub_cat: str) -> str:
        """URL 목록 파일 경로"""
        safe_main = main_cat.replace(" ", "_").replace("/", "-")
        safe_sub = sub_cat.replace(" ", "_").replace("/", "-")
        return os.path.join(TEMP_DIR, f"{safe_main}_{safe_sub}_urls.json")

    def _save_reviews_to_temp(self, main_cat: str, sub_cat: str, reviews: list):
        """리뷰 데이터 임시 파일에 저장"""
        temp_path = self._get_temp_file_path(main_cat, sub_cat)
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(reviews, f, ensure_ascii=False, indent=2)

    def _load_temp_reviews(self, main_cat: str, sub_cat: str) -> list:
        """임시 파일에서 기존 크롤링 데이터 로드"""
        temp_path = self._get_temp_file_path(main_cat, sub_cat)
        if os.path.exists(temp_path):
            try:
                with open(temp_path, "r", encoding="utf-8") as f:
                    reviews = json.load(f)
                    print(f"    📂 임시파일에서 {len(reviews)}개 리뷰 복구")
                    return reviews
            except:
                pass
        return []

    def _save_urls_to_temp(self, main_cat: str, sub_cat: str, urls: list):
        """URL 목록 임시 파일에 저장"""
        url_path = self._get_url_file_path(main_cat, sub_cat)
        with open(url_path, "w", encoding="utf-8") as f:
            json.dump(urls, f, ensure_ascii=False, indent=2)
        print(f"    💾 URL 목록 저장: {len(urls)}개")

    def _load_temp_urls(self, main_cat: str, sub_cat: str) -> list:
        """임시 파일에서 URL 목록 로드"""
        url_path = self._get_url_file_path(main_cat, sub_cat)
        if os.path.exists(url_path):
            try:
                with open(url_path, "r", encoding="utf-8") as f:
                    urls = json.load(f)
                    print(f"    📂 URL 목록 로드: {len(urls)}개")
                    return urls
            except:
                pass
        return []

    def _get_crawled_urls(self, reviews: list) -> set:
        """이미 크롤링된 상품 URL 목록 반환"""
        return {r.get("url") for r in reviews if r.get("url")}

    def _clear_temp_files(self, main_cat: str, sub_cat: str):
        """서브카테고리 완료 후 임시 파일 삭제"""
        for path in [self._get_temp_file_path(main_cat, sub_cat),
                     self._get_url_file_path(main_cat, sub_cat)]:
            if os.path.exists(path):
                os.remove(path)
        print(f"    🗑️  임시파일 삭제 완료")

    def start_browser(self):
        """브라우저 시작"""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
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
                except:
                    pass
        except:
            pass

    def get_review_stats(self, product_url):
        """
        상품 페이지에서 리뷰 통계 데이터 크롤링
        - 피부타입: 건성, 복합성, 지성
        - 피부고민: 보습, 진정, 주름/미백
        - 자극도: 자극없이 순해요, 보통이에요, 자극이 느껴져요
        """
        review_data = {
            "url": product_url,
            "name": "",
            "brand": "",
            "rating": "",
            "review_count": "",
            "피부타입": {
                "건성에 좋아요": "",
                "복합성에 좋아요": "",
                "지성에 좋아요": ""
            },
            "피부고민": {
                "보습에 좋아요": "",
                "진정에 좋아요": "",
                "주름/미백에 좋아요": ""
            },
            "자극도": {
                "자극없이 순해요": "",
                "보통이에요": "",
                "자극이 느껴져요": ""
            },
            "crawled_at": datetime.now().isoformat()
        }

        try:
            self.page.goto(product_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            self.close_popups()

            # 상품명 추출
            raw_title = self.page.title()
            if " | 올리브영" in raw_title:
                review_data["name"] = raw_title.rsplit(" | 올리브영", 1)[0].strip()
            else:
                review_data["name"] = raw_title.strip()

            # 브랜드 추출
            try:
                review_data["brand"] = self.page.locator("[class*='brand']").first.inner_text().strip()
            except:
                pass

            # 리뷰&셔터 탭 클릭
            try:
                review_tab = self.page.locator("button:has-text('리뷰&셔터')").first
                if review_tab.is_visible(timeout=3000):
                    review_tab.click()
                    time.sleep(3)
            except:
                pass

            # 평점 및 리뷰 수 추출 (ReviewArea에서)
            try:
                review_area = self.page.locator("[class*='ReviewArea']").first
                if review_area.is_visible(timeout=2000):
                    area_text = review_area.inner_text()
                    # 평점 추출
                    rating_match = re.search(r'(\d+\.?\d*)', area_text)
                    if rating_match:
                        review_data["rating"] = rating_match.group(1)
                    # 리뷰 수 추출
                    count_match = re.search(r'(\d[\d,]*)\s*건', area_text)
                    if count_match:
                        review_data["review_count"] = count_match.group(1).replace(',', '')
            except:
                pass

            # "자세히 보기" 버튼 클릭 (text 셀렉터 사용)
            detail_btn_clicked = False
            try:
                detail_btn = self.page.locator("text=자세히 보기").first
                if detail_btn.is_visible(timeout=3000):
                    detail_btn.click()
                    time.sleep(5)  # Shadow DOM 로딩 대기
                    detail_btn_clicked = True
            except:
                pass

            if detail_btn_clicked:
                # Shadow DOM에서 통계 데이터 추출
                self._extract_stats_from_shadow_dom(review_data)

                # 팝업 닫기 (Shadow DOM 내 닫기 버튼)
                try:
                    close_btn = self.page.locator("text=닫기").first
                    if close_btn.is_visible(timeout=1000):
                        close_btn.click()
                        time.sleep(0.5)
                except:
                    pass

            if review_data["name"]:
                stats_found = any([
                    review_data["피부타입"]["건성에 좋아요"],
                    review_data["피부고민"]["보습에 좋아요"],
                    review_data["자극도"]["자극없이 순해요"]
                ])
                status = "✓" if stats_found else "△ (리뷰통계 없음)"
                print(f"      {status} {review_data['brand']} - {review_data['name'][:25]}...")

            return review_data

        except Exception as e:
            print(f"      ✗ 크롤링 실패: {str(e)[:50]}")
            return review_data

    def _extract_stats_from_shadow_dom(self, review_data):
        """Shadow DOM에서 리뷰 통계 추출 (oy-review-modal-component)"""
        try:
            # JavaScript로 Shadow DOM 내부 텍스트 추출
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
                    if (modal) {
                        return getAllText(modal);
                    }
                    return '';
                }
            """)

            if not shadow_text:
                return

            # 공백 정규화
            text = re.sub(r'\s+', ' ', shadow_text)

            # 피부타입 추출
            patterns = [
                (r'건성에 좋아요\s*(\d+)\s*%', '피부타입', '건성에 좋아요'),
                (r'복합성에 좋아요\s*(\d+)\s*%', '피부타입', '복합성에 좋아요'),
                (r'지성에 좋아요\s*(\d+)\s*%', '피부타입', '지성에 좋아요'),
                (r'보습에 좋아요\s*(\d+)\s*%', '피부고민', '보습에 좋아요'),
                (r'진정에 좋아요\s*(\d+)\s*%', '피부고민', '진정에 좋아요'),
                (r'주름/미백에 좋아요\s*(\d+)\s*%', '피부고민', '주름/미백에 좋아요'),
                (r'자극없이 순해요\s*(\d+)\s*%', '자극도', '자극없이 순해요'),
                (r'보통이에요\s*(\d+)\s*%', '자극도', '보통이에요'),
                (r'자극이 느껴져요\s*(\d+)\s*%', '자극도', '자극이 느껴져요'),
            ]

            for pattern, category, key in patterns:
                match = re.search(pattern, text)
                if match:
                    review_data[category][key] = f"{match.group(1)}%"

        except Exception as e:
            print(f"      Shadow DOM 추출 실패: {e}")

    def _extract_stats_from_page(self, review_data):
        """페이지에서 직접 리뷰 통계 추출 (팝업 없이)"""
        try:
            page_text = self.page.inner_text("body")

            # 피부타입
            patterns = [
                (r'건성에 좋아요\s*\n*\s*(\d+)%', "건성에 좋아요", "피부타입"),
                (r'복합성에 좋아요\s*\n*\s*(\d+)%', "복합성에 좋아요", "피부타입"),
                (r'지성에 좋아요\s*\n*\s*(\d+)%', "지성에 좋아요", "피부타입"),
                (r'보습에 좋아요\s*\n*\s*(\d+)%', "보습에 좋아요", "피부고민"),
                (r'진정에 좋아요\s*\n*\s*(\d+)%', "진정에 좋아요", "피부고민"),
                (r'주름/미백에 좋아요\s*\n*\s*(\d+)%', "주름/미백에 좋아요", "피부고민"),
                (r'자극없이 순해요\s*\n*\s*(\d+)%', "자극없이 순해요", "자극도"),
                (r'보통이에요\s*\n*\s*(\d+)%', "보통이에요", "자극도"),
                (r'자극이 느껴져요\s*\n*\s*(\d+)%', "자극이 느껴져요", "자극도"),
            ]

            for pattern, key, category in patterns:
                match = re.search(pattern, page_text, re.DOTALL)
                if match:
                    review_data[category][key] = f"{match.group(1)}%"

        except Exception as e:
            print(f"      페이지 통계 추출 실패: {e}")

    def get_product_urls_from_page(self):
        """현재 페이지에서 상품 URL 추출"""
        product_urls = []

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

    def crawl_category(self, main_cat, sub_cat, max_products=None, progress_callback=None):
        """카테고리별 상품 리뷰 통계 크롤링 (중간 저장/복구 지원)"""
        print(f"\n{'='*60}")
        print(f"크롤링: {main_cat} > {sub_cat}")
        print(f"{'='*60}")

        # 1. 기존 크롤링 데이터 복구
        all_reviews = self._load_temp_reviews(main_cat, sub_cat)
        crawled_urls = self._get_crawled_urls(all_reviews)
        if crawled_urls:
            print(f"    ♻️  이전에 {len(crawled_urls)}개 리뷰 크롤링됨, 이어서 진행")

        # 2. URL 목록 확보 (기존 파일 있으면 로드, 없으면 수집)
        product_urls = self._load_temp_urls(main_cat, sub_cat)

        if not product_urls:
            # 카테고리 페이지로 이동해서 URL 수집
            self.page.goto(self.BASE_URL, wait_until="networkidle", timeout=60000)
            time.sleep(3)
            self.close_popups()

            # 카테고리 네비게이션
            try:
                cat_btn = self.page.locator("button:has-text('카테고리'), a:has-text('카테고리')").first
                if cat_btn.is_visible(timeout=3000):
                    cat_btn.click()
                    time.sleep(1)

                main_link = self.page.locator(f"a:has-text('{main_cat}')").first
                if main_link.is_visible(timeout=3000):
                    main_link.hover()
                    time.sleep(0.5)

                sub_link = self.page.locator(f"a:has-text('{sub_cat}')").first
                if sub_link.is_visible(timeout=3000):
                    sub_link.click()
                    time.sleep(3)
                    self.close_popups()
            except Exception as e:
                print(f"  카테고리 이동 실패: {e}")
                return all_reviews

            # 상품 URL 수집
            product_urls = self.get_product_urls_from_page()
            print(f"  발견된 상품: {len(product_urls)}개")

            # URL 목록 저장
            if product_urls:
                self._save_urls_to_temp(main_cat, sub_cat, product_urls)

        if max_products:
            product_urls = product_urls[:max_products]

        # 3. 이미 크롤링된 URL 제외
        remaining_urls = [url for url in product_urls if url not in crawled_urls]
        skipped = len(product_urls) - len(remaining_urls)
        if skipped > 0:
            print(f"    ⏭️  {skipped}개 상품 스킵 (이미 크롤링됨)")

        # 4. 각 상품의 리뷰 통계 수집
        total = len(remaining_urls)
        for i, url in enumerate(remaining_urls):
            print(f"    [{i+1}/{total}] 수집 중...")

            review_data = self.get_review_stats(url)
            review_data["main_category"] = main_cat
            review_data["sub_category"] = sub_cat

            all_reviews.append(review_data)

            # 매 상품마다 임시 저장
            self._save_reviews_to_temp(main_cat, sub_cat, all_reviews)

            if progress_callback:
                try:
                    progress_callback(all_reviews)
                except Exception as e:
                    print(f"    중간 저장 콜백 실패: {e}")
            time.sleep(2)

        print(f"\n✓ '{main_cat} > {sub_cat}' 완료: {len(all_reviews)}개 리뷰")
        return all_reviews

    def crawl_all_categories(self, target_categories=None, max_products_per_category=None):
        """모든 카테고리 크롤링 (중간 저장/복구 지원)"""
        all_reviews = []
        success = True
        categories_to_crawl = target_categories if target_categories else CATEGORIES

        self.start_browser()

        try:
            for main_cat, sub_cats in categories_to_crawl.items():
                for sub_cat in sub_cats:
                    try:
                        reviews = self.crawl_category(
                            main_cat,
                            sub_cat,
                            max_products_per_category,
                            progress_callback=lambda current_reviews: self.save_to_json(
                                all_reviews + current_reviews,
                                "review_stats_temp.json"
                            )
                        )
                        all_reviews.extend(reviews)

                        # 전체 중간 저장
                        self.save_to_json(all_reviews, "review_stats_temp.json")

                        # S3 업로드 (카테고리 단위)
                        if self.s3_uploader:
                            self.s3_uploader.add_reviews(main_cat, sub_cat, reviews)
                            self.s3_uploader.flush_category(main_cat, sub_cat)
                            self.s3_uploader.save_checkpoint()

                        # 서브카테고리 완료 후 임시 파일 삭제
                        self._clear_temp_files(main_cat, sub_cat)

                    except Exception as e:
                        print(f"  ❌ '{main_cat} > {sub_cat}' 오류: {e}")
                        # 오류 발생해도 현재까지 데이터는 보존
                        self.save_to_json(all_reviews, "review_stats_temp.json")
                        success = False
                        continue
        finally:
            self.close_browser()

        if self.s3_uploader:
            self.s3_uploader.finalize(success=success)

        self.products = all_reviews
        return all_reviews

    def save_to_json(self, data, filename):
        """JSON 저장"""
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"JSON 저장: {filename} ({len(data)}개)")

    def save_to_csv(self, data, filename):
        """CSV 저장"""
        if not data:
            print("저장할 데이터가 없습니다.")
            return

        headers = [
            "name", "brand", "main_category", "sub_category",
            "rating", "review_count",
            "피부타입_건성", "피부타입_복합성", "피부타입_지성",
            "피부고민_보습", "피부고민_진정", "피부고민_주름미백",
            "자극도_순함", "자극도_보통", "자극도_자극",
            "url", "crawled_at"
        ]

        with open(filename, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

            for item in data:
                row = [
                    item.get("name", ""),
                    item.get("brand", ""),
                    item.get("main_category", ""),
                    item.get("sub_category", ""),
                    item.get("rating", ""),
                    item.get("review_count", ""),
                    item.get("피부타입", {}).get("건성에 좋아요", ""),
                    item.get("피부타입", {}).get("복합성에 좋아요", ""),
                    item.get("피부타입", {}).get("지성에 좋아요", ""),
                    item.get("피부고민", {}).get("보습에 좋아요", ""),
                    item.get("피부고민", {}).get("진정에 좋아요", ""),
                    item.get("피부고민", {}).get("주름/미백에 좋아요", ""),
                    item.get("자극도", {}).get("자극없이 순해요", ""),
                    item.get("자극도", {}).get("보통이에요", ""),
                    item.get("자극도", {}).get("자극이 느껴져요", ""),
                    item.get("url", ""),
                    item.get("crawled_at", "")
                ]
                writer.writerow(row)

        print(f"CSV 저장: {filename} ({len(data)}개)")


def main():
    """메인 실행"""
    import argparse

    parser = argparse.ArgumentParser(description="올리브영 리뷰 통계 크롤러")
    parser.add_argument("--s3-bucket", type=str, help="S3 버킷 이름 (없으면 로컬만 저장)")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--max-products", type=int, default=None,
                        help="카테고리당 최대 상품 수")
    parser.add_argument("--test", action="store_true",
                        help="테스트 모드 (스킨케어 > 스킨/토너만 크롤링)")
    parser.add_argument("--person", type=str, choices=["혁준", "지우", "서연", "재원"],
                        help="담당자 이름 (혁준/지우/서연/재원)")
    args = parser.parse_args()
    s3_bucket = (
        args.s3_bucket
        or os.environ.get("REVIEW_S3_BUCKET")
        or os.environ.get("S3_BUCKET")
        or "oliveyoung-review"
    )

    # 담당자별 카테고리 선택
    if args.person:
        target_categories = PERSON_CATEGORIES[args.person]
        print("=" * 60)
        print(f"올리브영 리뷰 통계 크롤러 - 담당자: {args.person}")
        print("=" * 60)
    elif args.test:
        target_categories = {"스킨케어": ["스킨/토너"]}
        print("=" * 60)
        print("올리브영 리뷰 통계 크롤러 - 테스트 모드")
        print("=" * 60)
    else:
        target_categories = CATEGORIES
        print("=" * 60)
        print("올리브영 리뷰 통계 크롤러 - 전체 크롤링")
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

    crawler = ReviewStatsCrawler(headless=args.headless, s3_bucket=s3_bucket)

    if args.test:
        reviews = crawler.crawl_all_categories(target_categories, max_products_per_category=5)
    else:
        reviews = crawler.crawl_all_categories(target_categories, max_products_per_category=args.max_products)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    crawler.save_to_json(reviews, f"review_stats_{timestamp}.json")
    crawler.save_to_csv(reviews, f"review_stats_{timestamp}.csv")

    print(f"\n{'='*60}")
    print(f"완료! 총 {len(reviews)}개 상품 리뷰 통계 수집")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
