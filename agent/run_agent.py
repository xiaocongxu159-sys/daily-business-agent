# -*- coding: utf-8 -*-
"""Launcher for the Windows local Agent."""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from agent.lingxing_integration import create_integrated_app
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
    return parser


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


def main() -> None:
    args = build_parser().parse_args()
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
