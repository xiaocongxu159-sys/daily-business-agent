# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.html_dashboard_writer import filter_dashboard_records, write_html_dashboard


def _workbook(path: Path) -> None:
    rows = []
    for day in range(1, 8):
        rows.append({
            "日期": f"2026-07-{day:02d}",
            "shop_id": "STORE-RICH",
            "shop_name": "Rich Dashboard Store",
            "marketplace": "US",
            "SKU": "SKU-RICH",
            "MSKU": "SKU-RICH",
            "ASIN": "B0RICH001",
            "产品名称": "Rich Dashboard Product",
            "Sessions": day * 10,
            "PV": day * 12,
            "总订单": day,
            "销售额": day * 19.99,
            "广告花费": day * 2.5,
            "广告销售额": day * 9.99,
            "FBA可售库存": 100 - day,
            "总库存": 130 - day,
        })
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="每日数据录入", index=False)
        pd.DataFrame([{"类别": "结果", "内容": "合成数据正常"}]).to_excel(
            writer, sheet_name="数据质量报告", index=False
        )


def test_rich_dashboard_is_local_and_contains_expected_charts(tmp_path: Path) -> None:
    workbook = tmp_path / "report.xlsx"
    _workbook(workbook)
    html_path, json_path = write_html_dashboard(workbook, tmp_path / "output")

    html = html_path.read_text(encoding="utf-8")
    assert "data-local-dashboard-checked=\"true\"" in html
    assert "Content-Security-Policy" in html
    assert "http://" not in html and "https://" not in html
    for chart_id in ("trafficChart", "salesChart", "adsChart", "inventoryChart"):
        assert f'id="{chart_id}"' in html
    for label in ("流量", "销量", "销售额", "广告花费", "广告销售额", "库存"):
        assert label in html
    assert "function uprightAxis" in html
    assert "data-axis-label" in html
    assert 'data-axis-upright="1"' in html
    assert 'data-chart-tooltip="1"' in html
    assert "chart-tooltip-row" in html
    assert "chart-tooltip-dot" in html
    assert "pointermove" in html
    assert 'id="filterDetails"' in html
    assert 'id="dashboard-data"' in html
    assert 'id="cards"' in html
    assert 'id="body"' in html

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(payload["daily"]) == 7
    assert round(sum(float(row["销售额"]) for row in payload["daily"]), 2) == 559.72
    assert payload["meta"]["min_date"] == "2026-07-01"
    assert payload["meta"]["max_date"] == "2026-07-07"


def test_compound_filters_remain_store_safe() -> None:
    records = [
        {"shop_id": "A", "marketplace": "US", "ASIN": "B0A", "SKU": "SKU-A"},
        {"shop_id": "B", "marketplace": "CA", "ASIN": "B0B", "SKU": "SKU-B"},
    ]
    assert filter_dashboard_records(records, shop_id="A") == [records[0]]
    assert filter_dashboard_records(records, marketplace="CA", asin="B0B") == [records[1]]
