# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.dashboard_ux_patch import patch_dashboard_html_file
from src.html_dashboard_writer import write_html_dashboard


def _write_workbook(path: Path) -> None:
    daily = pd.DataFrame(
        [
            {
                "日期": "2026-07-28",
                "shop_id": "12940",
                "shop_name": "承拓嘉-US",
                "marketplace": "美国",
                "SKU": "SKU-1",
                "MSKU": "SKU-1",
                "ASIN": "B000000001",
                "产品名称": "测试商品",
                "总订单": 2,
                "销售额": 39.98,
                "广告花费": 3.5,
                "广告销售额": 19.99,
                "FBA可售库存": 12,
                "总库存": 18,
            },
            {
                "日期": "2026-07-29",
                "shop_id": "12940",
                "shop_name": "承拓嘉-US",
                "marketplace": "美国",
                "SKU": "SKU-1",
                "MSKU": "SKU-1",
                "ASIN": "B000000001",
                "产品名称": "测试商品",
                "总订单": 1,
                "销售额": 19.99,
                "广告花费": 1.5,
                "广告销售额": 9.99,
                "FBA可售库存": 11,
                "总库存": 17,
            },
            {
                "日期": "2026-07-29",
                "shop_id": "12941",
                "shop_name": "第二店铺-CA",
                "marketplace": "加拿大",
                "SKU": "SKU-2",
                "MSKU": "SKU-2",
                "ASIN": "B000000002",
                "产品名称": "第二商品",
                "总订单": 3,
                "销售额": 59.97,
                "广告花费": 2.0,
                "广告销售额": 20.0,
                "FBA可售库存": 8,
                "总库存": 10,
            },
        ]
    )
    quality = pd.DataFrame(
        [{"类别": "有广告数据但无业务数据的行", "内容": "PRIVATE_TECHNICAL_MARKER"}]
    )
    inventory = pd.DataFrame()
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        daily.to_excel(writer, sheet_name="每日数据录入", index=False)
        quality.to_excel(writer, sheet_name="数据质量报告", index=False)
        inventory.to_excel(writer, sheet_name="库存汇总", index=False)


def test_lingxing_operator_dashboard_hides_technical_detail_and_labels_stores(tmp_path: Path) -> None:
    workbook = tmp_path / "report.xlsx"
    _write_workbook(workbook)
    html_path, _ = write_html_dashboard(
        workbook,
        tmp_path,
        dashboard_context={
            "meta": {
                "source_mode": "lingxing_local_sync",
                "source_label": "本机领星同步快照",
                "inventory_snapshot_date": "2026-07-29",
            },
            "metric_availability": {
                "sessions": {"status": "unavailable", "reason": "当前数据源未提供"},
                "page_views": {"status": "unavailable", "reason": "当前数据源未提供"},
            },
            "supplemental_daily": [
                {
                    "date": "2026-07-29",
                    "shop_id": "12940",
                    "shop_name": "承拓嘉-US",
                    "marketplace": "美国",
                    "adSpend": 1.0,
                    "adSales": 2.0,
                    "sourceRows": 4,
                }
            ],
        },
    )

    patch_dashboard_html_file(html_path)
    text = html_path.read_text(encoding="utf-8")

    assert "<h2>每日商品数据</h2>" not in text
    assert "<h2>数据质量</h2>" not in text
    assert "PRIVATE_TECHNICAL_MARKER" not in text
    assert "运营提示" in text
    assert "库存快照日期：2026-07-29" in text
    assert "4 条店铺级广告汇总已计入广告总额" in text
    assert "['商品数',productCount,'筛选范围内去重']" in text
    assert "storeOptions();options('market'" in text
    assert "selectedStoreLabel" in text
    assert "body.innerHTML=data.slice" not in text
    assert "quality.innerHTML" not in text

    match = re.search(
        r'<script type="application/json" id="dashboard-data">(.*?)</script>',
        text,
        re.DOTALL,
    )
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload["quality"] == []
    assert payload["daily"]


def test_cleanup_is_atomic_and_rejects_remote_resources(tmp_path: Path) -> None:
    target = tmp_path / "dashboard.html"
    target.write_text(
        '<html data-local-dashboard-checked="true"><head><script src="https://example.invalid/x.js"></script></head>'
        '<body>每日经营看板</body></html>',
        encoding="utf-8",
    )
    original = target.read_text(encoding="utf-8")
    try:
        patch_dashboard_html_file(target)
    except ValueError as exc:
        assert "remote script" in str(exc)
    else:
        raise AssertionError("remote dashboard resource was not rejected")
    assert target.read_text(encoding="utf-8") == original
