# -*- coding: utf-8 -*-
"""Launcher for the Windows local Agent."""
from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from agent.lingxing_controlled_integration import create_integrated_app
from agent.security import load_or_create_agent_token
from agent.settings import AgentSettings, default_data_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily Business Windows Local Agent")
    parser.add_argument("connection_package", nargs="?", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-root", type=Path, default=default_data_root())
    parser.add_argument("--allow-origin", action="append", default=[])
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--verify-sdk-runtime",
        action="store_true",
        help="Verify frozen Lingxing SDK imports and exit without reading user data.",
    )
    parser.add_argument(
        "--verify-probe-runtime",
        action="store_true",
        help="Verify frozen privacy-safe probe and local sync stores without reading user data.",
    )
    parser.add_argument(
        "--run-lingxing-dashboard-job",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--job-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--dashboard-context", type=Path, help=argparse.SUPPRESS)
    return parser


def verify_sdk_runtime() -> None:
    """Fail closed when the packaged executable is missing an SDK dependency."""
    try:
        from aiohttp_socks import ProxyConnector
        from lingxingapi import API
    except ImportError as exc:
        missing = str(getattr(exc, "name", "") or type(exc).__name__)
        raise RuntimeError(f"Lingxing SDK runtime import failed: {missing}") from exc
    if API is None or ProxyConnector is None:
        raise RuntimeError("Lingxing SDK runtime import returned an invalid object")


def verify_probe_runtime() -> None:
    """Exercise synthetic probe, sync stores and isolated dashboard worker."""
    from agent.frozen_dashboard_verification import verify_frozen_dashboard_worker
    from agent.lingxing_available_sync import (
        AVAILABLE_DATASETS,
        UNAVAILABLE_DATASETS,
        AvailableLingxingSyncService,
    )
    from agent.lingxing_probe import LingxingProbeResultStore
    from agent.lingxing_sync_foundation import LingxingDatasetStore

    with tempfile.TemporaryDirectory(prefix="daily-agent-probe-runtime-") as temp:
        root = Path(temp)
        probe_store = LingxingProbeResultStore(root)
        saved = probe_store.save(
            {
                "status": "success",
                "datasets": [
                    {
                        "dataset": "orders",
                        "status": "no_data",
                        "fields": ["amazon_order_id", "purchase_date_loc"],
                        "sampled_rows": 0,
                        "response_count": 0,
                        "total_count": 0,
                        "date_from": "2026-07-27",
                        "date_to": "2026-07-27",
                        "error_code": "",
                    }
                ],
                "message_code": "probe_completed",
            }
        )
        loaded = probe_store.load()
        if saved != loaded or loaded["datasets"][0]["dataset"] != "orders":
            raise RuntimeError("frozen Lingxing probe runtime validation failed")
        content = probe_store.path.read_text(encoding="utf-8")
        if "amazon_order_id" not in content or "app_secret" in content:
            raise RuntimeError("frozen Lingxing probe privacy contract failed")

        dataset_store = LingxingDatasetStore(root)
        snapshot = dataset_store.commit(
            "orders",
            [
                {
                    "sid": 101,
                    "amazon_order_id": "SYNTHETIC-ORDER-1",
                    "order_status": "Shipped",
                    "asin": "B0SYNTHETIC",
                    "msku": "MSKU-1",
                    "lsku": "LSKU-1",
                    "order_qty": 1,
                    "sales_amt": 10.0,
                    "currency_code": "USD",
                    "purchase_time_utc": "2026-07-27T01:00:00+00:00",
                    "purchase_date_loc": "2026-07-26",
                    "update_time_ts": 1785114000,
                }
            ],
            checkpoint={"date_from": "2026-07-26", "date_to": "2026-07-27"},
        )
        reloaded = dataset_store.load("orders")
        if reloaded is None or reloaded.generation != snapshot.generation:
            raise RuntimeError("frozen Lingxing dataset generation validation failed")
        status = dataset_store.load_status("orders")
        if status.get("status") != "success" or len(reloaded.rows) != 1:
            raise RuntimeError("frozen Lingxing dataset status validation failed")
        dataset_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in dataset_store.root.rglob("*.json")
        )
        if "SYNTHETIC-ORDER-1" not in dataset_text or "app_secret" in dataset_text:
            raise RuntimeError("frozen Lingxing dataset privacy contract failed")
        if (
            "orders" not in AVAILABLE_DATASETS
            or UNAVAILABLE_DATASETS.get("sales_traffic") != "sdk_contract_mismatch"
            or AvailableLingxingSyncService is None
        ):
            raise RuntimeError("frozen Lingxing available sync contract failed")

        verify_frozen_dashboard_worker(root / "dashboard-worker")


def _already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=1.2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def _stage_package(local_url: str, token: str, package_path: Path) -> str:
    payload = json.dumps(
        {"source_path": str(package_path.expanduser().absolute())},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{local_url}/v1/lingxing/stage-package",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Agent-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        raise RuntimeError(detail or "连接包导入失败") from exc
    import_token = str(result.get("import_token", "")).strip()
    if not import_token:
        raise RuntimeError("连接包导入会话创建失败")
    return f"{local_url}/lingxing?import_token={urllib.parse.quote(import_token, safe='')}"


def _open_when_ready(
    local_url: str,
    landing_url: str,
    token: str,
    package_path: Path | None,
) -> None:
    for _ in range(80):
        if _already_running(local_url):
            target = landing_url
            if package_path is not None:
                try:
                    target = _stage_package(local_url, token, package_path)
                except Exception:
                    target = f"{landing_url}?import_error=1"
            webbrowser.open(target)
            return
        time.sleep(0.2)
    webbrowser.open(f"{landing_url}?startup_error=1")


def _run_dashboard_child(args: argparse.Namespace) -> None:
    if not args.job_id or args.manifest is None or args.dashboard_context is None:
        raise RuntimeError("dashboard child arguments are incomplete")
    from agent.lingxing_dashboard_worker import run_dashboard_child

    exit_code = run_dashboard_child(
        args.data_root.resolve(),
        args.job_id,
        args.manifest.resolve(),
        args.dashboard_context.resolve(),
    )
    raise SystemExit(exit_code)


def main() -> None:
    args = build_parser().parse_args()
    if args.verify_sdk_runtime:
        verify_sdk_runtime()
        return
    if args.verify_probe_runtime:
        verify_probe_runtime()
        return
    if args.run_lingxing_dashboard_job:
        _run_dashboard_child(args)

    origins = tuple(args.allow_origin) or (
        f"http://127.0.0.1:{args.port}",
        f"http://localhost:{args.port}",
    )
    settings = AgentSettings(
        data_root=args.data_root,
        host=args.host,
        port=args.port,
        allowed_origins=origins,
    ).validated()
    token, _ = load_or_create_agent_token(settings.data_root)
    local_url = f"http://{settings.host}:{settings.port}"
    landing_url = f"{local_url}/lingxing"
    package_path = args.connection_package

    if package_path is not None and package_path.suffix.lower() != ".dba":
        package_path = None

    if _already_running(local_url):
        if not args.no_browser:
            target = landing_url
            if package_path is not None:
                try:
                    target = _stage_package(local_url, token, package_path)
                except Exception:
                    target = f"{landing_url}?import_error=1"
            webbrowser.open(target)
        return

    if not args.no_browser:
        threading.Thread(
            target=_open_when_ready,
            args=(local_url, landing_url, token, package_path),
            name="open-agent-ui",
            daemon=True,
        ).start()

    uvicorn.run(
        create_integrated_app(settings, token=token),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
