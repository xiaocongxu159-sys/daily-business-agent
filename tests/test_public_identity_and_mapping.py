# -*- coding: utf-8 -*-
"""Synthetic tests for public report identification and multi-store mapping."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import settings
from src.data_identity import standardize_identity_fields
from src.date_parser import extract_date_from_filename, normalize_date
from src.product_mapping_loader import load_mapping
from src.report_detector import detect_report_type


def test_public_defaults_do_not_ship_store_identity() -> None:
    assert settings.DEFAULT_SHOP_ID == ""
    assert settings.DEFAULT_SHOP_NAME == ""
    assert settings.DEFAULT_MARKETPLACE == ""
    assert settings.INVENTORY_MIGRATION_BASELINE_FILE == ""


def test_missing_store_identity_remains_pending() -> None:
    frame = pd.DataFrame(
        [{"seller_sku": "SKU-1", "asin": "B000TEST1", "report_date": "2026-07-24"}]
    )
    result = standardize_identity_fields(frame)

    assert result.loc[0, "shop_id"] == ""
    assert result.loc[0, "marketplace"] == ""
    assert result.loc[0, "identity_status"] == "pending_confirmation"
    assert pd.isna(result.loc[0, "store_key"])


def test_explicit_identity_builds_store_and_offer_keys() -> None:
    frame = pd.DataFrame(
        [
            {
                "shop_id": "STORE-A",
                "shop_name": "Synthetic Store",
                "marketplace": "US",
                "report_date": "2026-07-24",
                "asin": "B000TEST1",
                "seller_sku": "SKU-1",
            }
        ]
    )
    result = standardize_identity_fields(frame)

    assert result.loc[0, "store_key"] == "STORE-A|US"
    assert result.loc[0, "offer_key"] == "STORE-A|US|seller_sku|SKU-1"
    assert result.loc[0, "identity_status"] == "confirmed"


def test_mapping_loader_preserves_user_store_identity(tmp_path: Path) -> None:
    path = tmp_path / "mapping.csv"
    path.write_text(
        "shop_id,shop_name,marketplace,parent_asin,seller-sku,MSKU,asin1,item-name\n"
        "STORE-A,Synthetic Store,US,B0PARENT1,SKU-1,MSKU-1,B000TEST1,Synthetic Product\n",
        encoding="utf-8",
    )

    mapping, errors = load_mapping(path)

    assert errors == []
    assert len(mapping) == 1
    assert mapping.loc[0, "shop_id"] == "STORE-A"
    assert mapping.loc[0, "marketplace"] == "US"
    assert mapping.loc[0, "parent_asin"] == "B0PARENT1"
    assert mapping.loc[0, "msku"] == "MSKU-1"
    assert mapping.loc[0, "mapping_status"] == "confirmed"


def test_same_sku_in_two_stores_is_not_deduplicated(tmp_path: Path) -> None:
    path = tmp_path / "mapping.csv"
    path.write_text(
        "shop_id,marketplace,seller-sku,asin1\n"
        "STORE-A,US,SKU-1,B000TEST1\n"
        "STORE-B,CA,SKU-1,B000TEST1\n",
        encoding="utf-8",
    )

    mapping, errors = load_mapping(path)

    assert errors == []
    assert len(mapping) == 2
    assert set(mapping["store_key"]) == {"STORE-A|US", "STORE-B|CA"}


def test_report_type_detection_uses_only_synthetic_headers(tmp_path: Path) -> None:
    business = tmp_path / "business.csv"
    business.write_text(
        "ASIN,Sessions,Page Views,Units Ordered,Ordered Product Sales\n"
        "B000TEST1,1,2,1,9.99\n",
        encoding="utf-8",
    )
    advertising = tmp_path / "advertising.csv"
    advertising.write_text(
        "ASIN,Impressions,Clicks,Spend,ACOS\n"
        "B000TEST1,100,5,2.50,25%\n",
        encoding="utf-8",
    )
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "seller-sku,asin1,item-name,quantity\nSKU-1,B000TEST1,Synthetic,1\n",
        encoding="utf-8",
    )

    assert detect_report_type(business) == "business"
    assert detect_report_type(advertising) == "ad"
    assert detect_report_type(mapping) == "mapping"


def test_date_parser_rejects_invalid_calendar_dates() -> None:
    assert extract_date_from_filename("business_20260724.csv") == "2026-07-24"
    assert extract_date_from_filename("business_31-02-26.csv") is None
    assert normalize_date("2026/07/24") == "2026-07-24"
