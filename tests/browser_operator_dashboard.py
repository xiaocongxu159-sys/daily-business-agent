# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from playwright.sync_api import sync_playwright

from src.dashboard_ux_patch import patch_dashboard_html_file
from src.html_dashboard_writer import write_html_dashboard


def _build_dashboard(root: Path) -> Path:
    workbook = root / "operator.xlsx"
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
        [{"类别": "开发诊断", "内容": "PRIVATE_TECHNICAL_MARKER"}]
    )
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        daily.to_excel(writer, sheet_name="每日数据录入", index=False)
        quality.to_excel(writer, sheet_name="数据质量报告", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="库存汇总", index=False)

    html_path, _ = write_html_dashboard(
        workbook,
        root,
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
    return patch_dashboard_html_file(html_path)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="operator-dashboard-") as temp:
        html_path = _build_dashboard(Path(temp))
        external_requests: list[str] = []
        browser_errors: list[str] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.on(
                "request",
                lambda request: external_requests.append(request.url)
                if not request.url.startswith("file:")
                else None,
            )
            page.on("pageerror", lambda error: browser_errors.append(str(error)))
            page.goto(html_path.as_uri(), wait_until="load")
            page.wait_for_selector("#store option", state="attached")

            store_options = page.locator("#store option").all()
            labels = [item.inner_text() for item in store_options]
            values = [item.get_attribute("value") for item in store_options]
            assert labels == ["全部店铺", "承拓嘉-US", "第二店铺-CA"] or labels == [
                "全部店铺",
                "第二店铺-CA",
                "承拓嘉-US",
            ]
            assert set(values) == {"", "12940", "12941"}
            assert page.get_by_role("heading", name="每日商品数据").count() == 0
            assert page.get_by_role("heading", name="数据质量").count() == 0
            assert page.get_by_text("PRIVATE_TECHNICAL_MARKER").count() == 0
            assert page.locator('[data-operator-summary="1"]').is_visible()
            assert "4 条店铺级广告汇总" in page.locator(
                '[data-operator-summary="1"]'
            ).inner_text()

            first_card = page.locator("#cards .metric").first
            assert first_card.locator("span").inner_text() == "商品数"
            assert first_card.locator("strong").inner_text() == "2"

            page.select_option("#store", "12940")
            summary = page.locator("#filterSummary").inner_text()
            assert "承拓嘉-US" in summary
            assert "12940" not in summary
            assert first_card.locator("strong").inner_text() == "1"
            assert page.locator(".charts .chart").count() == 4
            assert page.locator(".charts svg").count() == 3
            assert not external_requests, external_requests
            assert not browser_errors, browser_errors
            browser.close()


if __name__ == "__main__":
    main()
