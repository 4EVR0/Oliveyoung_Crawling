# 🛍️ 프로젝트 이름

> 한 줄 소개 : 올리브영 화장품 데이터 기반 GraphRAG 추천 시스템

---

## 📌 프로젝트 소개

<!-- 이게 뭔지, 왜 만들었는지 -->

**어떤 프로젝트인가요?**
올리브영 베스트 카테고리의 화장품 정보(상품명, 가격, 브랜드, 순위 등)를 자동으로 수집합니다.

**왜 만들었나요?**
- 수작업으로 수집하던 화장품 트렌드 데이터를 자동화하기 위해
- 수집된 데이터를 S3에 적재하여 이후 분석 파이프라인에 활용하기 위해

---

## 🛠️ 기술 스택

| 분류 | 사용 기술 |
|------|----------|
| 크롤링 | Playwright |
| 언어 | Python 3.12 |
| 컴퓨팅 서버 | AWS EC2 |
| 스토리지 | AWS S3 (boto3) |
| 테이블 포맷 | Apache Iceberg |
| 메타데이터 서버 | --- |
| 컨테이너 | Docker, Docker Compose |

---

## 🏗️ 아키텍처

```
[올리브영 웹사이트]
        ↓  Playwright로 크롤링
[olivecrawler.py]
        ↓  JSON 변환
[AWS S3]
└── oliveyoung/
    └── {카테고리}/
        └── {서브카테고리}/
            └── run_id={날짜}/
                └── part_XXXX.json
```

---

## ⚙️ 설치 및 실행

### 요구사항

- Python 3.12 (3.13 미지원 — greenlet 호환성 문제)
- Docker 
- AWS 계정 및 S3 버킷

### 빠른 시작 (Docker 권장)

```bash
# 1. 레포지토리 클론
git clone https://github.com/your-org/your-repo.git
cd your-repo

# 2. .env 파일 설정
cp .env.example .env
# .env 파일을 열어 본인 정보 입력

# 3. 실행
docker-compose up --build
```

### 로컬 실행

```bash
pip install -r requirements.txt
playwright install chromium

python olivecrawler.py --person 재원 --s3-bucket your-bucket-name
```

> 자세한 실행 방법은 [RUNBOOK.md](./docs/RUNBOOK.md)를 참고하세요.

---

## 🤝 기여 방법

1. 이 레포지토리를 Fork 합니다
2. 새 브랜치를 생성합니다 (`git checkout -b feat/기능명`)
3. 변경사항을 커밋합니다 (`git commit -m "feat: 기능 설명"`)
4. 브랜치에 Push 합니다 (`git push origin feat/기능명`)
5. Pull Request를 생성합니다

---

## 📄 라이센스

```
MIT License — 자유롭게 사용, 수정, 배포 가능
```

---

## 👥 팀원

| 이름 | 역할 |
|------|------|
| 혁준 | 팀장, 스킨케어 크롤링 |
| 지우 | 마스크팩 크롤링 |
| 서연 | 클렌징 / 맨즈케어 크롤링 |
| 재원 | 더모 코스메틱 크롤링 |