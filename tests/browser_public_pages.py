# -*- coding: utf-8 -*-
"""Real-Chromium synthetic acceptance checks for the public local pages."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

from agent.lingxing_integration import create_integrated_app
from agent.lingxing_secure_store import LingxingCredentials, TestOnlyProtector
from agent.settings import AgentSettings

HOST = "127.0.0.1"
PORT = 8766
BASE_URL = f"http://{HOST}:{PORT}"
TOKEN = "synthetic-browser-token"


class BrowserFakeProvider:
    def list_shops(self, credentials: LingxingCredentials) -> list[dict]:
        if credentials.app_id != "synthetic-browser-app":
            raise AssertionError("unexpected synthetic AppID")
        if credentials.app_secret != "synthetic-browser-secret":
            raise AssertionError("unexpected synthetic AppSecret")
        return [
            {
                "seller_id": "SELLER-BROWSER-1",
                "seller_name": "Synthetic Browser Store",
                "country": "US",
                "region": "NA",
                "status": "active",
                "ads_authorized": True,
            }
        ]


def _wait_for_server(timeout_seconds: float = 15) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("local browser-test server did not start")


def _write_synthetic_inputs(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    mapping = root / "mapping.csv"
    business = root / "business_20260724.csv"
    advertising = root / "advertising.csv"
    mapping.write_text(
        "shop_id,shop_name,marketplace,parent_asin,seller-sku,MSKU,asin1,item-name,fulfillment-channel,status\n"
        "STORE-BROWSER,Synthetic Browser Store,US,B0PARENT01,SKU-BROWSER,SKU-BROWSER,B0CHILD01,Synthetic Browser Product,FBA,Active\n",
        encoding="utf-8",
    )
    business.write_text(
        "shop_id,marketplace,ASIN,Sessions,Page Views,Units Ordered,Ordered Product Sales,Total Order Items\n"
        "STORE-BROWSER,US,B0CHILD01,120,150,12,239.88,12\n",
        encoding="utf-8",
    )
    advertising.write_text(
        "shop_id,marketplace,Date,Campaign,Ad Group,Advertised ASIN,Advertised SKU,Impressions,Clicks,Spend,Orders,Sales\n"
        "STORE-BROWSER,US,2026-07-24,Synthetic Campaign,Synthetic Group,B0CHILD01,SKU-BROWSER,1000,80,40,6,119.94\n",
        encoding="utf-8",
    )
    return {"mapping": mapping, "business": business, "advertising": advertising}


def _watch_network(context: BrowserContext) -> list[str]:
    external: list[str] = []

    def inspect_request(request) -> None:
        parsed = urlsplit(request.url)
        if parsed.scheme in {"data", "blob", "about"}:
            return
        if parsed.scheme not in {"http", "https"}:
            external.append(request.url)
            return
        if parsed.hostname not in {HOST, "localhost"}:
            external.append(request.url)

    context.on("request", inspect_request)
    return external


def _open_report_page(page: Page) -> None:
    response = page.goto(BASE_URL, wait_until="domcontentloaded")
    if response is None or response.status != 200:
        raise AssertionError(
            f"report page returned {None if response is None else response.status}"
        )
    expect(page).to_have_title("每日经营数据分析")
    expect(page.locator(".notice")).to_contain_text("只在你电脑")
    expect(page.locator("#start-analysis")).to_be_visible()
    expect(page.locator("#input-mapping")).to_be_attached()
    expect(page.locator("#input-business")).to_be_attached()
    expect(page.locator("#input-advertising")).to_be_attached()


def _run_report_analysis(page: Page, inputs: dict[str, Path]) -> None:
    _open_report_page(page)
    page.locator("#input-mapping").set_input_files(str(inputs["mapping"]))
    page.locator("#input-business").set_input_files(str(inputs["business"]))
    page.locator("#input-advertising").set_input_files(str(inputs["advertising"]))
    page.locator("#job-label").fill("Synthetic Browser Acceptance")
    page.locator("#start-analysis").click()
    expect(page.locator("#job-status")).to_have_text("success", timeout=90_000)
    expect(page.locator("#job-step")).to_contain_text("分析完成")
    expect(page.get_by_role("link", name="打开经营看板")).to_be_visible()
    expect(page.get_by_role("link", name="下载完整 Excel")).to_be_visible()
    if page.locator("#message").get_attribute("class") == "error":
        raise AssertionError(
            "report page showed an error: " + page.locator("#message").inner_text()
        )


def _dashboard_href(page: Page, inputs: dict[str, Path]) -> str:
    _run_report_analysis(page, inputs)
    link = page.get_by_role("link", name="打开经营看板")
    expect(link).to_have_attribute("target", "_blank")
    href = link.get_attribute("href")
    if not href or not href.startswith("/v1/jobs/") or not href.endswith("/dashboard/"):
        raise AssertionError(f"unexpected dashboard href: {href!r}")
    return href


def _load_dashboard(page: Page, inputs: dict[str, Path]) -> Page:
    href = _dashboard_href(page, inputs)
    dashboard = page.context.new_page()
    response = dashboard.goto(urljoin(BASE_URL, href), wait_until="domcontentloaded")
    if response is None or response.status != 200:
        raise AssertionError(
            f"dashboard returned {None if response is None else response.status}"
        )
    expect(dashboard).to_have_title("每日经营看板")
    return dashboard


def _dashboard_payload(dashboard: Page) -> dict:
    text = dashboard.locator("#dashboard-data").text_content()
    if not text:
        raise AssertionError("dashboard embedded payload is empty")
    payload = json.loads(text)
    if not payload.get("daily"):
        raise AssertionError("dashboard daily payload is empty")
    return payload


def _check_dashboard_payload_product(page: Page, inputs: dict[str, Path]) -> None:
    dashboard = _load_dashboard(page, inputs)
    payload = _dashboard_payload(dashboard)
    products = [str(row.get("产品名称", "")) for row in payload["daily"]]
    if "Synthetic Browser Product" not in products:
        raise AssertionError(f"product name missing from dashboard payload: {products!r}")
    dashboard.close()


def _check_dashboard_payload_sales(page: Page, inputs: dict[str, Path]) -> None:
    dashboard = _load_dashboard(page, inputs)
    payload = _dashboard_payload(dashboard)
    sales = [float(row.get("销售额", 0) or 0) for row in payload["daily"]]
    if not any(abs(value - 239.88) < 0.001 for value in sales):
        raise AssertionError(f"sales value missing from dashboard payload: {sales!r}")
    dashboard.close()


def _check_dashboard_rendered_product(page: Page, inputs: dict[str, Path]) -> None:
    dashboard = _load_dashboard(page, inputs)
    expect(dashboard.locator("#body")).to_contain_text("Synthetic Browser Product")
    dashboard.close()


def _check_dashboard_rendered_sales(page: Page, inputs: dict[str, Path]) -> None:
    dashboard = _load_dashboard(page, inputs)
    expect(dashboard.locator("#cards")).to_contain_text("239.88")
    dashboard.close()


def _check_lingxing_page(page: Page) -> None:
    response = page.goto(f"{BASE_URL}/lingxing", wait_until="domcontentloaded")
    if response is None or response.status != 200:
        raise AssertionError(
            f"Lingxing page returned {None if response is None else response.status}"
        )
    expect(page).to_have_title("领星自动同步 · 每日经营数据")
    expect(page.locator(".notice")).to_contain_text("数据只保存在这台电脑")
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

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#disconnect").click()
    expect(page.locator("#message")).to_contain_text("已断开", timeout=10_000)
    expect(page.locator("#shops")).to_contain_text("Synthetic Browser Store")


def _run_check(check: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"daily-agent-browser-{check}-") as temp:
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
        thread = threading.Thread(target=server.run, name="browser-test-server", daemon=True)
        thread.start()
        _wait_for_server()

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(accept_downloads=True)
                external = _watch_network(context)
                page = context.new_page()
                inputs = _write_synthetic_inputs(root / "inputs")
                if check == "report-page":
                    _open_report_page(page)
                elif check == "report-analysis":
                    _run_report_analysis(page, inputs)
                elif check == "dashboard-link":
                    _dashboard_href(page, inputs)
                elif check == "dashboard-load":
                    dashboard = _load_dashboard(page, inputs)
                    dashboard.close()
                elif check == "dashboard-payload-product":
                    _check_dashboard_payload_product(page, inputs)
                elif check == "dashboard-payload-sales":
                    _check_dashboard_payload_sales(page, inputs)
                elif check == "dashboard-render-product":
                    _check_dashboard_rendered_product(page, inputs)
                elif check == "dashboard-render-sales":
                    _check_dashboard_rendered_sales(page, inputs)
                elif check == "lingxing":
                    _check_lingxing_page(page)
                else:
                    raise ValueError(f"unsupported browser check: {check}")
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
                raise RuntimeError("browser-test server did not stop cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        choices=(
            "report-page",
            "report-analysis",
            "dashboard-link",
            "dashboard-load",
            "dashboard-payload-product",
            "dashboard-payload-sales",
            "dashboard-render-product",
            "dashboard-render-sales",
            "lingxing",
        ),
        required=True,
    )
    args = parser.parse_args()
    _run_check(args.check)
    print(f"browser acceptance passed: {args.check}, local-only network")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
