# -*- coding: utf-8 -*-
"""Run local Lingxing dashboard analysis in a killable child process."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from agent.job_store import JobStore
from agent.lingxing_dashboard_state import ACTIVE_STATUSES, set_dashboard_stage

DEFAULT_TIMEOUT_SECONDS = 600


def _atomic_context_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(
        json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), default=str) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _child_command(
    data_root: Path,
    job_id: str,
    manifest_path: Path,
    context_path: Path,
) -> list[str]:
    arguments = [
        "--run-lingxing-dashboard-job",
        "--data-root",
        str(data_root),
        "--job-id",
        job_id,
        "--manifest",
        str(manifest_path),
        "--dashboard-context",
        str(context_path),
    ]
    if getattr(sys, "frozen", False):
        return [sys.executable, *arguments]
    return [sys.executable, "-m", "agent.run_agent", *arguments]


def run_dashboard_job_isolated(
    job_store: JobStore,
    job_id: str,
    manifest_path: Path,
    dashboard_context: Mapping[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Start one child worker and fail closed on timeout or abnormal exit."""
    job_dir = job_store._job_dir(job_id)
    context_path = job_dir / "lingxing_dashboard_context.json"
    log_path = job_dir / "dashboard_worker.log"
    _atomic_context_write(context_path, dashboard_context)
    set_dashboard_stage(job_store, job_id, "starting_worker", status="queued")

    command = _child_command(job_store.data_root, job_id, manifest_path, context_path)
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment.pop("DAILY_BUSINESS_AGENT_CRASH_LOG", None)
    try:
        with log_path.open("w", encoding="utf-8", newline="\n") as log_handle:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path.cwd()),
                env=environment,
                timeout=max(30, int(timeout_seconds)),
                check=False,
            )
    except subprocess.TimeoutExpired:
        current = job_store.load_job(job_id)
        if current.get("status") in ACTIVE_STATUSES:
            set_dashboard_stage(
                job_store,
                job_id,
                "timed_out",
                status="failed",
                result=None,
                error="DashboardTimeout: 本机看板生成超过安全时限，已停止，可重新生成。",
                replace_result=True,
            )
        return
    except (OSError, ValueError):
        current = job_store.load_job(job_id)
        if current.get("status") in ACTIVE_STATUSES:
            set_dashboard_stage(
                job_store,
                job_id,
                "worker_start_failed",
                status="failed",
                result=None,
                error="DashboardWorkerUnavailable: 本机看板子进程无法启动。",
                replace_result=True,
            )
        return

    current = job_store.load_job(job_id)
    if completed.returncode == 0 and current.get("status") == "success":
        set_dashboard_stage(
            job_store,
            job_id,
            "completed",
            status="success",
            error=None,
        )
        return
    if current.get("status") == "failed":
        set_dashboard_stage(job_store, job_id, "failed", status="failed")
        return
    if current.get("status") in ACTIVE_STATUSES:
        set_dashboard_stage(
            job_store,
            job_id,
            "worker_incomplete",
            status="failed",
            result=None,
            error="DashboardWorkerFailed: 本机看板子进程异常结束。",
            replace_result=True,
        )


def run_dashboard_child(
    data_root: Path,
    job_id: str,
    manifest_path: Path,
    context_path: Path,
) -> int:
    """Child-process entrypoint used by source and frozen Windows runtimes."""
    from agent.lingxing_dashboard_bridge import execute_lingxing_dashboard_job

    context = json.loads(Path(context_path).read_text(encoding="utf-8"))
    job_store = JobStore(Path(data_root))
    execute_lingxing_dashboard_job(
        job_store,
        job_id,
        Path(manifest_path),
        context,
    )
    return 0 if job_store.load_job(job_id).get("status") == "success" else 1
