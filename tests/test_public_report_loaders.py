# -*- coding: utf-8 -*-
"""Synthetic tests for public business and advertising report loaders."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.ad_report_loader import load_ad_report, load_all_ad_reports, process_ad_for_merge
from src.business_report_loader import load_all_business_reports, load_business_report


def test_business_reports_keep_same_asin_from_two_stores(tmp_path: Path) -> None:
    first = tmp_path / "business_a_2026-07-24.csv"
    second = tmp_path / "business_b_2026-07-24.csv"
    header = "shop_id,marketplace,ASIN,Sessions,Page Views,Units Ordered,Ordered Product Sales,Total Order Items\n"
    first.write_text(header + "STORE-A,US,B000TEST1,10,12,2,19.98,2\n", encoding="utf-8")
    second.write_text(header + "STORE-B,CA,B000TEST1,20,24,3,29.97,3\n", encoding="utf-8")

    combined, errors = load_all_business_reports([first, second])

    assert errors == []
    assert len(combined) == 2
    assert set(combined["store_key"]) == {"STORE-A|US", "STORE-B|CA"}


def test_business_mapping_uses_store_scope_when_asin_is_ambiguous(tmp_path: Path) -> None:
    report = tmp_path / "business_2026-07-24.csv"
    report.write_text(
        "shop_id,marketplace,ASIN,Sessions,Ordered Product Sales\n"
        "STORE-A,US,B000TEST1,1,9.99\n"
        "STORE-B,CA,B000TEST1,1,8.99\n",
        encoding="utf-8",
    )
    mapping = pd.DataFrame(
        [
            {"shop_id": "STORE-A", "marketplace": "US", "ASIN": "B000TEST1", "SKU": "SKU-US"},
            {"shop_id": "STORE-B", "marketplace": "CA", "ASIN": "B000TEST1", "SKU": "SKU-CA"},
        ]
    )

    combined, errors = load_all_business_reports([report], mapping)

    assert errors == []
    assert list(combined.sort_values("shop_id")["SKU"]) == ["SKU-US", "SKU-CA"]
    assert combined["offer_key"].nunique() == 2


def test_business_loader_rejects_invalid_date(tmp_path: Path) -> None:
    report = tmp_path / "business.csv"
    report.write_text(
        "Date,ASIN,Sessions,Ordered Product Sales\n"
        "2026-02-31,B000TEST1,1,9.99\n",
        encoding="utf-8",
    )

    dataframe, errors = load_business_report(report)

    assert dataframe is None
    assert any("日期" in error for error in errors)


def test_ad_loader_keeps_two_products_in_same_campaign(tmp_path: Path) -> None:
    report = tmp_path / "advertising.csv"
    report.write_text(
        "Date,Campaign,Ad Group,Advertised ASIN,Advertised SKU,Impressions,Clicks,Spend,Orders,Sales\n"
        "2026-07-24,Campaign A,Group A,B000TEST1,SKU-1,100,10,5,1,10\n"
        "2026-07-24,Campaign A,Group A,B000TEST2,SKU-2,200,20,8,2,20\n",
        encoding="utf-8",
    )

    dataframe, errors = load_all_ad_reports([report])

    assert dataframe is not None
    assert len(dataframe) == 2
    assert errors == []
    assert set(dataframe["ASIN"]) == {"B000TEST1", "B000TEST2"}


def test_ad_loader_rejects_range_without_daily_date(tmp_path: Path) -> None:
    report = tmp_path / "advertising_20260701-20260724.csv"
    report.write_text(
        "Campaign,Impressions,Clicks,Spend\nCampaign A,100,10,5\n",
        encoding="utf-8",
    )

    dataframe, errors = load_ad_report(report)

    assert dataframe is None
    assert any("范围" in error for error in errors)


def test_ad_aggregation_keeps_store_identity(tmp_path: Path) -> None:
    report = tmp_path / "advertising.csv"
    report.write_text(
        "shop_id,marketplace,Date,Campaign,Advertised ASIN,Advertised SKU,Impressions,Clicks,Spend,Orders,Sales\n"
        "STORE-A,US,2026-07-24,Campaign A,B000TEST1,SKU-1,100,10,5,1,10\n"
        "STORE-B,CA,2026-07-24,Campaign A,B000TEST1,SKU-1,200,20,8,2,20\n",
        encoding="utf-8",
    )
    raw, errors = load_ad_report(report)
    aggregate, _, _, _ = process_ad_for_merge(raw)

    assert errors == []
    assert len(aggregate) == 2
    assert set(aggregate["store_key"]) == {"STORE-A|US", "STORE-B|CA"}
