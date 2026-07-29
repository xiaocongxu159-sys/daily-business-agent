# -*- coding: utf-8 -*-
from __future__ import annotations

from playwright.sync_api import expect

import browser_lingxing_steps as legacy


def _check_operator_dashboard(page) -> None:
    expect(page).to_have_title("每日经营看板")
    expect(page.locator("#source")).to_contain_text("本机领星同步快照")
    expect(page.locator("#availabilityNotice")).to_be_visible()
    expect(page.locator("#availabilityNotice")).to_contain_text("暂不可用")
    expect(page.locator("#cards")).to_contain_text("商品数")
    expect(page.locator("#cards")).to_contain_text("59.97")
    expect(page.locator("#cards")).to_contain_text("7.75")
    expect(page.locator("#cards")).to_contain_text("含店铺级未分配广告")
    expect(page.locator("#trafficChart")).to_contain_text("不会将缺失数据绘制为 0")
    expect(page.locator("#salesChart svg")).to_be_visible()
    expect(page.locator("#adsChart svg")).to_be_visible()
    expect(page.locator("#inventoryChart svg")).to_be_visible()
    expect(page.locator('[data-axis-upright="1"]')).to_have_count(5)
    expect(page.locator('[data-chart-tooltip="1"]')).to_have_count(3)
    expect(page.get_by_role("heading", name="每日商品数据")).to_have_count(0)
    expect(page.get_by_role("heading", name="数据质量")).to_have_count(0)
    expect(page.locator('[data-operator-summary="1"]')).to_be_visible()
    expect(page.locator('[data-operator-summary="1"]')).to_contain_text(
        "库存快照日期：2026-07-29"
    )

    page.locator("#filterDetails > summary").click()
    page.wait_for_selector("#store option", state="attached")
    store_options = page.locator("#store option").all()
    labels = {
        str(item.get_attribute("value") or ""): str(item.text_content() or "").strip()
        for item in store_options
    }
    assert labels.get("501") == "Synthetic Browser Store", labels
    page.select_option("#store", "501")
    expect(page.locator("#filterSummary")).to_contain_text("Synthetic Browser Store")
    expect(page.locator("#filterSummary")).not_to_contain_text("501")

    body = page.locator("body").inner_text()
    for forbidden in (
        legacy.BROWSER_FORBIDDEN_BUYER,
        "synthetic-browser-secret",
        "browser-pass",
        "SELLER-BROWSER-PRIVATE",
    ):
        if forbidden in body:
            raise AssertionError(f"local dashboard leaked forbidden value: {forbidden}")

    page.locator("#adsChart").hover(position={"x": 220, "y": 150})
    expect(page.locator("#adsChart [data-chart-tooltip]")).to_be_visible()
    page.mouse.wheel(0, 500)
    if page.evaluate("window.scrollY") <= 0:
        raise AssertionError("mouse wheel did not scroll the operator dashboard")


if __name__ == "__main__":
    legacy._check_dashboard = _check_operator_dashboard
    legacy.run_page_check()
