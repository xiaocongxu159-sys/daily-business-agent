# -*- coding: utf-8 -*-
"""Regression tests for date-scoped inventory snapshot merging."""
from __future__ import annotations

import pandas as pd

from src.data_merger import merge_inventory_to_daily


def test_inventory_snapshots_match_the_same_store_sku_and_date() -> None:
    daily = pd.DataFrame(
        [
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "日期": "2026-07-23",
                "SKU": "SKU-1",
                "ASIN": "B000TEST1",
            },
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "日期": "2026-07-24",
                "SKU": "SKU-1",
                "ASIN": "B000TEST1",
            },
        ]
    )
    inventory = pd.DataFrame(
        [
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "日期": "2026-07-23",
                "SKU": "SKU-1",
                "ASIN": "B000TEST1",
                "FBA可售库存": 10,
                "FBA在途库存": 2,
                "总库存": 12,
            },
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "日期": "2026-07-24",
                "SKU": "SKU-1",
                "ASIN": "B000TEST1",
                "FBA可售库存": 7,
                "FBA在途库存": 2,
                "总库存": 9,
            },
        ]
    )

    result, issues = merge_inventory_to_daily(daily, inventory)
    by_date = result.set_index("日期")

    assert issues == []
    assert by_date.loc["2026-07-23", "FBA可售库存"] == 10
    assert by_date.loc["2026-07-24", "FBA可售库存"] == 7
    assert by_date.loc["2026-07-23", "总库存"] == 12
    assert by_date.loc["2026-07-24", "总库存"] == 9
    assert set(result["inventory_match_status"]) == {"matched"}
    assert sum(result["FBA可售库存"]) == 17


def test_dated_snapshots_are_not_repeated_on_unmatched_dates() -> None:
    daily = pd.DataFrame(
        [
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "日期": "2026-07-23",
                "SKU": "SKU-1",
                "ASIN": "B000TEST1",
            },
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "日期": "2026-07-24",
                "SKU": "SKU-1",
                "ASIN": "B000TEST1",
            },
        ]
    )
    inventory = pd.DataFrame(
        [
            {
                "shop_id": "STORE-A",
                "marketplace": "US",
                "日期": "2026-07-24",
                "SKU": "SKU-1",
                "ASIN": "B000TEST1",
                "FBA可售库存": 7,
                "总库存": 9,
            }
        ]
    )

    result, issues = merge_inventory_to_daily(daily, inventory)
    by_date = result.set_index("日期")

    assert by_date.loc["2026-07-23", "FBA可售库存"] == 0
    assert by_date.loc["2026-07-24", "FBA可售库存"] == 7
    assert issues == [{"type": "inventory_identity_unmatched", "count": 1}]
