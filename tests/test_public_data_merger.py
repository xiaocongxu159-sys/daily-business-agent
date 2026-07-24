# -*- coding: utf-8 -*-
"""Synthetic tests for multi-store-safe data merging."""
from __future__ import annotations

import pandas as pd

from src.data_merger import (
    aggregate_business_by_sku,
    check_duplicate_rows,
    find_ad_only_rows,
    find_biz_only_rows,
    merge_business_and_ad,
    merge_inventory_to_daily,
)
from src.full_grid import generate_full_sku_date_grid, merge_onto_full_grid


def business_row(store: str, marketplace: str, sku: str, asin: str, sales: float) -> dict:
    return {
        "shop_id": store,
        "marketplace": marketplace,
        "日期": "2026-07-24",
        "SKU": sku,
        "ASIN": asin,
        "Sessions": 10,
        "Page Views": 12,
        "Units Ordered": 2,
        "Ordered Product Sales": sales,
        "Total Order Items": 2,
    }


def ad_row(store: str, marketplace: str, sku: str, asin: str, spend: float) -> dict:
    return {
        "shop_id": store,
        "marketplace": marketplace,
        "日期": "2026-07-24",
        "SKU": sku,
        "ASIN": asin,
        "Impressions": 100,
        "Clicks": 10,
        "Spend": spend,
        "Orders": 1,
        "Sales": 10,
        "ACOS": spend / 10,
    }


def test_business_aggregation_keeps_same_sku_in_two_stores() -> None:
    frame = pd.DataFrame(
        [
            business_row("STORE-A", "US", "SKU-1", "B000TEST1", 20),
            business_row("STORE-B", "CA", "SKU-1", "B000TEST1", 30),
        ]
    )
    result = aggregate_business_by_sku(frame)

    assert len(result) == 2
    assert set(result["store_key"]) == {"STORE-A|US", "STORE-B|CA"}


def test_business_and_ad_merge_does_not_cross_stores() -> None:
    business = aggregate_business_by_sku(
        pd.DataFrame(
            [
                business_row("STORE-A", "US", "SKU-1", "B000TEST1", 20),
                business_row("STORE-B", "CA", "SKU-1", "B000TEST1", 30),
            ]
        )
    )
    advertising = pd.DataFrame(
        [
            ad_row("STORE-A", "US", "SKU-1", "B000TEST1", 5),
            ad_row("STORE-B", "CA", "SKU-1", "B000TEST1", 8),
        ]
    )
    result = merge_business_and_ad(business, advertising)

    assert len(result) == 2
    by_store = result.set_index("shop_id")
    assert by_store.loc["STORE-A", "广告花费"] == 5
    assert by_store.loc["STORE-B", "广告花费"] == 8


def test_legacy_single_store_rows_merge_by_date_and_sku() -> None:
    business = aggregate_business_by_sku(
        pd.DataFrame([business_row("", "", "SKU-1", "B000TEST1", 20)])
    )
    advertising = pd.DataFrame([ad_row("", "", "SKU-1", "B000TEST1", 5)])
    result = merge_business_and_ad(business, advertising)

    assert len(result) == 1
    assert result.loc[0, "销售额"] == 20
    assert result.loc[0, "广告花费"] == 5


def test_full_grid_keeps_each_store_offer_for_each_date() -> None:
    mapping = pd.DataFrame(
        [
            {"shop_id": "STORE-A", "marketplace": "US", "SKU": "SKU-1", "ASIN": "B000TEST1"},
            {"shop_id": "STORE-B", "marketplace": "CA", "SKU": "SKU-1", "ASIN": "B000TEST1"},
        ]
    )
    business = pd.DataFrame(
        [
            {**business_row("STORE-A", "US", "SKU-1", "B000TEST1", 20), "日期": "2026-07-23"},
            business_row("STORE-B", "CA", "SKU-1", "B000TEST1", 30),
        ]
    )
    grid, metadata = generate_full_sku_date_grid(mapping, business, None)

    assert metadata == {"dates": 2, "offers": 2}
    assert len(grid) == 4
    assert grid["daily_offer_key"].nunique() == 4


def test_grid_merge_fills_missing_metrics_with_zero() -> None:
    mapping = pd.DataFrame(
        [{"shop_id": "STORE-A", "marketplace": "US", "SKU": "SKU-1", "ASIN": "B000TEST1"}]
    )
    raw = pd.DataFrame([business_row("STORE-A", "US", "SKU-1", "B000TEST1", 20)])
    business = aggregate_business_by_sku(raw)
    grid, _ = generate_full_sku_date_grid(mapping, raw, None, ("2026-07-23", "2026-07-24"))
    result = merge_onto_full_grid(grid, business, pd.DataFrame(), mapping)

    assert len(result) == 2
    assert sorted(result["销售额"]) == [0.0, 20.0]


