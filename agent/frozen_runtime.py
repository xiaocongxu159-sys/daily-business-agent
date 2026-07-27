# -*- coding: utf-8 -*-
"""Frozen-Windows runtime safeguards for local background analysis."""
from __future__ import annotations

import csv
import json
import queue
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable


_STOP = object()
_STALE_JOB_MESSAGE = (
    "Agent detected an unfinished task from a previous process. "
    "The uploaded input files were preserved; start a new analysis to continue."
)


class FrozenSafeThreadPoolExecutor:
    """Small single-worker executor with an explicitly started daemon thread.

    The public Agent only runs one local analysis at a time. This implementation
    keeps that contract while avoiding frozen-runtime worker dispatch failures.
    """

    def __init__(self, max_workers: int = 1, thread_name_prefix: str = ""):
        if max_workers != 1:
            raise ValueError("FrozenSafeThreadPoolExecutor supports exactly one worker")
        self._items: queue.Queue[Any] = queue.Queue()
        self._lock = threading.Lock()
        self._shutdown = False
        self._thread = threading.Thread(
            target=self._worker,
            name=thread_name_prefix or "daily-business-agent-worker",
            daemon=True,
        )
        self._thread.start()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        with self._lock:
            if self._shutdown:
                raise RuntimeError("cannot schedule new futures after shutdown")
            future: Future = Future()
            self._items.put((future, fn, args, kwargs))
            return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if cancel_futures:
                while True:
                    try:
                        item = self._items.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if item is not _STOP:
                            item[0].cancel()
                    finally:
                        self._items.task_done()
            self._items.put(_STOP)
        if wait:
            self._thread.join()

    def _worker(self) -> None:
        while True:
            item = self._items.get()
            try:
                if item is _STOP:
                    return
                future, fn, args, kwargs = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:  # Future must retain worker failures.
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._items.task_done()


def recover_stale_jobs(store: Any) -> int:
    """Make persisted unfinished jobs explicit while preserving all inputs."""
    recovered = 0
    for job in store.list_jobs():
        if job.get("status") not in {"queued", "running"}:
            continue
        try:
            store.update_status(
                job["job_id"],
                "failed",
                result=None,
                error=_STALE_JOB_MESSAGE,
            )
            recovered += 1
        except (FileNotFoundError, ValueError, OSError):
            continue
    return recovered


def install_frozen_runtime_patches() -> None:
    """Patch the packaged Agent before run_agent imports the integrated app."""
    import agent.app as app_module

    if getattr(app_module, "_frozen_runtime_patched", False):
        return

    original_create_app = app_module.create_app
    app_module.ThreadPoolExecutor = FrozenSafeThreadPoolExecutor

    def create_app_with_recovery(*args: Any, **kwargs: Any):
        app = original_create_app(*args, **kwargs)
        recover_stale_jobs(app.state.store)
        return app

    app_module.create_app = create_app_with_recovery
    app_module._frozen_runtime_patched = True


def _write_verification_progress(workspace: Path, stage: str) -> None:
    (workspace / "verification-progress.txt").write_text(stage + "\n", encoding="utf-8")


def verify_local_analysis_runtime(workspace: Path) -> dict[str, str]:
    """Generate synthetic inputs and prove the frozen engine writes all outputs."""
    workspace = Path(workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    _write_verification_progress(workspace, "start")

    _write_verification_progress(workspace, "importing-pandas")
    import pandas as pd

    _write_verification_progress(workspace, "importing-local-engine")
    from src.local_engine import LocalEngineRequest, run_local_engine

    input_dir = workspace / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    _write_verification_progress(workspace, "writing-mapping")
    mapping = input_dir / "mapping.csv"
    with mapping.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "shop_id",
                "shop_name",
                "marketplace",
                "seller-sku",
                "asin1",
                "item-name",
                "quantity",
            ]
        )
        writer.writerow(
            [
                "SYNTHETIC-STORE",
                "Synthetic Store",
                "US",
                "SYNTH-SKU-1",
                "B000TEST01",
                "Synthetic Product",
                10,
            ]
        )

    _write_verification_progress(workspace, "writing-erp-xlsx")
    erp = input_dir / "product-performance.xlsx"
    pd.DataFrame(
        [
            {
                "日期": "2026-07-27",
                "ASIN": "B000TEST01",
                "MSKU": "SYNTH-SKU-1",
                "标题": "Synthetic Product",
                "销售额": 39.98,
                "订单量": 2,
                "Sessions-Total": 10,
                "PV-Total": 12,
                "展示": 100,
                "点击": 10,
                "广告花费": 5.0,
                "广告销售额": 19.99,
                "广告订单量": 1,
            }
        ]
    ).to_excel(erp, sheet_name="sheet1", index=False)

    _write_verification_progress(workspace, "running-local-engine")
    result = run_local_engine(
        LocalEngineRequest(
            workspace=workspace,
            mapping_file=mapping,
            erp_files=(erp,),
            include_history=False,
            write_excel=True,
            write_html=True,
        )
    )
    _write_verification_progress(workspace, "local-engine-returned")

    paths = {
        "excel": str(Path(result.output_excel).resolve()),
        "html": str(Path(result.dashboard_html).resolve()),
        "json": str(Path(result.dashboard_json).resolve()),
    }
    if result.status != "success" or not all(Path(path).is_file() for path in paths.values()):
        raise RuntimeError("frozen local analysis did not create Excel, HTML and JSON")

    verification = workspace / "verification-result.json"
    verification.write_text(
        json.dumps({"status": result.status, **paths}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_verification_progress(workspace, "complete")
    return paths
