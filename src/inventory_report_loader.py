# -*- coding: utf-8 -*-
"""Load explicitly selected inventory reports with store-scoped identities."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.data_identity import standardize_identity_fields

INVENTORY_COLUMNS = [
    "FBA可售库存", "FBA在途库存", "FBA预留库存", "FBA不可售库存", "FBM库存", "总库存",
]
ALIASES = {
    "shop_id": ["shop_id", "店铺ID", "sid"],
    "shop_name": ["shop_name", "店铺", "店铺名称"],
    "marketplace": ["marketplace", "站点", "国家"],
    "msku": ["MSKU", "msku", "merchant sku", "商家SKU"],
    "seller_sku": ["seller_sku", "seller-sku", "Seller SKU", "SKU", "卖家SKU", "系统SKU"],
    "ASIN": ["ASIN", "asin", "asin1", "商品ASIN"],
    "产品名称": ["Product Name", "item-name", "title", "产品名称", "商品名称", "标题"],
    "FBA可售库存": ["FBA可售库存", "fba_available", "available", "fulfillable quantity", "afn-fulfillable-quantity", "FBA可售", "可售库存"],
    "FBA在途库存": ["FBA在途库存", "fba_in_transit", "in transit", "inbound", "inbound quantity", "FBA在途", "在途库存"],
    "FBA预留库存": ["FBA预留库存", "fba_reserved", "reserved", "reserved quantity", "afn-reserved-quantity", "预留库存"],
    "FBA不可售库存": ["FBA不可售库存", "fba_unsellable", "unsellable", "unfulfillable quantity", "afn-unsellable-quantity", "不可售库存"],
    "FBM库存": ["FBM库存", "fbm_inventory", "merchant-quantity", "mfn-fulfillable-quantity", "本地库存", "自发货库存"],
    "总库存": ["总库存", "total_inventory", "库存合计", "总计库存"],
}


def _norm(value) -> str:
    return re.sub(r"[\s_\-/　]", "", str(value).strip().lower())


def _find(dataframe: pd.DataFrame, aliases) -> str | None:
    columns = {column: _norm(column) for column in dataframe.columns}
    for alias in aliases:
        key = _norm(alias)
        for column, normalized in columns.items():
            if normalized == key:
                return column
    for alias in aliases:
        key = _norm(alias)
        for column, normalized in columns.items():
            if key and key in normalized:
                return column
    return None


def _text(dataframe: pd.DataFrame, aliases) -> pd.Series:
    column = _find(dataframe, aliases)
    if column is None:
        return pd.Series("", index=dataframe.index)
    return dataframe[column].fillna("").astype(str).str.strip()


def _number(dataframe: pd.DataFrame, aliases) -> pd.Series:
    column = _find(dataframe, aliases)
    if column is None:
        return pd.Series(0.0, index=dataframe.index)
    text = (
        dataframe[column].fillna("").astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("US$", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(text, errors="coerce").fillna(0.0)


def _date_from_filename(path: Path) -> str:
    for pattern, format_string in [
        (r"(\d{8})", "%Y%m%d"),
        (r"(\d{4}-\d{1,2}-\d{1,2})", None),
    ]:
        match = re.search(pattern, path.stem)
        if match:
            value = pd.to_datetime(match.group(1), format=format_string, errors="coerce")
            if pd.notna(value):
                return value.strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def _read(path: Path) -> pd.DataFrame | None:
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=object)
    if path.suffix.lower() in {".csv", ".tsv", ".txt"}:
        separator = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
        return pd.read_csv(path, sep=separator, dtype=object)
    return None


def _unique_asin_mapping(mapping_df) -> dict:
    if mapping_df is None or mapping_df.empty:
        return {}
    mapping = standardize_identity_fields(mapping_df, log_default=False)
    mapping["SKU"] = mapping.get("SKU", mapping.get("offer_identity", "")).astype(str).str.strip()
    result = {}
    for asin, group in mapping[mapping["asin"].ne("")].groupby("asin"):
        values = set(group["SKU"]) - {""}
        if len(values) == 1:
            result[asin] = next(iter(values))
    return result


def load_inventory_report(filepath, asin_to_sku=None, mapping_df=None):
    path = Path(filepath)
    try:
        raw = _read(path)
    except Exception:
        return None
    if raw is None or raw.empty:
        return None
    raw.columns = [str(column).strip() for column in raw.columns]

    result = pd.DataFrame(index=raw.index)
    result["来源文件"] = path.name
    result["识别日期"] = _date_from_filename(path)
    for field in ["shop_id", "shop_name", "marketplace", "msku", "seller_sku", "ASIN", "产品名称"]:
        result[field] = _text(raw, ALIASES[field])
    result["SKU"] = result["msku"].where(result["msku"].ne(""), result["seller_sku"])
    if result["SKU"].eq("").all() and result["ASIN"].ne("").any():
        mapping = dict(asin_to_sku or {}) or _unique_asin_mapping(mapping_df)
        result["SKU"] = result["ASIN"].map(mapping).fillna("")
        result["seller_sku"] = result["SKU"]
    for field in INVENTORY_COLUMNS:
        result[field] = _number(raw, ALIASES[field])
    calculated = result[INVENTORY_COLUMNS[:-1]].sum(axis=1)
    result["总库存"] = result["总库存"].where(result["总库存"].gt(0), calculated)

    identity = result.copy()
    identity["日期"] = result["识别日期"]
    identity["asin"] = result["ASIN"]
    identity = standardize_identity_fields(identity)
    for column in identity.columns:
        if column not in result.columns or column in {
            "shop_id", "shop_name", "marketplace", "report_date", "parent_asin", "asin",
            "seller_sku", "msku", "store_key", "product_key", "offer_key", "daily_offer_key",
            "daily_asin_key", "key_source", "offer_identity", "identity_status",
            "default_shop_id_used", "default_marketplace_used", "legacy_offer_key",
            "legacy_daily_offer_key",
        }:
            result[column] = identity[column]
    result["inventory_match_status"] = result["identity_status"].map(
        {"confirmed": "ready", "configured_default": "configured_default"}
    ).fillna("pending_confirmation")
    valid = result["offer_identity"].ne("") | result["asin"].ne("")
    return result.loc[valid].reset_index(drop=True)


def aggregate_inventory(inventory_dfs):
    if not inventory_dfs:
        return pd.DataFrame(), pd.DataFrame()
    detail = pd.concat(inventory_dfs, ignore_index=True, sort=False)
    if detail.empty:
        return pd.DataFrame(), detail
    detail = standardize_identity_fields(detail, use_config_defaults=False, log_default=False)
    detail["SKU"] = detail["offer_identity"]
    for column in INVENTORY_COLUMNS:
        detail[column] = pd.to_numeric(detail.get(column, 0), errors="coerce").fillna(0.0)
    legacy = "legacy|" + detail["report_date"] + "|" + detail["SKU"].str.upper()
    detail["_inventory_key"] = detail["daily_offer_key"].where(
        detail["daily_offer_key"].notna(), legacy
    )
    first_columns = [
        "来源文件", "识别日期", "SKU", "ASIN", "产品名称", "shop_id", "shop_name",
        "marketplace", "report_date", "parent_asin", "asin", "seller_sku", "msku", "store_key",
        "product_key", "offer_key", "daily_offer_key", "daily_asin_key", "key_source",
        "offer_identity", "identity_status", "inventory_match_status",
    ]
    aggregation = {column: "first" for column in first_columns if column in detail.columns}
    aggregation.update({column: "sum" for column in INVENTORY_COLUMNS})
    aggregate = detail.groupby("_inventory_key", as_index=False, dropna=False).agg(aggregation)
    return aggregate.drop(columns=["_inventory_key"], errors="ignore"), detail
