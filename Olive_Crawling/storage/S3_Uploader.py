import json
import time
from io import BytesIO
from datetime import datetime
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from config.Settings import PART_SIZE, S3_MAX_RETRIES
from crawler.Utils import safe_name


class S3Uploader:
    """
    S3 업로드 전담 클래스.

    개선 포인트
    ─────────
    1. category/subcategory + run_id 기반 prefix
    2. PART_SIZE 단위 part 업로드
    3. 업로드 직후 manifest 즉시 저장 → resume 안정성 강화
    4. 같은 key 존재 시 overwrite 대신 다음 part 번호 탐색
    5. finalize / checkpoint 시 manifest 상태 갱신
    """

    def __init__(self, bucket: str, run_id: str):
        self.s3 = boto3.client("s3")
        self.bucket = bucket
        self.run_id = run_id
        self._buffer: dict[tuple, list] = {}

        self._manifest = {
            "run_id": run_id,
            "created_at": datetime.now().isoformat(),
            "last_checkpoint": None,
            "status": "in_progress",
            "categories": {},
            "total_products": 0,
            "parts": [],
        }

        self._load_existing_manifest()

    # ------------------------------------------------------------------ #
    # manifest 로드/저장
    # ------------------------------------------------------------------ #

    def _manifest_key(self) -> str:
        return f"oliveyoung/_manifests/run_id={self.run_id}/manifest.json"

    def _load_existing_manifest(self):
        """S3에서 기존 manifest.json을 찾아 상태를 복구한다."""
        key = self._manifest_key()
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            existing_data = json.loads(response["Body"].read().decode("utf-8"))

            # 기존 manifest 전체 복구
            self._manifest = existing_data
            self._manifest["status"] = "in_progress"
            self._manifest["last_checkpoint"] = datetime.now().isoformat()

            print(
                f"  🔄 S3 Manifest 복구 완료: "
                f"현재 총 {self._manifest.get('total_products', 0)}개 상품 수집됨"
            )
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound"):
                print("  🆕 신규 수집 시작 (기존 manifest 없음)")
            else:
                print(f"  ⚠️ Manifest 로드 중 오류 발생: {e}")
        except Exception as e:
            print(f"  ⚠️ 예기치 못한 Manifest 로드 오류: {e}")

    def _put_manifest(self):
        """manifest.json 을 S3 에 PUT 한다."""
        key = self._manifest_key()
        data = json.dumps(self._manifest, ensure_ascii=False, indent=2).encode("utf-8")

        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=BytesIO(data),
                ContentType="application/json",
            )
        except Exception as e:
            print(f"  ⚠️ manifest 저장 실패: {e}")
            raise

    # ------------------------------------------------------------------ #
    # S3 object 유틸
    # ------------------------------------------------------------------ #

    def _object_exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def _make_prefix(self, main_cat: str, sub_cat: str) -> str:
        return (
            f"oliveyoung/{safe_name(main_cat)}/{safe_name(sub_cat)}"
            f"/run_id={self.run_id}"
        )

    def _ensure_category_entry(self, cat_key: str):
        self._manifest["categories"].setdefault(
            cat_key,
            {
                "parts": [],
                "product_count": 0,
            },
        )

    def _next_part_number(self, main_cat: str, sub_cat: str, cat_key: str) -> int:
        """
        다음 part 번호를 결정한다.

        1. manifest 기준으로 시작
        2. 이미 같은 key가 S3에 있으면 overwrite 하지 않고 다음 번호 탐색
        """
        self._ensure_category_entry(cat_key)

        part_num = len(self._manifest["categories"][cat_key]["parts"])

        while True:
            candidate_key = (
                f"{self._make_prefix(main_cat, sub_cat)}/part_{part_num:04d}.json"
            )
            if not self._object_exists(candidate_key):
                return part_num
            part_num += 1

    # ------------------------------------------------------------------ #
    # 공개 인터페이스
    # ------------------------------------------------------------------ #

    def add_products(self, main_cat: str, sub_cat: str, products: list):
        """
        상품을 버퍼에 추가.
        버퍼가 PART_SIZE 를 넘으면 자동으로 upload_part 를 호출한다.
        """
        key = (main_cat, sub_cat)
        self._buffer.setdefault(key, []).extend(products)

        while len(self._buffer[key]) >= PART_SIZE:
            chunk = self._buffer[key][:PART_SIZE]
            self._buffer[key] = self._buffer[key][PART_SIZE:]
            self._upload_part(main_cat, sub_cat, chunk)

    def flush_subcategory(self, main_cat: str, sub_cat: str):
        """특정 서브카테고리 버퍼를 강제로 비워 업로드한다."""
        key = (main_cat, sub_cat)
        if self._buffer.get(key):
            self._upload_part(main_cat, sub_cat, self._buffer[key])
            self._buffer[key] = []

    def flush_all(self):
        """남은 모든 버퍼를 업로드한다."""
        for (main_cat, sub_cat), products in list(self._buffer.items()):
            if products:
                self._upload_part(main_cat, sub_cat, products)
        self._buffer.clear()

    def save_manifest_checkpoint(self):
        """진행 중 manifest 를 S3 에 저장한다."""
        self._manifest["status"] = "in_progress"
        self._manifest["last_checkpoint"] = datetime.now().isoformat()
        self._put_manifest()
        print(f"  💾 S3 manifest 체크포인트: {self._manifest['total_products']}개 상품")

    def finalize(self, success: bool = True):
        """
        크롤링 종료 시 남은 버퍼를 업로드하고 최종 manifest 저장.
        success=False 면 interrupted/failed 로 남긴다.
        """
        self.flush_all()

        self._manifest["status"] = "completed" if success else "interrupted"
        self._manifest["finished_at"] = datetime.now().isoformat()
        self._manifest["last_checkpoint"] = datetime.now().isoformat()
        self._put_manifest()

        print(
            f"\n✅ S3 finalize 완료 | "
            f"상태={self._manifest['status']} | "
            f"총 {self._manifest['total_products']}개 상품 | "
            f"{len(self._manifest['parts'])}개 part 파일"
        )
        return self._manifest

    # ------------------------------------------------------------------ #
    # 내부 구현
    # ------------------------------------------------------------------ #

    def _upload_part(self, main_cat: str, sub_cat: str, products: list):
        """
        part 파일을 S3 에 업로드한다.
        성공 후 즉시 manifest 를 S3 에도 저장한다.
        """
        if not products:
            return

        cat_key = f"{main_cat}/{sub_cat}"
        self._ensure_category_entry(cat_key)

        part_num = self._next_part_number(main_cat, sub_cat, cat_key)
        s3_key = f"{self._make_prefix(main_cat, sub_cat)}/part_{part_num:04d}.json"

        data = json.dumps(products, ensure_ascii=False, indent=2).encode("utf-8")

        for attempt in range(S3_MAX_RETRIES):
            try:
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=s3_key,
                    Body=BytesIO(data),
                    ContentType="application/json",
                )
                break
            except Exception as e:
                if attempt < S3_MAX_RETRIES - 1:
                    wait = 2 ** attempt
                    print(
                        f"  ⚠️ S3 업로드 실패 "
                        f"(시도 {attempt + 1}/{S3_MAX_RETRIES}): {e} → {wait}초 후 재시도"
                    )
                    time.sleep(wait)
                else:
                    print(f"  ❌ S3 업로드 최종 실패: {s3_key}")
                    raise

        # 업로드 성공 후 manifest 갱신
        part_info = {
            "key": s3_key,
            "part_num": part_num,
            "category": main_cat,
            "subcategory": sub_cat,
            "product_count": len(products),
            "uploaded_at": datetime.now().isoformat(),
        }

        self._manifest["categories"][cat_key]["parts"].append(part_info)
        self._manifest["categories"][cat_key]["product_count"] += len(products)
        self._manifest["total_products"] += len(products)
        self._manifest["parts"].append(part_info)
        self._manifest["last_checkpoint"] = datetime.now().isoformat()
        self._manifest["status"] = "in_progress"

        # 가장 중요: part 업로드 직후 manifest 즉시 저장
        self._put_manifest()

        print(f"  📤 S3 업로드: {s3_key} ({len(products)}개)")