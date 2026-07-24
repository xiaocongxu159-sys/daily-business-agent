# -*- coding: utf-8 -*-
"""Real-Chromium acceptance checks for the local-only Lingxing page."""
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
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

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


def _open_lingxing(page: Page) -> None:
    response = page.goto(f"{BASE_URL}/lingxing", wait_until="domcontentloaded")
    if response is None or response.status != 200:
        raise AssertionError(
            f"Lingxing page returned {None if response is None else response.status}"
        )
    expect(page).to_have_title("领星自动同步 · 每日经营数据")
    expect(page.locator(".notice")).to_contain_text("数据只保存在这台电脑")
    expect(page.locator("#show-config")).to_be_visible()
    expect(page.locator("#config-panel")).to_be_attached()


def _configure(page: Page) -> None:
    _open_lingxing(page)
    page.locator("#show-config").click()
    expect(page.locator("#config-panel")).to_have_attribute("open", "")
    page.locator("#app-id").fill("synthetic-browser-app")
    page.locator("#app-secret").fill("synthetic-browser-secret")
    page.locator("#proxy-url").fill("http://127.0.0.1:18080")
    page.locator("#auto-sync").uncheck()
    page.locator("#config-form button[type=submit]").click()
    expect(page.locator("#message")).to_contain_text("连接成功", timeout=15_000)
    expect(page.locator("#app-secret")).to_have_value("")
    expect(page.locator("#proxy-url")).to_have_value("")
    expect(page.locator("#shops")).to_contain_text("Synthetic Browser Store")
    if "synthetic-browser-secret" in page.locator("body").inner_text():
        raise AssertionError("AppSecret remained visible in the Lingxing page")


def _disconnect(page: Page) -> None:
    _configure(page)
    page.locator("#show-config").click()
    expect(page.locator("#config-panel")).to_have_attribute("open", "")
    expect(page.locator("#disconnect")).to_be_visible()
    expect(page.locator("#disconnect")).to_be_enabled()
    page.evaluate("window.confirm = () => true")
    page.locator("#disconnect").click()
    expect(page.locator("#message")).to_contain_text("已断开", timeout=10_000)
    expect(page.locator("#shops")).to_contain_text("Synthetic Browser Store")


def _run_check(check: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"daily-agent-lingxing-{check}-") as temp:
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
                if check == "page":
                    _open_lingxing(page)
                elif check == "configure":
                    _configure(page)
                elif check == "disconnect":
                    _disconnect(page)
                else:
                    raise ValueError(f"unsupported Lingxing browser check: {check}")
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
    parser.add_argument("--check", choices=("page", "configure", "disconnect"), required=True)
    args = parser.parse_args()
    _run_check(args.check)
    print(f"Lingxing browser acceptance passed: {args.check}, local-only network")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
