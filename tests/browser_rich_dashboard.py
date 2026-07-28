# -*- coding: utf-8 -*-
from __future__ import annotations

import functools
import http.server
import json
import os
import re
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


CHART_EXPECTATIONS = {
    "trafficChart": {
        "title": "业务流量趋势",
        "series": ["Sessions", "PV"],
        "axes": {"left": list("流量")},
    },
    "salesChart": {
        "title": "销量与销售额趋势",
        "series": ["订单量", "销售额"],
        "axes": {"left": list("销量"), "right": list("销售额")},
    },
    "adsChart": {
        "title": "广告花费与销售额趋势",
        "series": ["广告花费", "广告销售额"],
        "axes": {"left": list("广告花费"), "right": list("广告销售额")},
    },
    "inventoryChart": {
        "title": "库存趋势",
        "series": ["FBA可售库存", "总库存"],
        "axes": {"left": list("库存")},
    },
}


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
          html: node.outerHTML,
          chars: Array.from(node.querySelectorAll('tspan')).map(item => item.textContent),
          xValues: Array.from(node.querySelectorAll('tspan')).map(item => item.getAttribute('x')),
          yValues: Array.from(node.querySelectorAll('tspan')).map(item => Number(item.getAttribute('y'))),
          tspanTransforms: Array.from(node.querySelectorAll('tspan')).map(item => item.getAttribute('transform')),
        })
        """
    )


def _assert_upright_axes(page, events: list[str]) -> None:
    axis_events = {}
    for chart_id, expected in CHART_EXPECTATIONS.items():
        expected_by_side = expected["axes"]
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
            assert all(value is None for value in state["tspanTransforms"]), (chart_id, state)
            assert "rotate(90" not in state["html"], (chart_id, state)
            assert "rotate(-90" not in state["html"], (chart_id, state)
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


def _assert_chart_structure(page, events: list[str]) -> None:
    states = {}
    for chart_id, expected in CHART_EXPECTATIONS.items():
        chart = page.locator(f"#{chart_id}")
        svg = chart.locator("svg")
        text = svg.text_content() or ""
        assert svg.get_attribute("aria-label") == expected["title"], (chart_id, text)
        assert expected["title"] in text, (chart_id, text)
        for series_name in expected["series"]:
            assert series_name in text, (chart_id, series_name, text)
        assert "07-01" in text and "07-30" in text, (chart_id, text)
        point_count = chart.locator("[data-chart-point]").count()
        assert point_count == 30 * len(expected["series"]), (chart_id, point_count)
        assert chart.locator("title").count() == 0, chart_id
        widths = chart.evaluate(
            "node => ({container:node.clientWidth,svg:node.querySelector('svg').getBoundingClientRect().width})"
        )
        assert widths["svg"] <= widths["container"] + 1, (chart_id, widths)
        states[chart_id] = {"points": point_count, "widths": widths}
    events.append(f"chart_structure={json.dumps(states, ensure_ascii=False)}")


def _hover_and_assert_tooltip(page, chart_id: str, artifact_dir: Path, events: list[str]) -> None:
    expected_series = CHART_EXPECTATIONS[chart_id]["series"]
    chart = page.locator(f"#{chart_id}")
    chart.scroll_into_view_if_needed()
    svg = chart.locator("svg")
    svg_box = svg.bounding_box()
    assert svg_box is not None
    page.mouse.move(
        svg_box["x"] + svg_box["width"] * 0.56,
        svg_box["y"] + svg_box["height"] * 0.50,
    )
    tooltip = chart.locator("[data-chart-tooltip]")
    tooltip.wait_for(state="visible", timeout=5_000)
    tooltip_state = tooltip.evaluate(
        r"""
        node => ({
          tag: node.tagName,
          background: getComputedStyle(node).backgroundColor,
          color: getComputedStyle(node).color,
          hidden: node.hidden,
          date: node.dataset.tooltipDate,
          width: node.getBoundingClientRect().width,
          height: node.getBoundingClientRect().height,
          rows: Array.from(node.querySelectorAll('.chart-tooltip-row')).map(row => ({
            name: (row.querySelectorAll('span')[1]?.textContent || '').replace(/：\s*$/, ''),
            value: row.querySelector('strong')?.textContent || '',
            dot: getComputedStyle(row.querySelector('.chart-tooltip-dot')).backgroundColor,
          })),
        })
        """
    )
    assert tooltip_state["tag"] == "DIV", (chart_id, tooltip_state)
    assert not tooltip_state["hidden"], (chart_id, tooltip_state)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", tooltip_state["date"]), (
        chart_id,
        tooltip_state,
    )
    assert tooltip_state["width"] > 100 and tooltip_state["height"] > 40, (
        chart_id,
        tooltip_state,
    )
    background_numbers = [float(value) for value in re.findall(r"[\d.]+", tooltip_state["background"])]
    assert len(background_numbers) >= 3 and max(background_numbers[:3]) < 80, (
        chart_id,
        tooltip_state,
    )
    assert tooltip_state["color"] == "rgb(255, 255, 255)", (chart_id, tooltip_state)
    assert [row["name"] for row in tooltip_state["rows"]] == expected_series, (
        chart_id,
        tooltip_state,
    )
    assert all(row["value"].strip() for row in tooltip_state["rows"]), (
        chart_id,
        tooltip_state,
    )
    assert all(row["dot"] not in {"rgba(0, 0, 0, 0)", "transparent"} for row in tooltip_state["rows"]), (
        chart_id,
        tooltip_state,
    )
    screenshot = artifact_dir / f"rich-dashboard-tooltip-{chart_id}.png"
    page.screenshot(path=str(screenshot), full_page=False)
    assert screenshot.stat().st_size > 10_000, screenshot
    tooltip.wait_for(state="visible", timeout=2_000)
    events.append(f"tooltip_{chart_id}={json.dumps(tooltip_state, ensure_ascii=False)}")
    page.mouse.move(2, 2)
    tooltip.wait_for(state="hidden", timeout=2_000)


def _assert_inventory_changes_by_date(page, events: list[str]) -> None:
    trends = {}
    for series_index, series_name in enumerate(CHART_EXPECTATIONS["inventoryChart"]["series"]):
        points = page.locator(
            f'#inventoryChart [data-chart-point][data-series-index="{series_index}"]'
        )
        assert points.count() == 30, (series_name, points.count())
        first_y = float(points.first.get_attribute("cy"))
        last_y = float(points.last.get_attribute("cy"))
        assert last_y > first_y + 20, (series_name, first_y, last_y)
        trends[series_name] = {"first_y": first_y, "last_y": last_y}
    events.append(f"inventory_daily_change={json.dumps(trends, ensure_ascii=False)}")


def _assert_filters_and_wheel(page, events: list[str]) -> None:
    details = page.locator("#filterDetails")
    assert not details.evaluate("node => node.open")
    page.locator("#filterDetails > summary").click()
    assert details.evaluate("node => node.open")
    page.locator("#filterDetails > summary").click()
    assert not details.evaluate("node => node.open")
    events.append("filters=collapsed-expanded-collapsed")

    traffic = page.locator("#trafficChart")
    traffic.scroll_into_view_if_needed()
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


def _assert_responsive_width(page, artifact_dir: Path, events: list[str]) -> None:
    page.set_viewport_size({"width": 780, "height": 900})
    page.wait_for_timeout(400)
    boxes = []
    widths = {}
    for chart_id in CHART_EXPECTATIONS:
        chart = page.locator(f"#{chart_id}")
        box = chart.bounding_box()
        assert box is not None
        boxes.append((chart_id, box))
        state = chart.evaluate(
            "node => ({container:node.clientWidth,svg:node.querySelector('svg').getBoundingClientRect().width})"
        )
        assert state["container"] > 300, (chart_id, state)
        assert state["svg"] <= state["container"] + 1, (chart_id, state)
        widths[chart_id] = state
    base_x = boxes[0][1]["x"]
    assert all(abs(box["x"] - base_x) < 2 for _, box in boxes), boxes
    assert [box["y"] for _, box in boxes] == sorted(box["y"] for _, box in boxes), boxes
    screenshot = artifact_dir / "rich-dashboard-responsive-780.png"
    page.screenshot(path=str(screenshot), full_page=True)
    assert screenshot.stat().st_size > 10_000
    events.append(f"responsive_widths={json.dumps(widths, ensure_ascii=False)}")


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

                        _assert_upright_axes(page, events)
                        _assert_chart_structure(page, events)
                        _assert_inventory_changes_by_date(page, events)
                        for chart_id in CHART_EXPECTATIONS:
                            _hover_and_assert_tooltip(page, chart_id, artifact_dir, events)
                        _assert_filters_and_wheel(page, events)
                        assert page.locator("#body").inner_text().count("Rich Product") >= 2
                        _assert_responsive_width(page, artifact_dir, events)
                        assert not external, external
                        browser_errors = [
                            event
                            for event in events
                            if event.startswith("pageerror:") or event.startswith("console[error]:")
                        ]
                        assert not browser_errors, browser_errors
                        events.append("network=local-only")
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
    print(
        "PASS: four rich dashboard charts, upright axes, black tooltips, inventory dates, filters, "
        "wheel scrolling, responsive width and local-only network"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
