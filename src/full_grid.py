# -*- coding: utf-8 -*-
"""Build and merge complete date/offer grids without crossing store identities.

Exact ``daily_offer_key`` matches are preferred. A legacy key that omits the
identity-source label may be used only when it appears exactly once on both
sides and neither row has an exact primary-key counterpart. This supports a
mapping row identified by MSKU and a report row identified by Seller SKU when
the visible token is the same, without permitting ambiguous cross-merges.
"""
from __future__ import annotations

from itertools import product

import pandas as pd

from src.data_identity import standardize_identity_fields

IDENTITY_COLUMNS = [
    "shop_id",
    "shop_name",
    "marketplace",
    "report_date",
    "日期",
    "parent_asin",
    "asin",
    "ASIN",
    "seller_sku",
    "msku",
    "SKU",
    "store_key",
    "product_key",
    "offer_key",
    "daily_offer_key",
    "daily_asin_key",
    "legacy_offer_key",
    "legacy_daily_offer_key",
    "key_source",
    "offer_identity",
    "identity_status",
]
PRODUCT_COLUMNS = [
    "产品名称",
    "售价",
    "尺寸/规格",
    "库存",
    "配送方式",
    "是否在售",
    "备注",
    "mapping_source",
    "mapping_status",
]
METRIC_COLUMNS = [
    "Sessions",
    "PV",
    "总订单",
    "销售额",
    "业务CVR",
    "广告曝光",
    "广告点击",
    "广告花费",
    "广告订单",
    "广告销售额",
    "ACoS",
    "CTR",
    "CPC",
    "广告CVR",
    "ROAS",
    "TACoS",
]


def _series(frame: pd.DataFrame, column: str, default="") -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series(default, index=frame.index, dtype="object")


