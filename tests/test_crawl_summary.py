"""crawl_summary 순수 함수 테스트 — S3·브라우저 없이 가짜 manifest dict로 검증."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Olive_Crawling"))

from storage.crawl_summary import summarize_crawl  # noqa: E402

# 실제처럼 서브카테고리 이름에 '/'가 들어간 경우 포함
TARGETS = {
    "스킨케어": ["스킨/토너", "에센스/세럼/앰플", "크림"],
    "마스크팩": ["패드"],
}


def _cat(product_count=0, parts=0, expected=None):
    entry = {"product_count": product_count, "parts": [{"part_num": i} for i in range(parts)]}
    if expected is not None:
        entry["expected_urls"] = expected
    return entry


def test_정상_완료():
    manifest = {
        "total_products": 100,
        "completed_subcategories": ["스킨케어/스킨/토너", "스킨케어/에센스/세럼/앰플", "스킨케어/크림", "마스크팩/패드"],
        "categories": {
            "스킨케어/스킨/토너": _cat(40, 1, 40),
            "스킨케어/에센스/세럼/앰플": _cat(30, 1, 30),
            "스킨케어/크림": _cat(20, 1, 20),
            "마스크팩/패드": _cat(10, 1, 10),
        },
    }
    m = summarize_crawl(manifest, TARGETS)
    assert m == {
        "products_total": 100,
        "products_expected": 100,
        "products_coverage": 1.0,
        "categories_total": 4,
        "categories_completed": 4,
        "categories_failed": 0,
        "categories_partial": 0,
        "categories_zero": 0,
    }


def test_재개_형태_완료분은_0으로_세지_않음():
    # 9/25 형태: 이전 시도 완료분(manifest 누적) + 부분 수집 1 + 미시도 실패 1
    manifest = {
        "total_products": 4781,
        "completed_subcategories": ["스킨케어/스킨/토너", "스킨케어/크림"],
        "categories": {
            "스킨케어/스킨/토너": _cat(3000, 30, 3000),
            "스킨케어/크림": _cat(1521, 16, 1521),
            "스킨케어/에센스/세럼/앰플": _cat(260, 13, 917),
        },
    }
    m = summarize_crawl(manifest, TARGETS)
    assert m["products_total"] == 4781
    assert m["categories_completed"] == 2
    assert m["categories_failed"] == 2  # 에센스(부분) + 패드(기록 없음)
    assert m["categories_partial"] == 1
    assert m["categories_zero"] == 0


def test_일반_예외_실패도_실패로_셈():
    # 9/22 형태: 이동·예외 실패 카테고리는 manifest에 흔적이 없음
    manifest = {
        "total_products": 40,
        "completed_subcategories": ["스킨케어/스킨/토너"],
        "categories": {"스킨케어/스킨/토너": _cat(40, 1, 40)},
        "failed_subcategories": {"스킨케어/크림": "error: Timeout 30000ms exceeded."},
    }
    m = summarize_crawl(manifest, TARGETS)
    assert m["categories_completed"] == 1
    assert m["categories_failed"] == 3
    assert m["categories_zero"] == 0


def test_진짜_빈_카테고리():
    manifest = {
        "total_products": 40,
        "completed_subcategories": ["스킨케어/스킨/토너", "스킨케어/크림"],
        "categories": {
            "스킨케어/스킨/토너": _cat(40, 1, 40),
            "스킨케어/크림": _cat(0, 0, 5),            # 완료했지만 상품 0
            "마스크팩/패드": _cat(0, 0, 0),            # URL 목록이 비어 있음(미완료)
        },
    }
    m = summarize_crawl(manifest, TARGETS)
    assert m["categories_zero"] == 2
    assert m["categories_completed"] == 2
    assert m["categories_failed"] == 1  # 에센스만(빈 URL 카테고리는 실패로 세지 않음)


def test_구버전_manifest는_기대치_생략():
    manifest = {
        "total_products": 50,
        "completed_subcategories": ["스킨케어/스킨/토너"],
        "categories": {"스킨케어/스킨/토너": _cat(50, 1)},
    }
    m = summarize_crawl(manifest, TARGETS)
    assert "products_expected" not in m
    assert "products_coverage" not in m
    assert m["products_total"] == 50


def test_대상_밖_카테고리는_무시():
    manifest = {
        "total_products": 10,
        "completed_subcategories": ["폐지/카테고리"],
        "categories": {"폐지/카테고리": _cat(10, 1, 10)},
    }
    m = summarize_crawl(manifest, TARGETS)
    assert m["categories_completed"] == 0
    assert m["categories_failed"] == 4

