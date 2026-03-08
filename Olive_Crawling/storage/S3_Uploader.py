import json
import time
from io import BytesIO
from datetime import datetime
from typing import Optional

import boto3

from config.Settings import PART_SIZE, S3_MAX_RETRIES
from crawler.Utils import safe_name


class S3Uploader:
    """
    S3 업로드 전담 클래스.

    설계 원칙
    ─────────
    1. category/subcategory + run_id 기반 prefix
    2. PART_SIZE(100) 단위로 묶어서 part 파일 업로드
    3. 업로드 실패 시 지수 백오프 재시도 (최대 S3_MAX_RETRIES)
    4. manifest.json 으로 스냅샷 완성도 보장
    """

    def __init__(self, bucket: str, run_id: str):
        self.s3      = boto3.client("s3")
        self.bucket  = bucket
        self.run_id  = run_id
        self._buffer: dict[tuple, list] = {}  # {(main_cat, sub_cat): [products]}
        self._manifest = {
            "run_id":         run_id,
            "created_at":     datetime.now().isoformat(),
            "status":         "in_progress",
            "categories":     {},
            "total_products": 0,
            "parts":          [],
        }

    # ------------------------------------------------------------------ #
    #  공개 인터페이스
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
        """진행 중 manifest 를 S3 에 저장한다 (체크포인트 역할)."""
        self._manifest["last_checkpoint"] = datetime.now().isoformat()
        self._put_manifest()
        print(f"  💾 S3 manifest 체크포인트: {self._manifest['total_products']}개 상품")

    def finalize(self, success: bool = True):
        """
        크롤링 완료 후 최종 manifest 를 S3 에 업로드한다.
        finalize 전에 flush_all 이 호출된다.
        """
        self.flush_all()
        self._manifest["status"]      = "completed" if success else "failed"
        self._manifest["finished_at"] = datetime.now().isoformat()
        self._put_manifest()
        print(
            f"\n✅ S3 finalize 완료 | "
            f"총 {self._manifest['total_products']}개 상품 | "
            f"{len(self._manifest['parts'])}개 part 파일"
        )
        return self._manifest

    # ------------------------------------------------------------------ #
    #  내부 구현
    # ------------------------------------------------------------------ #

    def _make_prefix(self, main_cat: str, sub_cat: str) -> str:
        return (
            f"oliveyoung/{safe_name(main_cat)}/{safe_name(sub_cat)}"
            f"/run_id={self.run_id}"
        )

    def _upload_part(self, main_cat: str, sub_cat: str, products: list):
        """
        part 파일을 S3 에 업로드한다.
        성공 후에만 manifest 를 갱신한다.
        """
        cat_key = f"{main_cat}/{sub_cat}"
        self._manifest["categories"].setdefault(
            cat_key, {"parts": [], "product_count": 0}
        )
        part_num = len(self._manifest["categories"][cat_key]["parts"])
        s3_key   = f"{self._make_prefix(main_cat, sub_cat)}/part_{part_num:04d}.json"

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
                    print(f"  ⚠️ S3 업로드 실패 (시도 {attempt+1}/{S3_MAX_RETRIES}): {e} → {wait}초 후 재시도")
                    time.sleep(wait)
                else:
                    print(f"  ❌ S3 업로드 최종 실패: {s3_key}")
                    raise

        # 업로드 성공 후에만 manifest 갱신
        part_info = {
            "key":           s3_key,
            "product_count": len(products),
            "uploaded_at":   datetime.now().isoformat(),
        }
        self._manifest["categories"][cat_key]["parts"].append(part_info)
        self._manifest["categories"][cat_key]["product_count"] += len(products)
        self._manifest["total_products"]                        += len(products)
        self._manifest["parts"].append(part_info)
        print(f"  📤 S3 업로드: {s3_key} ({len(products)}개)")

    def _put_manifest(self):
        """manifest.json 을 S3 에 PUT 한다."""
        key  = f"oliveyoung/_manifests/run_id={self.run_id}/manifest.json"
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
