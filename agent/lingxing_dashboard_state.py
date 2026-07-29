# -*- coding: utf-8 -*-
"""Crash-safe state helpers for local Lingxing dashboard jobs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent.job_store import JobStore

SOURCE_KEY = "lingxing_local_sync"
ACTIVE_STATUSES = {"queued", "running"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_dashboard_stage(
    job_store: JobStore,
    job_id: str,
    stage: str,
    *,
    status: str | None = None,
    result: Any = None,
    error: str | None = None,
    replace_result: bool = False,
) -> dict:
    """Persist one privacy-safe stage without exposing business payloads."""
    job = job_store.load_job(job_id)
    if status is not None:
        job["status"] = status
    if replace_result:
        job["result"] = result
    if error is not None or status in {"success", "failed"}:
        job["error"] = error
    job["dashboard_stage"] = str(stage or "unknown")[:64]
    job["stage_updated_at"] = _utc_now()
    return job_store.save_job(job)


def recover_interrupted_dashboard_jobs(job_store: JobStore) -> int:
    """Fail active jobs left by a previous Agent process.

    Dashboard workers are child processes of the Agent. None can survive a
    clean installer stop or an Agent restart, so an active persisted status at
    startup is necessarily interrupted and may be retried safely.
    """
    recovered = 0
    for job in job_store.list_jobs():
        options = job.get("options") or {}
        if options.get("source") != SOURCE_KEY or job.get("status") not in ACTIVE_STATUSES:
            continue
        set_dashboard_stage(
            job_store,
            job["job_id"],
            "interrupted",
            status="failed",
            result=None,
            error="DashboardInterrupted: 上次看板任务已中断，可安全重新生成。",
            replace_result=True,
        )
        recovered += 1
    return recovered
