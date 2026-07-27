# -*- coding: utf-8 -*-
from __future__ import annotations

import functools
import http.server
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from playwright.sync_api import sync_playwright

from src.html_dashboard_writer import write_html_dashboard


def _build(root: Path) -> Path:
    rows = []
    for day in range(1, 31):
        for sku_index in (1, 2):
            rows.append({
                "日期": f"2026-07-{day:02d}",
                "shop_id": "STORE-RICH",
                "shop_name": "Rich Dashboard Store",
                "marketplace": "US",
                "SKU": f"SKU-RICH-{sku_index}",
                "MSKU": f"SKU-RICH-{sku_index}",
                "ASIN": f"B0RICH00{sku_index}",
                "产品名称": f"Rich Product {sku_index}",
                "Sessions": day * (8 + sku_index),
                "PV": day * (10 + sku_index),
                "总订单": day % 5 + sku_index,
                "销售额": day * (12.5 + sku_index),
                "广告花费": day * (1.5 + sku_index / 2),
                "广告销售额": day * (5 + sku_index),
                "FBA可售库存": 150 - day - sku_index,
                "总库存": 190 - day - sku_index,
            })
    workbook = root / "rich.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="每日数据录入", index=False)
        pd.DataFrame([{"类别": "结果", "内容": "浏览器合成数据正常"}]).to_excel(
            writer, sheet_name="数据质量报告", index=False
        )
    html, _ = write_html_dashboard(workbook, root)
    return html


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rich-dashboard-browser-") as temp:
        root = Path(temp)
        dashboard = _build(root)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        external: list[str] = []
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 800})
                context.on(
                    "request",
                    lambda request: external.append(request.url)
                    if urlsplit(request.url).hostname not in {"127.0.0.1", "localhost"}
                    else None,
                )
                page = context.new_page()
                response = page.goto(
                    f"http://127.0.0.1:{server.server_port}/{dashboard.name}",
                    wait_until="domcontentloaded",
                )
                assert response is not None and response.status == 200
                page.wait_for_selector("#trafficChart svg", timeout=15_000)
                for chart_id in ("trafficChart", "salesChart", "adsChart", "inventoryChart"):
                    assert page.locator(f"#{chart_id} svg").count() == 1
                assert "每日经营动态看板" in page.locator("h1").inner_text()
                assert "13020.00" in page.locator("#cards").inner_text()
                assert not page.locator("#filterDetails").evaluate("node => node.open")
                page.locator("#filterDetails > summary").click()
                assert page.locator("#filterDetails").evaluate("node => node.open")
                page.locator("#filterDetails > summary").click()
                assert not page.locator("#filterDetails").evaluate("node => node.open")
                chart = page.locator("#trafficChart")
                chart.scroll_into_view_if_needed()
                chart.hover()
                before = page.evaluate("window.scrollY")
                page.mouse.wheel(0, 650)
                page.wait_for_timeout(300)
                after = page.evaluate("window.scrollY")
                assert after > before, (before, after)
                widths = chart.evaluate(
                    "node => ({container:node.clientWidth,svg:node.querySelector('svg').getBoundingClientRect().width})"
                )
                assert widths["svg"] <= widths["container"] + 1, widths
                assert page.locator("#body").inner_text().count("Rich Product") >= 2
                context.close()
                browser.close()
                assert not external, external
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
    print("PASS: rich dashboard charts, filters, wheel scrolling and local-only network")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
