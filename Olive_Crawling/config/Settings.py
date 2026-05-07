import os

from oliveyoung_common.batch import build_batch_id

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
S3_BUCKET = os.environ.get("S3_BUCKET", "")
RUN_ID    = os.environ.get("RUN_ID", build_batch_id())

# async 병렬 옵션
DETAIL_CONCURRENCY = int(os.environ.get("DETAIL_CONCURRENCY", "5"))
PAGE_SIZE          = int(os.environ.get("PAGE_SIZE", "20"))
