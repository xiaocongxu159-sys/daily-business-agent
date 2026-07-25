# -*- coding: utf-8 -*-
"""Launcher for the Windows local Agent."""
from __future__ import annotations

import argparse
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from agent.lingxing_integration import create_integrated_app
from agent.security import load_or_create_agent_token
from agent.settings import AgentSettings, default_data_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily Business Windows Local Agent")
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
    if _already_running(local_url):
        if not args.no_browser:
            webbrowser.open(landing_url)
        return
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(landing_url)).start()
    uvicorn.run(
        create_integrated_app(settings, token=token),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
