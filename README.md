# 올리브영 화장품 크롤러

올리브영 베스트 화장품 정보를 크롤링하여 S3에 저장합니다.

## 폴더 구조

```
crawling/
├── items/                    # 화장품 데이터 크롤러 (상품 정보, 성분 등)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── olivecrawler.py
└── review/                   # 리뷰 데이터 크롤러 (피부타입, 자극도 등)
    ├── Dockerfile
    ├── requirements.txt
    └── review_crawler.py
```

## 담당자별 분담

| 담당자 | 메인 카테고리 | 서브카테고리 (총 20개) |
|--------|--------------|----------------------|
| **혁준** | 스킨케어 | 스킨/토너, 에센스/세럼/앰플, 크림, 로션, 미스트/오일 (5개) |
| **지우** | 마스크팩 | 시트팩, 패드, 페이셜팩, 코팩, 패치 (5개) |
| **서연** | 클렌징 + 맨즈케어 | 클렌징폼/젤, 오일/밤, 워터/밀크, 필링&스크럽, 스킨케어 (5개) |
| **재원** | 더모 코스메틱 | 스킨케어, 바디케어, 클렌징, 선케어, 마스크팩 (5개) |

## 실행 방법
### 1. Docker로 실행 <<권장>>

```bash
# 1. .env 파일 편집 (본인 정보 입력)
# PERSON=your_name
# S3_BUCKET=your-bucket-name
# AWS_ACCESS_KEY_ID=your-access-key
# AWS_SECRET_ACCESS_KEY=your-secret-key

# 2. 빌드 & 실행
docker-compose up --build
```

`.env` 파일 예시:
```env
PERSON=지우
S3_BUCKET=oliveyoung-crawl-data
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=ap-northeast-2
```


### 2. 로컬에서 담당자별로 실행

```bash
# 의존성 설치 (최초 1회)
pip install -r requirements.txt
playwright install chromium

# 본인 이름으로 실행
python olivecrawler.py --person 혁준 --s3-bucket oliveyoung-crawl-data
python olivecrawler.py --person 지우 --s3-bucket oliveyoung-crawl-data
python olivecrawler.py --person 서연 --s3-bucket oliveyoung-crawl-data
python olivecrawler.py --person 재원 --s3-bucket oliveyoung-crawl-data
```


### 3. 전체 크롤링 (--person 없이)

```bash
# 전체 카테고리 크롤링
python olivecrawler.py --s3-bucket your-bucket-name
```

## 크롤링 대상

| 메인 카테고리 | 서브 카테고리 |
|--------------|--------------|
| 스킨케어 | 스킨/토너, 에센스/세럼/앰플, 크림, 로션, 미스트/오일 |
| 마스크팩 | 시트팩, 패드, 페이셜팩, 코팩, 패치 |
| 클렌징 | 클렌징폼/젤, 오일/밤, 워터/밀크, 필링&스크럽 |
| 더모 코스메틱 | 스킨케어, 바디케어, 클렌징, 선케어, 마스크팩 |
| 맨즈케어 | 스킨케어 |

## S3 저장 구조

```
s3://bucket-name/
└── oliveyoung/
    ├── 스킨케어/
    │   └── 스킨-토너/
    │       └── run_id=20260225_093000/
    │           ├── part_0000.json (100개)
    │           ├── part_0001.json (100개)
    │           └── part_0002.json (나머지)
    └── _manifests/
        └── run_id=20260225_093000/
            └── manifest.json
```

## 기능

- 페이지네이션 자동 처리 (16페이지 이상 지원)
- 중간에 끊겨도 재시작 시 이어서 크롤링
- S3 업로드 실패 시 자동 재시도 (최대 3회)
- 서브카테고리별 체크포인트 저장

---

## 리뷰 크롤러 (review 폴더)

리뷰 통계 데이터 (피부타입, 피부고민, 자극도)를 수집합니다.

### 1. Docker Compose로 실행 (권장)

```bash
cd review

# 1. .env 파일 생성 (본인 이름 입력)
cp .env.example .env
# PERSON=지우  # 본인 이름으로 수정

# 2. 빌드 & 실행
docker-compose up --build
```

### 2. 로컬에서 담당자별로 실행

```bash
cd review

# 의존성 설치 (최초 1회)
pip install -r requirements.txt
playwright install chromium

# 본인 이름으로 실행
python review_crawler.py --person 혁준 --headless
python review_crawler.py --person 지우 --headless
python review_crawler.py --person 서연 --headless
python review_crawler.py --person 재원 --headless
```

### 3. 전체/테스트 크롤링

```bash
# 테스트 모드 (스킨케어 > 스킨/토너, 5개 상품)
python review_crawler.py --test

# 전체 크롤링 (카테고리당 10개 상품)
python review_crawler.py --headless --max-products 10

# 전체 크롤링 (모든 상품)
python review_crawler.py --headless
```

### 수집 데이터

| 항목 | 세부 데이터 |
|------|-----------|
| 피부타입 | 건성에 좋아요, 복합성에 좋아요, 지성에 좋아요 (%) |
| 피부고민 | 보습에 좋아요, 진정에 좋아요, 주름/미백에 좋아요 (%) |
| 자극도 | 자극없이 순해요, 보통이에요, 자극이 느껴져요 (%) |
| 기타 | 평점, 리뷰 수 |
