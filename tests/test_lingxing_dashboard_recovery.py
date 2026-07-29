# -*- coding: utf-8 -*-
"""Crash recovery and timeout tests for local Lingxing dashboards."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from agent.job_store import JobStore
from agent.lingxing_dashboard_state import (
    recover_interrupted_dashboard_jobs,
    set_dashboard_stage,
)
from agent.lingxing_dashboard_worker import (
    _child_command,
    run_dashboard_job_isolated,
)


def _dashboard_job(store: JobStore) -> dict:
    return store.create_job(
        label="synthetic dashboard",
        options={
            "write_excel": True,
            "write_html": True,
            "source": "lingxing_local_sync",
        },
    )


def test_startup_recovery_marks_old_active_jobs_retryable(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    queued = _dashboard_job(store)
    running = _dashboard_job(store)
    ordinary = store.create_job(label="ordinary")
    store.update_status(queued["job_id"], "queued")
    store.update_status(running["job_id"], "running")
    store.update_status(ordinary["job_id"], "running")

    assert recover_interrupted_dashboard_jobs(store) == 2
    for job_id in (queued["job_id"], running["job_id"]):
        recovered = store.load_job(job_id)
        assert recovered["status"] == "failed"
        assert recovered["dashboard_stage"] == "interrupted"
        assert recovered["error"].startswith("DashboardInterrupted:")
    assert store.load_job(ordinary["job_id"])["status"] == "running"

    replacement = _dashboard_job(store)
    assert replacement["status"] == "draft"


def test_isolated_worker_timeout_fails_closed_without_touching_inputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = JobStore(tmp_path)
    job = _dashboard_job(store)
    manifest = store._job_dir(job["job_id"]) / "job_manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 30))

    monkeypatch.setattr(subprocess, "run", timeout)
    run_dashboard_job_isolated(
        store,
        job["job_id"],
        manifest,
        {"metric_availability": {}},
        timeout_seconds=30,
    )

    failed = store.load_job(job["job_id"])
    assert failed["status"] == "failed"
    assert failed["dashboard_stage"] == "timed_out"
    assert failed["error"].startswith("DashboardTimeout:")
    assert manifest.read_text(encoding="utf-8") == "{}\n"


def test_isolated_worker_preserves_child_success_and_finalizes_stage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = JobStore(tmp_path)
    job = _dashboard_job(store)
    manifest = store._job_dir(job["job_id"]) / "job_manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    def succeed(*args, **kwargs):
        store.update_status(
            job["job_id"],
            "success",
            result={"row_counts": {"daily_sku": 12}},
            error=None,
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", succeed)
    run_dashboard_job_isolated(
        store,
        job["job_id"],
        manifest,
        {"metric_availability": {}},
        timeout_seconds=30,
    )

    completed = store.load_job(job["job_id"])
    assert completed["status"] == "success"
    assert completed["dashboard_stage"] == "completed"
    assert completed["result"]["row_counts"]["daily_sku"] == 12


def test_source_child_command_uses_real_agent_entrypoint(tmp_path: Path) -> None:
    command = _child_command(
        tmp_path,
        "20260729_deadbeef",
        tmp_path / "manifest.json",
        tmp_path / "context.json",
    )
    assert command[1:3] == ["-m", "agent.run_agent"]
    assert "--run-lingxing-dashboard-job" in command
    assert "--job-id" in command