def test_full_grid_uniquely_falls_back_between_msku_and_seller_sku() -> None:
    mapping = pd.DataFrame(
        [{
            "shop_id": "STORE-A",
            "marketplace": "US",
            "SKU": "SKU-1",
            "msku": "SKU-1",
            "seller_sku": "",
            "ASIN": "B000TEST1",
            "产品名称": "Mapped Product",
        }]
    )
    business = pd.DataFrame(
        [{
            "shop_id": "STORE-A",
            "marketplace": "US",
            "日期": "2026-07-24",
            "report_date": "2026-07-24",
            "SKU": "SKU-1",
            "msku": "",
            "seller_sku": "SKU-1",
            "ASIN": "B000TEST1",
            "Sessions": 10,
            "PV": 12,
            "总订单": 2,
            "销售额": 20,
            "业务CVR": 0.2,
        }]
    )

    grid, _ = generate_full_sku_date_grid(mapping, business, None)
    result = merge_onto_full_grid(grid, business, pd.DataFrame(), mapping)

    assert len(result) == 1
    assert result.loc[0, "销售额"] == 20
    assert result.loc[0, "产品名称"] == "Mapped Product"
    assert result.loc[0, "key_source"] == "seller_sku"


def test_full_grid_does_not_use_ambiguous_legacy_fallback() -> None:
    mapping = pd.DataFrame(
        [
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "SKU": "SKU-1",
                "msku": "SKU-1",
                "seller_sku": "",
                "ASIN": "B000TEST1",
                "产品名称": "MSKU Product",
            },
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "SKU": "SKU-1",
                "msku": "",
                "seller_sku": "SKU-1",
                "ASIN": "B000TEST2",
                "产品名称": "Seller Product",
            },
        ]
    )
    business = pd.DataFrame(
        [{
            "shop_id": "STORE-A",
            "marketplace": "US",
            "日期": "2026-07-24",
            "report_date": "2026-07-24",
            "SKU": "SKU-1",
            "msku": "",
            "seller_sku": "SKU-1",
            "ASIN": "B000TEST2",
            "Sessions": 10,
            "PV": 12,
            "总订单": 2,
            "销售额": 20,
            "业务CVR": 0.2,
        }]
    )

    grid, _ = generate_full_sku_date_grid(mapping, business, None)
    result = merge_onto_full_grid(grid, business, pd.DataFrame(), mapping)
    by_name = result.set_index("产品名称")

    assert by_name.loc["MSKU Product", "销售额"] == 0
    assert by_name.loc["Seller Product", "销售额"] == 20


def test_quality_helpers_identify_one_sided_and_duplicate_rows() -> None:
    frame = pd.DataFrame(
        [
            {**business_row("STORE-A", "US", "SKU-1", "B000TEST1", 20), "广告花费": 0, "广告销售额": 0, "广告曝光": 0},
            {"shop_id": "STORE-A", "marketplace": "US", "日期": "2026-07-24", "SKU": "SKU-2", "ASIN": "B000TEST2", "Sessions": 0, "销售额": 0, "总订单": 0, "广告花费": 5, "广告销售额": 10, "广告曝光": 100},
        ]
    )
    assert len(find_biz_only_rows(frame)) == 1
    assert len(find_ad_only_rows(frame)) == 1
    duplicated = pd.concat([frame.iloc[[0]], frame.iloc[[0]]], ignore_index=True)
    assert len(check_duplicate_rows(duplicated)) == 2


def test_inventory_merge_uses_store_scoped_offer_key() -> None:
    daily = pd.DataFrame(
        [
            business_row("STORE-A", "US", "SKU-1", "B000TEST1", 20),
            business_row("STORE-B", "CA", "SKU-1", "B000TEST1", 30),
        ]
    )
    inventory = pd.DataFrame(
        [
            {"shop_id": "STORE-A", "marketplace": "US", "SKU": "SKU-1", "ASIN": "B000TEST1", "FBA可售库存": 10},
            {"shop_id": "STORE-B", "marketplace": "CA", "SKU": "SKU-1", "ASIN": "B000TEST1", "FBA可售库存": 20},
        ]
    )
    result, issues = merge_inventory_to_daily(daily, inventory)

    assert issues == []
    values = result.set_index("shop_id")["FBA可售库存"].to_dict()
    assert values == {"STORE-A": 10, "STORE-B": 20}
