"""
체크포인트 전략
─────────────────────────────────────────────────────────
[Bronze 수집 체크포인트]

  재시작 단위 : 서브카테고리 내 '페이지'

  checkpoint.json 구조
  {
    "run_id": "20260225_093000",
    "completed_subcategories": [
        "스킨케어/스킨-토너",          ← S3 업로드까지 완전히 끝난 서브카테고리
        ...
    ],
    "page_progress": {
        "스킨케어/크림": {
            "completed_pages": [1, 2, 3, 5],   ← S3 업로드 성공한 페이지만 기록
            "total_pages":     16,
            "collected_urls":  ["url1", ...]    ← 해당 서브카테고리 전체 URL 캐시
        }
    }
  }

핵심 원칙
  - S3 업로드 성공 후에만 completed_pages 에 추가
  - 수집은 됐어도 업로드 실패한 페이지는 기록하지 않음
    → 재시작 시 해당 페이지만 재처리, 중복/누락 없음
─────────────────────────────────────────────────────────
"""

import json
import os
from datetime import datetime

from config.Settings import TEMP_DIR, RUN_ID

class CheckpointManager:

    def __init__(self, person: str | None = None, bucket: str | None = None):
        os.makedirs(TEMP_DIR, exist_ok=True)
        self.person = person or "default"
        self.CHECKPOINT_FILE = os.path.join(TEMP_DIR, f"checkpoint_{self.person}.json")
        self._state = self._load()
        if bucket:
            self._sync_from_s3_manifest(bucket, self._state["run_id"])
        self.save()

    # ------------------------------------------------------------------ #
    #  로드 / 저장
    # ------------------------------------------------------------------ #

    def _sync_from_s3_manifest(self, bucket: str, run_id: str) -> None:
        """S3 manifest에서 completed_subcategories를 merge한다. 실패는 무시."""
        try:
            import boto3
            from cosme_common import s3_paths

            s3_client = boto3.client("s3")
            key = s3_paths.manifest_key(run_id)
            response = s3_client.get_object(Bucket=bucket, Key=key)
            manifest = json.loads(response["Body"].read().decode("utf-8"))

            s3_completed = set(manifest.get("completed_subcategories", []))
            local_completed = set(self._state.get("completed_subcategories", []))
            merged = local_completed | s3_completed
            if len(merged) > len(local_completed):
                self._state["completed_subcategories"] = list(merged)
                print(f"  🔄 S3 manifest 동기화: {len(merged) - len(local_completed)}개 서브카테고리 추가")
        except Exception:
            pass  # S3 조회 실패는 무시하고 로컬 파일만 사용

    def _load(self) -> dict:
        """기존 체크포인트 파일이 있으면 자동으로 이어받고, 없으면 새로 생성"""
        if os.path.exists(self.CHECKPOINT_FILE):
            try:
                with open(self.CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                print(f"📂 체크포인트 복구: run_id={state['run_id']}")
                return state
            except Exception:
                pass

        # 없으면 RUN_ID 환경변수 사용 (Airflow 주입값, 없으면 타임스탬프)
        new_run_id = RUN_ID
        print(f"🆕 새 run_id 생성: {new_run_id}")
        return {
            "run_id":                  new_run_id,
            "created_at":              datetime.now().isoformat(),
            "completed_subcategories": [],
            "page_progress":           {},
        }

    def save(self):
        """현재 상태를 로컬 파일에 저장"""
        
        self._state["last_updated"] = datetime.now().isoformat()
        with open(self.CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)
    # ------------------------------------------------------------------ #
    #  서브카테고리 단위
    # ------------------------------------------------------------------ #

    def is_subcategory_done(self, main_cat: str, sub_cat: str) -> bool:
        key = f"{main_cat}/{sub_cat}"
        return key in self._state["completed_subcategories"]

    def mark_subcategory_done(self, main_cat: str, sub_cat: str):
        """서브카테고리 전체 완료 표시 (page_progress 정리 포함)"""
        
        key = f"{main_cat}/{sub_cat}"
        if key not in self._state["completed_subcategories"]:
            self._state["completed_subcategories"].append(key)
        
        # 완료된 서브카테고리의 page_progress 는 정리
        self._state["page_progress"].pop(key, None)
        self.save()
        
        print(f"  ✅ 체크포인트: '{key}' 완료 기록")

    # ------------------------------------------------------------------ #
    #  페이지 단위  
    # ------------------------------------------------------------------ #

    def get_completed_pages(self, main_cat: str, sub_cat: str) -> set[int]:
        """S3 업로드까지 완료된 페이지 번호 집합 반환"""
        
        key = f"{main_cat}/{sub_cat}"
        return set(self._state["page_progress"].get(key, {}).get("completed_pages", []))

    def mark_page_done(self, main_cat: str, sub_cat: str, page_num: int):
        """
        페이지 S3 업로드 완료 후 호출.
        반드시 업로드 성공 이후에만 호출해야 한다.
        """
        key = f"{main_cat}/{sub_cat}"
        progress = self._state["page_progress"].setdefault(key, {
            "completed_pages": [],
            "total_pages":      0,
        })
        if page_num not in progress["completed_pages"]:
            progress["completed_pages"].append(page_num)
        self.save()

    def set_total_pages(self, main_cat: str, sub_cat: str, total: int):
        key = f"{main_cat}/{sub_cat}"
        self._state["page_progress"].setdefault(key, {"completed_pages": []})
        self._state["page_progress"][key]["total_pages"] = total
        self.save()

    # ------------------------------------------------------------------ #
    #  URL 캐시 (전체 URL 수집 결과 저장)
    # ------------------------------------------------------------------ #

    def get_cached_urls(self, main_cat: str, sub_cat: str) -> list[str] | None:
        """저장된 URL 목록이 있으면 반환, 없으면 None"""
        key = f"{main_cat}/{sub_cat}"
        return self._state["page_progress"].get(key, {}).get("collected_urls")

    def set_cached_urls(self, main_cat: str, sub_cat: str, urls: list[str]):
        """URL 수집 완료 후 체크포인트에 캐시"""
        key = f"{main_cat}/{sub_cat}"
        self._state["page_progress"].setdefault(key, {"completed_pages": []})
        self._state["page_progress"][key]["collected_urls"] = urls
        self.save()
