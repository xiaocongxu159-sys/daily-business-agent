# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from agent.frozen_runtime import (
    FrozenSafeThreadPoolExecutor,
    recover_stale_jobs,
    verify_local_analysis_runtime,
)
from agent.job_store import JobStore


def test_frozen_safe_executor_runs_submitted_work() -> None:
    executor = FrozenSafeThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="test-frozen-worker",
    )
    try:
        future = executor.submit(lambda left, right: left + right, 2, 3)
        assert future.result(timeout=3) == 5
    finally:
        executor.shutdown(wait=True)


def test_recover_stale_jobs_marks_failed_and_preserves_inputs(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create_job(label="stale")
    input_path = store.category_dir(job["job_id"], "mapping") / "mapping.csv"
    input_path.write_text("shop_id,seller-sku\nS,SKU\n", encoding="utf-8")
    store.refresh_files(store.load_job(job["job_id"]))
    store.update_status(job["job_id"], "queued", result=None, error=None)

    assert recover_stale_jobs(store) == 1
    recovered = store.load_job(job["job_id"])
    assert recovered["status"] == "failed"
    assert "input files were preserved" in recovered["error"]
    assert input_path.is_file()


def test_frozen_runtime_verifier_writes_outputs_and_preserves_metrics(tmp_path: Path) -> None:
    paths = verify_local_analysis_runtime(tmp_path)
    assert set(paths) == {"excel", "html", "json"}
    assert all(Path(path).is_file() for path in paths.values())

    verification_path = tmp_path / "verification-result.json"
    assert verification_path.is_file()
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification["totals"] == {
        "rows": 1,
        "sales": 39.98,
        "orders": 2.0,
        "sessions": 10.0,
        "page_views": 12.0,
        "ad_spend": 5.0,
        "ad_sales": 19.99,
    }
