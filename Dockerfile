# Playwright 공식 이미지 사용 (브라우저 포함)
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright 브라우저 설치
RUN playwright install chromium

# 소스 코드 복사
COPY olivecrawler.py .

# 임시 저장 디렉토리 생성
RUN mkdir -p temp_crawl

# 환경변수 (S3 버킷은 실행 시 지정)
ENV S3_BUCKET=""
ENV AWS_DEFAULT_REGION="ap-northeast-2"

# 실행
ENTRYPOINT ["python", "olivecrawler.py"]
