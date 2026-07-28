# -*- coding: utf-8 -*-
"""Multi-store-safe business, advertising, inventory, and grid merging."""
from __future__ import annotations

import re
from itertools import product

import numpy as np
import pandas as pd

from src.data_identity import standardize_identity_fields

BUSINESS_METRICS = [
    "Sessions", "Page Views", "Units Ordered", "Ordered Product Sales", "Total Order Items",
]
AD_METRICS = ["Impressions", "Clicks", "Spend", "Orders", "Sales"]
INVENTORY_METRICS = [
    "FBA可售库存", "FBA在途库存", "FBA预留库存", "FBA不可售库存", "FBM库存", "总库存",
]


def _series(dataframe: pd.DataFrame, column: str, default=0.0) -> pd.Series:
    if column in dataframe.columns:
        return dataframe[column]
    return pd.Series(default, index=dataframe.index)


def _numeric(dataframe: pd.DataFrame, column: str, default=0.0) -> pd.Series:
    return pd.to_numeric(_series(dataframe, column, default), errors="coerce").fillna(default)


def _text(dataframe: pd.DataFrame, column: str, default="") -> pd.Series:
    return _series(dataframe, column, default).fillna(default).astype(str)


def _clean_sku_key(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = re.sub(r"[\u200b-\u200f\ufeff\xa0]", "", str(value))
    text = re.sub(r"\s+", "", text).strip()
    return "" if text.lower() in {"", "nan", "none", "null"} else text.upper()


def apply_asin_to_sku_mapping(dataframe, asin_to_sku_dict):
    result = dataframe.copy()
    result["ASIN"] = _text(result, "ASIN").str.strip()
    result["SKU"] = result["ASIN"].map(asin_to_sku_dict or {}).fillna("")
    return result


def _normalize_biz_cols(dataframe):
    if dataframe is None or dataframe.empty:
        return dataframe
    result = dataframe.copy().rename(
        columns={
            "Page Views": "PV",
            "Units Ordered": "总订单",
            "Ordered Product Sales": "销售额",
            "业务转化率": "业务CVR",
        }
    )
    for column in ["Sessions", "PV", "总订单", "销售额", "业务CVR"]:
        if column not in result.columns:
            result[column] = 0.0
    return result


def _normalize_ad_agg_cols(dataframe):
    if dataframe is None or dataframe.empty:
        return dataframe
    result = dataframe.copy().rename(
        columns={
            "Impressions": "广告曝光",
            "Clicks": "广告点击",
            "Spend": "广告花费",
            "Orders": "广告订单",
            "Sales": "广告销售额",
            "ACOS": "ACoS",
            "CVR": "广告CVR",
        }
    )
    for column in [
        "广告曝光", "广告点击", "广告花费", "广告订单", "广告销售额",
        "ACoS", "CTR", "CPC", "广告CVR", "ROAS",
    ]:
        if column not in result.columns:
            result[column] = 0.0
    return result


def _ensure_sku(dataframe: pd.DataFrame) -> pd.DataFrame:
    result = dataframe.copy()
    if "SKU" not in result.columns:
        if "msku" in result.columns:
            result["SKU"] = result["msku"]
        elif "seller_sku" in result.columns:
            result["SKU"] = result["seller_sku"]
        else:
            result["SKU"] = ""
    result["SKU"] = result["SKU"].fillna("").astype(str).str.strip()
    if "seller_sku" not in result.columns:
        result["seller_sku"] = result["SKU"]
    return result


def _row_merge_key(dataframe: pd.DataFrame) -> pd.Series:
    frame = standardize_identity_fields(_ensure_sku(dataframe), log_default=False)
    date = (
        _text(frame, "日期").str.strip()
        if "日期" in frame.columns
        else _text(frame, "report_date").str.strip()
    )
    sku = _text(frame, "SKU").map(_clean_sku_key)
    scoped = _series(frame, "daily_offer_key", pd.NA)
    legacy = "legacy|" + date + "|" + sku
    return scoped.where(scoped.notna(), legacy)


def aggregate_business_by_sku(business_df):
    if business_df is None or business_df.empty:
        return pd.DataFrame()
    frame = standardize_identity_fields(_ensure_sku(business_df), log_default=False)
    frame = frame[frame["SKU"].ne("")].copy()
    if frame.empty:
        return pd.DataFrame()
    for column in BUSINESS_METRICS:
        frame[column] = _numeric(frame, column)
    for column in INVENTORY_METRICS:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["_merge_key"] = _row_merge_key(frame)

    first_columns = [
        "shop_id", "shop_name", "marketplace", "report_date", "日期", "parent_asin",
        "asin", "ASIN", "seller_sku", "msku", "SKU", "store_key", "product_key",
        "offer_key", "daily_offer_key", "daily_asin_key", "key_source", "offer_identity",
        "identity_status",
    ]
    aggregation = {column: "first" for column in first_columns if column in frame.columns}
    aggregation.update({column: "sum" for column in BUSINESS_METRICS})
    for column in INVENTORY_METRICS:
        if column in frame.columns:
            aggregation[column] = lambda values: values.sum(min_count=1)
    grouped = frame.groupby("_merge_key", as_index=False, dropna=False).agg(aggregation)
    grouped["业务转化率"] = np.where(
        grouped["Sessions"].gt(0),
        grouped["Units Ordered"] / grouped["Sessions"] * 100,
        0.0,
    )
    return _normalize_biz_cols(grouped)


def _coalesce(result: pd.DataFrame, column: str) -> None:
    left, right = f"{column}_biz", f"{column}_ad"
    if left in result.columns or right in result.columns:
        left_values = _series(result, left, pd.NA)
        right_values = _series(result, right, pd.NA)
        result[column] = left_values.where(
            left_values.notna() & left_values.astype(str).ne(""),
            right_values,
        )
        result.drop(columns=[left, right], inplace=True, errors="ignore")


def _mapping_info(mapping_df: pd.DataFrame | None) -> tuple[dict, dict]:
    if mapping_df is None or mapping_df.empty:
        return {}, {}
    mapping = standardize_identity_fields(_ensure_sku(mapping_df), log_default=False)
    info_columns = [
        column
        for column in [
            "产品名称", "售价", "尺寸/规格", "库存", "配送方式", "是否在售", "ASIN", "parent_asin",
        ]
        if column in mapping.columns
    ]
    scoped, global_unique = {}, {}
    for offer_key, group in mapping[mapping["offer_key"].notna()].groupby("offer_key"):
        if len(group) == 1:
            scoped[offer_key] = group.iloc[0][info_columns].to_dict()
    for sku, group in mapping[mapping["SKU"].ne("")].groupby("SKU"):
        if len(group) == 1:
            global_unique[_clean_sku_key(sku)] = group.iloc[0][info_columns].to_dict()
    return scoped, global_unique


def _backfill_product_info(dataframe: pd.DataFrame, mapping_df: pd.DataFrame | None) -> pd.DataFrame:
    result = standardize_identity_fields(_ensure_sku(dataframe), log_default=False)
    scoped, global_unique = _mapping_info(mapping_df)
    fields = ["产品名称", "售价", "尺寸/规格", "库存", "配送方式", "是否在售"]
    matched = []
    for index, row in result.iterrows():
        info = scoped.get(row.get("offer_key")) or global_unique.get(
            _clean_sku_key(row.get("SKU"))
        )
        matched.append(bool(info))
        if not info:
            continue
        for field in fields:
            if field not in result.columns:
                result[field] = ""
            current = result.at[index, field]
            if current is None or pd.isna(current) or str(current).strip() == "":
                result.at[index, field] = info.get(field, "")
    result["product_info_match_status"] = np.where(matched, "matched", "unmatched")
    return result


def merge_business_and_ad(business_agg, ad_agg, mapping_df=None):
    business = _normalize_biz_cols(business_agg) if business_agg is not None else pd.DataFrame()
    advertising = _normalize_ad_agg_cols(ad_agg) if ad_agg is not None else pd.DataFrame()
    if business is None or business.empty:
        result = advertising.copy()
        if result.empty:
            return pd.DataFrame()
        result["_merge_key"] = _row_merge_key(result)
    elif advertising is None or advertising.empty:
        result = business.copy()
        result["_merge_key"] = _row_merge_key(result)
    else:
        business = business.copy()
        advertising = advertising.copy()
        business["_merge_key"] = _row_merge_key(business)
        advertising["_merge_key"] = _row_merge_key(advertising)
        result = business.merge(
            advertising,
            on="_merge_key",
            how="outer",
            suffixes=("_biz", "_ad"),
            validate="one_to_one",
        )
        for column in [
            "shop_id", "shop_name", "marketplace", "report_date", "日期", "parent_asin",
            "asin", "ASIN", "seller_sku", "msku", "SKU", "store_key", "product_key",
            "offer_key", "daily_offer_key", "daily_asin_key", "key_source", "offer_identity",
            "identity_status",
        ]:
            _coalesce(result, column)

    for column in ["Sessions", "PV", "总订单", "销售额", "业务CVR"]:
        result[column] = _numeric(result, column)
    for column in [
        "广告曝光", "广告点击", "广告花费", "广告订单", "广告销售额",
        "ACoS", "CTR", "CPC", "广告CVR", "ROAS",
    ]:
        result[column] = _numeric(result, column)
    result["TACoS"] = np.where(
        result["销售额"].gt(0), result["广告花费"] / result["销售额"], 0.0
    )
    result["总销售额含广告"] = result["销售额"]
    result["总订单含广告"] = result["总订单"]
    result = _backfill_product_info(result, mapping_df)
    return result.drop(columns=["_merge_key"], errors="ignore")


def generate_full_sku_date_grid(mapping_df, business_raw, ad_raw, date_range=None):
    mapping = standardize_identity_fields(_ensure_sku(mapping_df), log_default=False)
    dates = set()
    for frame in [business_raw, ad_raw]:
        if frame is not None and not frame.empty:
            source = (
                frame["日期"]
                if "日期" in frame.columns
                else _series(frame, "report_date", "")
            )
            dates.update(str(value) for value in source.dropna() if str(value).strip())
    if date_range:
        start, end = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
        dates.update(pd.date_range(start, end).strftime("%Y-%m-%d"))
    dates = sorted(dates)
    if mapping.empty or not dates:
        return pd.DataFrame(), {"dates": len(dates), "offers": len(mapping)}

    rows = []
    identity_columns = [
        "shop_id", "shop_name", "marketplace", "parent_asin", "asin", "ASIN",
        "seller_sku", "msku", "SKU", "store_key", "product_key", "offer_key",
        "key_source", "offer_identity", "identity_status",
    ]
    for (_, row), date in product(mapping.iterrows(), dates):
        item = {column: row.get(column, "") for column in identity_columns}
        item["日期"] = date
        item["report_date"] = date
        rows.append(item)
    grid = standardize_identity_fields(pd.DataFrame(rows), log_default=False)
    return grid, {"dates": len(dates), "offers": len(mapping)}


def merge_onto_full_grid(full_grid, business_agg, ad_agg, mapping_df=None, inventory_agg=None):
    grid = standardize_identity_fields(_ensure_sku(full_grid), log_default=False)
    grid["_merge_key"] = _row_merge_key(grid)
    daily = merge_business_and_ad(business_agg, ad_agg, mapping_df)
    if daily.empty:
        result = grid.copy()
    else:
        daily = daily.copy()
        daily["_merge_key"] = _row_merge_key(daily)
        result = grid.merge(daily, on="_merge_key", how="left", suffixes=("_grid", ""))
        for column in [
            "shop_id", "shop_name", "marketplace", "report_date", "日期", "parent_asin",
            "asin", "ASIN", "seller_sku", "msku", "SKU", "store_key", "product_key",
            "offer_key", "daily_offer_key", "daily_asin_key", "key_source", "offer_identity",
            "identity_status",
        ]:
            grid_column = f"{column}_grid"
            if grid_column in result.columns:
                if column not in result.columns:
                    result[column] = result[grid_column]
                else:
                    result[column] = result[column].where(
                        result[column].notna() & result[column].astype(str).ne(""),
                        result[grid_column],
                    )
                result.drop(columns=[grid_column], inplace=True)
    for column in [
        "Sessions", "PV", "总订单", "销售额", "业务CVR", "广告曝光", "广告点击",
        "广告花费", "广告订单", "广告销售额", "ACoS", "CTR", "CPC", "广告CVR", "ROAS", "TACoS",
    ]:
        result[column] = _numeric(result, column)
    result = result.drop(columns=["_merge_key"], errors="ignore")
    if inventory_agg is not None:
        result, _ = merge_inventory_to_daily(result, inventory_agg, None)
    return result


def _activity_masks(dataframe: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    business = (
        _numeric(dataframe, "Sessions").gt(0)
        | _numeric(dataframe, "销售额").gt(0)
        | _numeric(dataframe, "总订单").gt(0)
    )
    advertising = (
        _numeric(dataframe, "广告曝光").gt(0)
        | _numeric(dataframe, "广告花费").gt(0)
        | _numeric(dataframe, "广告销售额").gt(0)
    )
    return business, advertising


def find_ad_only_rows(dataframe):
    if dataframe is None or dataframe.empty:
        return pd.DataFrame()
    business, advertising = _activity_masks(dataframe)
    return dataframe[advertising & ~business].copy()


def find_biz_only_rows(dataframe):
    if dataframe is None or dataframe.empty:
        return pd.DataFrame()
    business, advertising = _activity_masks(dataframe)
    return dataframe[business & ~advertising].copy()


def check_biz_order_no_sales(dataframe):
    if dataframe is None or dataframe.empty:
        return pd.DataFrame()
    orders = (
        _numeric(dataframe, "总订单")
        if "总订单" in dataframe.columns
        else _numeric(dataframe, "Units Ordered")
    )
    sales = (
        _numeric(dataframe, "销售额")
        if "销售额" in dataframe.columns
        else _numeric(dataframe, "Ordered Product Sales")
    )
    return dataframe[orders.gt(0) & sales.le(0)].copy()


def check_ad_order_no_sales(dataframe):
    if dataframe is None or dataframe.empty:
        return pd.DataFrame()
    orders = (
        _numeric(dataframe, "广告订单")
        if "广告订单" in dataframe.columns
        else _numeric(dataframe, "Orders")
    )
    sales = (
        _numeric(dataframe, "广告销售额")
        if "广告销售额" in dataframe.columns
        else _numeric(dataframe, "Sales")
    )
    return dataframe[orders.gt(0) & sales.le(0)].copy()


def check_duplicate_rows(dataframe):
    if dataframe is None or dataframe.empty:
        return pd.DataFrame()
    keys = _row_merge_key(dataframe)
    return dataframe[keys.duplicated(keep=False)].copy()


def merge_inventory_to_daily(daily_sku, inventory_agg, inventory_detail=None):
    if daily_sku is None:
        daily_sku = pd.DataFrame()
    result = standardize_identity_fields(_ensure_sku(daily_sku), log_default=False)
    issues = []
    if inventory_agg is None or inventory_agg.empty:
        for column in INVENTORY_METRICS:
            result[column] = _numeric(result, column)
        result["inventory_match_status"] = "source_not_available"
        result["inventory_source_present"] = False
        return result, issues

    inventory = standardize_identity_fields(_ensure_sku(inventory_agg), log_default=False)
    for column in INVENTORY_METRICS:
        inventory[column] = _numeric(inventory, column)

    daily_keys = _series(inventory, "daily_offer_key", pd.NA)
    dated_mask = daily_keys.notna() & daily_keys.astype(str).str.strip().ne("")
    dated_inventory = inventory.loc[dated_mask].copy()
    undated_inventory = inventory.loc[~dated_mask].copy()

    daily_values = {}
    for daily_offer_key, group in dated_inventory.groupby("daily_offer_key"):
        daily_values[daily_offer_key] = group[INVENTORY_METRICS].sum().to_dict()

    offer_values = {}
    if not undated_inventory.empty:
        scoped = undated_inventory[undated_inventory["offer_key"].notna()]
        for offer_key, group in scoped.groupby("offer_key"):
            offer_values[offer_key] = group[INVENTORY_METRICS].sum().to_dict()

    legacy_values = {}
    if not undated_inventory.empty:
        for sku, group in undated_inventory[undated_inventory["SKU"].ne("")].groupby("SKU"):
            if group["store_key"].nunique(dropna=True) <= 1:
                legacy_values[_clean_sku_key(sku)] = group[INVENTORY_METRICS].sum().to_dict()

    statuses = []
    for index, row in result.iterrows():
        values = daily_values.get(row.get("daily_offer_key"))
        if values is None:
            values = offer_values.get(row.get("offer_key")) or legacy_values.get(
                _clean_sku_key(row.get("SKU"))
            )
        if values is None:
            statuses.append("identity_unmatched")
            for column in INVENTORY_METRICS:
                result.at[index, column] = 0.0
        else:
            nonzero = any(float(values.get(column, 0)) != 0 for column in INVENTORY_METRICS)
            statuses.append("matched" if nonzero else "zero_inventory")
            for column in INVENTORY_METRICS:
                result.at[index, column] = values.get(column, 0.0)
    result["inventory_match_status"] = statuses
    result["inventory_source_present"] = True
    unmatched = sum(status == "identity_unmatched" for status in statuses)
    if unmatched:
        issues.append({"type": "inventory_identity_unmatched", "count": unmatched})
    return result, issues
