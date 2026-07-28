# -*- coding: utf-8 -*-
from __future__ import annotations

import functools
import http.server
import json
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _axis_state(label):
    return label.evaluate(
        """
        node => ({
          side: node.getAttribute('data-axis-label'),
          upright: node.getAttribute('data-axis-upright'),
          transform: node.getAttribute('transform'),
          chars: Array.from(node.querySelectorAll('tspan')).map(item => item.textContent),
          xValues: Array.from(node.querySelectorAll('tspan')).map(item => item.getAttribute('x')),
          yValues: Array.from(node.querySelectorAll('tspan')).map(item => Number(item.getAttribute('y'))),
        })
        """
    )


def main() -> int:
    artifact_dir = Path(os.environ.get("RICH_DASHBOARD_ARTIFACT_DIR", tempfile.gettempdir()))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    events: list[str] = []
    try:
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
                    context = browser.new_context(viewport={"width": 1440, "height": 900})

                    def inspect_request(request) -> None:
                        parsed = urlsplit(request.url)
                        if parsed.scheme in {"data", "blob", "about"}:
                            return
                        if parsed.hostname not in {"127.0.0.1", "localhost"}:
                            external.append(request.url)

                    context.on("request", inspect_request)
                    page = context.new_page()
                    page.on("console", lambda message: events.append(f"console[{message.type}]: {message.text}"))
                    page.on("pageerror", lambda error: events.append(f"pageerror: {error}"))
                    try:
                        response = page.goto(
                            f"http://127.0.0.1:{server.server_port}/{dashboard.name}",
                            wait_until="domcontentloaded",
                        )
                        assert response is not None and response.status == 200
                        page.wait_for_function(
                            "() => ['trafficChart','salesChart','adsChart','inventoryChart'].every(id => document.querySelector('#'+id+' svg'))",
                            timeout=15_000,
                        )
                        events.append("charts=4")
                        assert "每日经营动态看板" in page.locator("h1").inner_text()
                        assert "13020.00" in page.locator("#cards").inner_text()
                        events.append("sales=13020.00")

                        axis_expectations = {
                            "trafficChart": {"left": list("流量")},
                            "salesChart": {"left": list("销量"), "right": list("销售额")},
                            "adsChart": {"left": list("广告花费"), "right": list("广告销售额")},
                            "inventoryChart": {"left": list("库存")},
                        }
                        axis_events = {}
                        for chart_id, expected_by_side in axis_expectations.items():
                            labels = page.locator(f"#{chart_id} [data-axis-label]")
                            assert labels.count() == len(expected_by_side), (
                                chart_id,
                                labels.count(),
                                expected_by_side,
                            )
                            actual_by_side = {}
                            for index in range(labels.count()):
                                label = labels.nth(index)
                                state = _axis_state(label)
                                actual_by_side[state["side"]] = state["chars"]
                                assert state["upright"] == "1", (chart_id, state)
                                assert state["transform"] is None, (chart_id, state)
                                assert len(set(state["xValues"])) == 1, (chart_id, state)
                                assert state["yValues"] == sorted(state["yValues"]), (chart_id, state)
                                assert len(set(state["yValues"])) == len(state["yValues"]), (chart_id, state)
                                box = label.bounding_box()
                                assert box is not None and box["width"] > 0 and box["height"] > 0, (
                                    chart_id,
                                    index,
                                    box,
                                )
                            assert actual_by_side == expected_by_side, (
                                chart_id,
                                actual_by_side,
                                expected_by_side,
                            )
                            axis_events[chart_id] = actual_by_side
                        events.append(f"upright_axes={json.dumps(axis_events, ensure_ascii=False)}")

                        traffic = page.locator("#trafficChart")
                        traffic.scroll_into_view_if_needed()
                        traffic_box = traffic.bounding_box()
                        assert traffic_box is not None
                        page.mouse.move(
                            traffic_box["x"] + traffic_box["width"] * 0.56,
                            traffic_box["y"] + traffic_box["height"] * 0.50,
                        )
                        tooltip = page.locator("#trafficChart [data-chart-tooltip]")
                        tooltip.wait_for(state="visible", timeout=5_000)
                        tooltip_text = tooltip.inner_text()
                        assert "2026-07-" in tooltip_text, tooltip_text
                        assert "Sessions" in tooltip_text and "PV" in tooltip_text, tooltip_text
                        tooltip_style = tooltip.evaluate(
                            "node => ({background:getComputedStyle(node).backgroundColor,color:getComputedStyle(node).color,hidden:node.hidden,date:node.dataset.tooltipDate})"
                        )
                        assert not tooltip_style["hidden"], tooltip_style
                        assert tooltip_style["date"].startswith("2026-07-"), tooltip_style
                        assert tooltip_style["background"] not in {"rgba(0, 0, 0, 0)", "transparent"}, tooltip_style
                        assert tooltip.locator(".chart-tooltip-dot").count() == 2
                        events.append(f"tooltip={json.dumps(tooltip_style, ensure_ascii=False)}")
                        page.screenshot(
                            path=str(artifact_dir / "rich-dashboard-tooltip-pass.png"),
                            full_page=True,
                        )

                        details = page.locator("#filterDetails")
                        assert not details.evaluate("node => node.open")
                        page.locator("#filterDetails > summary").click()
                        assert details.evaluate("node => node.open")
                        page.locator("#filterDetails > summary").click()
                        assert not details.evaluate("node => node.open")
                        events.append("filters=collapsed-expanded-collapsed")

                        traffic.hover()
                        before = page.evaluate("window.scrollY")
                        maximum = page.evaluate("document.documentElement.scrollHeight - window.innerHeight")
                        if before >= maximum - 5:
                            page.mouse.wheel(0, -500)
                            page.wait_for_timeout(300)
                            after = page.evaluate("window.scrollY")
                            assert after < before, (before, after, maximum)
                        else:
                            page.mouse.wheel(0, 500)
                            page.wait_for_timeout(300)
                            after = page.evaluate("window.scrollY")
                            assert after > before, (before, after, maximum)
                        events.append(f"wheel_before={before};wheel_after={after};max={maximum}")

                        widths = traffic.evaluate(
                            "node => ({container:node.clientWidth,svg:node.querySelector('svg').getBoundingClientRect().width})"
                        )
                        assert widths["svg"] <= widths["container"] + 1, widths
                        assert page.locator("#body").inner_text().count("Rich Product") >= 2
                        assert not external, external
                        events.append(f"widths={json.dumps(widths)}")
                        page.screenshot(path=str(artifact_dir / "rich-dashboard-pass.png"), full_page=True)
                    finally:
                        try:
                            page.screenshot(path=str(artifact_dir / "rich-dashboard-final.png"), full_page=True)
                        except Exception as exc:
                            events.append(f"screenshot_error={exc}")
                        context.close()
                        browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=10)
    except Exception:
        error_text = traceback.format_exc()
        (artifact_dir / "rich-dashboard-error.txt").write_text(error_text, encoding="utf-8")
        print(error_text, flush=True)
        raise
    finally:
        (artifact_dir / "rich-dashboard-events.txt").write_text("\n".join(events) + "\n", encoding="utf-8")
    print("PASS: rich dashboard upright axes, rich tooltip, filters, wheel scrolling and local-only network")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
