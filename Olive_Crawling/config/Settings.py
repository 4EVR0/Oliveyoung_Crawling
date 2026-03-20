import os


def _get_int_env(name: str, default: int) -> int:
    value = os.environ.get(name, str(default))
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except (TypeError, ValueError):
        pass
    return default


# 경로
TEMP_DIR = "temp_crawl"

# 크롤링
BASE_URL        = "https://www.oliveyoung.co.kr"
PAGE_TIMEOUT    = 30_000   # ms
NAV_TIMEOUT     = 60_000   # ms
CRAWL_DELAY     = 2.0      # 상품 상세 간 대기(초)
RETRY_COUNT     = 3        # 상품 상세 재시도 횟수

# S3
PART_SIZE       = 100      # part 파일당 최대 상품 수
S3_MAX_RETRIES  = 3        # S3 업로드 재시도 횟수

# 환경변수
S3_BUCKET       = os.environ.get("S3_BUCKET", "")
PERSON          = os.environ.get("PERSON", "")

# async 병렬 옵션
DETAIL_CONCURRENCY = _get_int_env("DETAIL_CONCURRENCY", 7)
PAGE_SIZE          = _get_int_env("PAGE_SIZE", 20)