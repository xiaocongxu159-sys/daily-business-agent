# -*- coding: utf-8 -*-
from __future__ import annotations

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


def test_frozen_runtime_verifier_writes_excel_html_and_json(tmp_path: Path) -> None:
    paths = verify_local_analysis_runtime(tmp_path)
    assert set(paths) == {"excel", "html", "json"}
    assert all(Path(path).is_file() for path in paths.values())
    assert (tmp_path / "verification-result.json").is_file()
