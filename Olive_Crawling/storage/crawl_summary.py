"""크롤 run 요약 — manifest(run 누적) 기준 DQ 지표와 run 소유자 판정.

boto3·브라우저 의존 없는 순수 함수만 둔다(로컬 단위 테스트 대상).
"""


def summarize_crawl(manifest: dict, target_categories: dict) -> dict:
    """manifest와 대상 카테고리로 crawl DQ 지표를 계산한다.

    프로세스 내부 카운터가 아니라 run 누적 기록을 쓰므로 재시도로 재개해도 값이 같다.
    - completed: 모든 묶음을 끝낸 카테고리
    - zero: URL 목록이 비었거나(expected_urls=0), 완료됐지만 상품 0개
    - failed: 완료도 아니고 빈 카테고리도 아님(이동 실패·예외·부분 수집·미시도)
    - partial: failed 중 part가 있는 카테고리
    """
    categories = manifest.get("categories", {})
    completed = set(manifest.get("completed_subcategories", []))
    targets = [f"{main}/{sub}" for main, subs in target_categories.items() for sub in subs]

    n_completed = n_failed = n_partial = n_zero = 0
    expected_total = 0
    has_expected = False

    for key in targets:
        entry = categories.get(key, {})
        expected = entry.get("expected_urls")
        if expected is not None:
            has_expected = True
            expected_total += expected

        is_completed = key in completed
        if expected == 0 or (is_completed and entry.get("product_count", 0) == 0):
            n_zero += 1
        if is_completed:
            n_completed += 1
        elif expected != 0:
            n_failed += 1
            if entry.get("parts"):
                n_partial += 1

    products_total = manifest.get("total_products", 0)
    metrics = {"products_total": products_total}
    # expected_urls가 없는 구버전 manifest면 기대치·수집률은 생략
    if has_expected:
        metrics["products_expected"] = expected_total
        if expected_total > 0:
            # 재개 시 부분 카테고리 중복으로 1을 넘을 수 있음
            metrics["products_coverage"] = round(products_total / expected_total, 4)
    metrics.update(
        categories_total=len(targets),
        categories_completed=n_completed,
        categories_failed=n_failed,
        categories_partial=n_partial,
        categories_zero=n_zero,
    )
    return metrics


def check_run_owner(manifest: dict | None, dag_run_id: str | None) -> tuple[bool, str]:
    """같은 run_id(날짜)의 manifest를 다른 dagRun이 이어 쓰지 못하게 판정한다.

    반환: (진행 가능 여부, 사유). 같은 dagRun의 재시도·Clear만 재개를 허용한다.
    """
    if not dag_run_id:
        return True, "판정 생략(Airflow 밖 실행)"
    if manifest is None:
        return True, "새 run"
    owner = manifest.get("owner_dag_run_id")
    if owner is None:
        return False, "소유 dagRun 기록이 없는 기존 manifest(구버전 또는 다른 실행)"
    if owner != dag_run_id:
        return False, f"다른 dagRun의 manifest: owner={owner}, current={dag_run_id}"
    return True, "같은 dagRun 재시도 — 재개"
