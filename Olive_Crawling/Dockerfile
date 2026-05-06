# Playwright 공식 이미지 사용 (브라우저 포함)
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사 (디렉토리 구조 전체)
COPY config/   ./config/
COPY crawler/  ./crawler/
COPY storage/  ./storage/
COPY Model/    ./Model/
COPY main.py   .

# 임시 저장 디렉토리 생성
RUN mkdir -p temp_crawl

# 환경변수
ENV S3_BUCKET=""
ENV RUN_ID=""
ENV AWS_DEFAULT_REGION="ap-northeast-2"

# 실행 (olivecrawler.py → main.py)
ENTRYPOINT ["python", "main.py"]