def _ensure_sku(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "SKU" not in result.columns:
        if "offer_identity" in result.columns:
            result["SKU"] = result["offer_identity"]
        elif "msku" in result.columns:
            result["SKU"] = result["msku"]
        elif "seller_sku" in result.columns:
            result["SKU"] = result["seller_sku"]
        else:
            result["SKU"] = ""
    result["SKU"] = result["SKU"].fillna("").astype(str).str.strip()
    if "seller_sku" not in result.columns:
        result["seller_sku"] = result["SKU"]
    return result


def _identity_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return standardize_identity_fields(_ensure_sku(frame), log_default=False)


def generate_full_sku_date_grid(mapping_df, business_raw, ad_raw, date_range=None):
    """Return one row per mapping offer and report date, retaining product fields."""
    mapping = _identity_frame(mapping_df)
    dates: set[str] = set()
    for frame in (business_raw, ad_raw):
        if frame is None or frame.empty:
            continue
        values = frame["日期"] if "日期" in frame.columns else _series(frame, "report_date")
        dates.update(str(value).strip() for value in values.dropna() if str(value).strip())
    if date_range:
        start, end = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
        dates.update(pd.date_range(start, end).strftime("%Y-%m-%d"))
    ordered_dates = sorted(dates)
    if mapping.empty or not ordered_dates:
        return pd.DataFrame(), {"dates": len(ordered_dates), "offers": len(mapping)}

    retained = [
        column
        for column in IDENTITY_COLUMNS + PRODUCT_COLUMNS
        if column in mapping.columns and column not in {"report_date", "日期", "daily_offer_key", "daily_asin_key", "legacy_daily_offer_key"}
    ]
    rows = []
    for (_, mapping_row), report_date in product(mapping.iterrows(), ordered_dates):
        row = {column: mapping_row.get(column, "") for column in retained}
        row["日期"] = report_date
        row["report_date"] = report_date
        rows.append(row)
    grid = _identity_frame(pd.DataFrame(rows))
    return grid, {"dates": len(ordered_dates), "offers": len(mapping)}


def _safe_match_keys(grid: pd.DataFrame, daily: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    grid_primary = _series(grid, "daily_offer_key", pd.NA)
    daily_primary = _series(daily, "daily_offer_key", pd.NA)
    grid_legacy = _series(grid, "legacy_daily_offer_key", pd.NA)
    daily_legacy = _series(daily, "legacy_daily_offer_key", pd.NA)

    grid_primary_values = set(grid_primary.dropna().astype(str))
    daily_primary_values = set(daily_primary.dropna().astype(str))
    grid_counts = grid_legacy.dropna().astype(str).value_counts()
    daily_counts = daily_legacy.dropna().astype(str).value_counts()
    safe_legacy = {
        value
        for value in set(grid_counts.index) & set(daily_counts.index)
        if int(grid_counts[value]) == 1 and int(daily_counts[value]) == 1
    }

    grid_keys = pd.Series(
        [f"grid-unmatched:{index}" for index in grid.index],
        index=grid.index,
        dtype="object",
    )
    daily_keys = pd.Series(
        [f"daily-unmatched:{index}" for index in daily.index],
        index=daily.index,
        dtype="object",
    )

    grid_has_primary = grid_primary.notna()
    daily_has_primary = daily_primary.notna()
    grid_keys.loc[grid_has_primary] = "primary:" + grid_primary.loc[grid_has_primary].astype(str)
    daily_keys.loc[daily_has_primary] = "primary:" + daily_primary.loc[daily_has_primary].astype(str)

    grid_fallback = (
        ~grid_primary.astype(str).isin(daily_primary_values)
        & grid_legacy.notna()
        & grid_legacy.astype(str).isin(safe_legacy)
    )
    daily_fallback = (
        ~daily_primary.astype(str).isin(grid_primary_values)
        & daily_legacy.notna()
        & daily_legacy.astype(str).isin(safe_legacy)
    )
    grid_keys.loc[grid_fallback] = "legacy:" + grid_legacy.loc[grid_fallback].astype(str)
    daily_keys.loc[daily_fallback] = "legacy:" + daily_legacy.loc[daily_fallback].astype(str)
    return grid_keys, daily_keys


def _coalesce(result: pd.DataFrame, column: str) -> None:
    grid_column = f"{column}_grid"
    if grid_column not in result.columns:
        return
    if column not in result.columns:
        result[column] = result[grid_column]
    else:
        current = result[column]
        missing = current.isna() | current.astype(str).str.strip().isin({"", "nan", "None"})
        result.loc[missing, column] = result.loc[missing, grid_column]
    result.drop(columns=[grid_column], inplace=True)


def merge_onto_full_grid(full_grid, business_agg, ad_agg, mapping_df=None, inventory_agg=None):
    """Merge daily metrics onto the complete grid with uniqueness-checked fallback."""
    from src.data_merger import merge_business_and_ad, merge_inventory_to_daily

    grid = _identity_frame(full_grid)
    daily = merge_business_and_ad(business_agg, ad_agg, mapping_df)
    if daily is None or daily.empty:
        result = grid.copy()
    else:
        daily = _identity_frame(daily)
        grid_keys, daily_keys = _safe_match_keys(grid, daily)
        grid = grid.assign(_safe_grid_key=grid_keys)
        daily = daily.assign(_safe_grid_key=daily_keys)
        if grid["_safe_grid_key"].duplicated().any():
            raise ValueError("full grid contains ambiguous daily identities")
        if daily["_safe_grid_key"].duplicated().any():
            raise ValueError("daily data contains ambiguous identities")
        result = grid.merge(
            daily,
            on="_safe_grid_key",
            how="left",
            suffixes=("_grid", ""),
            validate="one_to_one",
        )
        for column in IDENTITY_COLUMNS + PRODUCT_COLUMNS:
            _coalesce(result, column)
        result.drop(columns=["_safe_grid_key"], inplace=True, errors="ignore")

    for column in METRIC_COLUMNS:
        if column not in result.columns:
            result[column] = 0.0
        else:
            result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)
    if inventory_agg is not None:
        result, _ = merge_inventory_to_daily(result, inventory_agg, None)
    return result
