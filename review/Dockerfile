FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 크롤러 코드 복사
COPY review_crawler.py .

# 출력 디렉토리 생성
RUN mkdir -p /app/output

# 환경 변수 설정
ENV PYTHONUNBUFFERED=1

# 기본 실행 명령어 (테스트 모드)
ENTRYPOINT ["python", "review_crawler.py"]
CMD ["--headless", "--test"]
