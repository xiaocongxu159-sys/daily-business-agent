# -*- coding: utf-8 -*-
"""Real-Chromium acceptance for the unified single-file Lingxing page."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from playwright.sync_api import BrowserContext, expect, sync_playwright

from agent.lingxing_integration import create_integrated_app
from agent.lingxing_secure_store import TestOnlyProtector
from agent.settings import AgentSettings
from browser_public_pages import (
    BASE_URL,
    HOST,
    PORT,
    TOKEN,
    BrowserFakeProvider,
    _wait_for_server,
    _watch_network,
)


def run_page_check() -> None:
    with tempfile.TemporaryDirectory(prefix="daily-agent-lingxing-page-") as temp:
        root = Path(temp)
        app = create_integrated_app(
            AgentSettings(data_root=root / "agent-data", host=HOST, port=PORT),
            token=TOKEN,
            provider_factory=BrowserFakeProvider,
            protector=TestOnlyProtector(),
            start_service=False,
        )
        server = uvicorn.Server(
            uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", access_log=False)
        )
        thread = threading.Thread(target=server.run, name="lingxing-browser-server", daemon=True)
        thread.start()
        _wait_for_server()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context: BrowserContext = browser.new_context()
                external = _watch_network(context)
                page = context.new_page()
                response = page.goto(f"{BASE_URL}/lingxing", wait_until="domcontentloaded")
                if response is None or response.status != 200:
                    raise AssertionError("Lingxing page did not return 200")
                expect(page).to_have_title("领星自动同步 · 每日经营数据")
                expect(page.locator("#show-config")).to_be_visible()
                expect(page.locator("#package-ready")).to_be_attached()
                expect(page.locator("#save-config")).to_be_disabled()
                if page.locator('input[type="file"]').count():
                    raise AssertionError("single-file native flow must not expose browser file input")
                if page.locator("#proxy-url").count():
                    raise AssertionError("technical proxy field must remain hidden")
                context.close()
                browser.close()
                if external:
                    raise AssertionError(
                        "non-local browser requests detected:\n" + json.dumps(external, indent=2)
                    )
        finally:
            server.should_exit = True
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError("Lingxing browser-test server did not stop cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=("page",), required=True)
    parser.parse_args()
    run_page_check()
    print("Lingxing browser acceptance passed: single-file local-only page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
