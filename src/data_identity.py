# -*- coding: utf-8 -*-
"""Canonical multi-store identity fields, compound keys, and variation links."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

import pandas as pd

from config.settings import DEFAULT_MARKETPLACE, DEFAULT_SHOP_ID, DEFAULT_SHOP_NAME

logger = logging.getLogger(__name__)

STANDARD_IDENTITY_COLUMNS = [
    "shop_id", "shop_name", "marketplace", "report_date", "parent_asin",
    "asin", "seller_sku", "msku",
]
COMPOUND_KEY_COLUMNS = [
    "store_key", "product_key", "offer_key", "daily_offer_key", "daily_asin_key", "key_source",
    "legacy_offer_key", "legacy_daily_offer_key", "offer_identity",
    "default_shop_id_used", "default_marketplace_used", "identity_status",
]
RELATIONSHIP_COLUMNS = [
    "shop_id", "marketplace", "parent_asin", "child_asin", "msku", "seller_sku",
    "relationship_source", "relationship_status", "last_updated_at",
]

ALIASES = {
    "shop_id": ("shop_id", "店铺ID", "店铺 Id", "sid"),
    "shop_name": ("shop_name", "店铺", "店铺名称"),
    "marketplace": ("marketplace", "站点", "国家", "marketplace_id"),
    "report_date": ("report_date", "日期", "Date"),
    "parent_asin": ("parent_asin", "父ASIN", "Parent ASIN"),
    "asin": ("asin", "ASIN", "子ASIN", "Child ASIN"),
    "seller_sku": ("seller_sku", "ERP内部SKU", "seller-sku", "Seller SKU", "SKU"),
    "msku": ("msku", "MSKU", "SKU"),
}


def _clean(value) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).replace("\u200b", "").replace("\ufeff", "").strip()
    return "" if text.lower() in {"", "nan", "none", "null"} else text


def _series(df: pd.DataFrame, names: Iterable[str]) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name].map(_clean)
    return pd.Series("", index=df.index, dtype=object)


def _compound(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    values = pd.DataFrame(
        {column: _series(frame, (column,)) for column in columns},
        index=frame.index,
    )
    complete = values.ne("").all(axis=1)
    result = pd.Series(pd.NA, index=frame.index, dtype="object")
    if complete.any():
        result.loc[complete] = values.loc[complete].agg("|".join, axis=1)
    return result


def standardize_identity_fields(
    df: pd.DataFrame,
    *,
    use_config_defaults: bool = True,
    log_default: bool = True,
) -> pd.DataFrame:
    """Add canonical fields and nullable compound keys without dropping legacy fields."""
    out = df.copy()
    for target, names in ALIASES.items():
        out[target] = _series(out, names)

    if use_config_defaults:
        missing_shop = out["shop_id"].eq("")
        existing_shop_default = (
            out["default_shop_id_used"].fillna(False).astype(bool)
            if "default_shop_id_used" in out.columns
            else pd.Series(False, index=out.index)
        )
        out["default_shop_id_used"] = existing_shop_default
        if missing_shop.any() and DEFAULT_SHOP_ID:
            out.loc[missing_shop, "shop_id"] = _clean(DEFAULT_SHOP_ID)
            out.loc[missing_shop, "default_shop_id_used"] = True
            if log_default:
                logger.warning(
                    "%s rows missing shop_id; using configured DEFAULT_SHOP_ID",
                    int(missing_shop.sum()),
                )

        missing_name = out["shop_name"].eq("")
        if missing_name.any() and DEFAULT_SHOP_NAME:
            out.loc[missing_name, "shop_name"] = _clean(DEFAULT_SHOP_NAME)

        missing_market = out["marketplace"].eq("")
        existing_market_default = (
            out["default_marketplace_used"].fillna(False).astype(bool)
            if "default_marketplace_used" in out.columns
            else pd.Series(False, index=out.index)
        )
        out["default_marketplace_used"] = existing_market_default
        if missing_market.any() and DEFAULT_MARKETPLACE:
            out.loc[missing_market, "marketplace"] = _clean(DEFAULT_MARKETPLACE)
            out.loc[missing_market, "default_marketplace_used"] = True
            if log_default:
                logger.warning(
                    "%s rows missing marketplace; using configured DEFAULT_MARKETPLACE",
                    int(missing_market.sum()),
                )
    else:
        out["default_shop_id_used"] = (
            out["default_shop_id_used"].fillna(False).astype(bool)
            if "default_shop_id_used" in out.columns
            else False
        )
        out["default_marketplace_used"] = (
            out["default_marketplace_used"].fillna(False).astype(bool)
            if "default_marketplace_used" in out.columns
            else False
        )

    parsed = pd.to_datetime(out["report_date"], errors="coerce")
    out["report_date"] = parsed.dt.strftime("%Y-%m-%d").fillna("")

    offer_id = out["msku"].where(out["msku"].ne(""), out["seller_sku"])
    out["key_source"] = ""
    out.loc[out["msku"].ne(""), "key_source"] = "msku"
    out.loc[out["msku"].eq("") & out["seller_sku"].ne(""), "key_source"] = "seller_sku"
    out["offer_identity"] = offer_id
    out["store_key"] = _compound(out, ["shop_id", "marketplace"])
    out["product_key"] = _compound(out, ["shop_id", "marketplace", "asin"])
    out["legacy_offer_key"] = _compound(out, ["shop_id", "marketplace", "offer_identity"])
    out["legacy_daily_offer_key"] = _compound(
        out,
        ["shop_id", "marketplace", "report_date", "offer_identity"],
    )
    out["offer_key"] = _compound(
        out,
        ["shop_id", "marketplace", "key_source", "offer_identity"],
    )
    out["daily_offer_key"] = _compound(
        out,
        ["shop_id", "marketplace", "report_date", "key_source", "offer_identity"],
    )
    out["daily_asin_key"] = _compound(
        out,
        ["shop_id", "marketplace", "report_date", "asin"],
    )
    missing_identity = out[["shop_id", "marketplace", "offer_identity"]].eq("").any(axis=1)
    out["identity_status"] = "confirmed"
    out.loc[missing_identity, "identity_status"] = "pending_confirmation"
    out.loc[
        ~missing_identity & (out["default_shop_id_used"] | out["default_marketplace_used"]),
        "identity_status",
    ] = "configured_default"
    return out


def detect_identity_change_risks(df: pd.DataFrame) -> pd.Series:
    frame = standardize_identity_fields(df, log_default=False)
    seller_tokens = set(frame.loc[frame["key_source"].eq("seller_sku"), "offer_identity"])
    msku_tokens = set(frame.loc[frame["key_source"].eq("msku"), "offer_identity"])
    return frame["offer_identity"].isin(seller_tokens & msku_tokens)


def build_safe_legacy_offer_lookup(
    mapping_df: pd.DataFrame,
    value_columns: Iterable[str],
) -> tuple[dict, set]:
    frame = standardize_identity_fields(mapping_df, log_default=False)
    values = list(value_columns)
    safe, rejected = {}, set()
    for legacy_key, group in frame[frame["legacy_offer_key"].notna()].groupby(
        "legacy_offer_key",
        dropna=False,
    ):
        sources = set(group["key_source"])
        asin_values = set(group["asin"]) - {""}
        if len(group) == 1 and len(sources) == 1 and len(asin_values) <= 1:
            safe[legacy_key] = group.iloc[0][values].to_dict()
        else:
            rejected.add(legacy_key)
    return safe, rejected


def reconcile_by_store(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    frame = standardize_identity_fields(df, log_default=False)
    metrics = [
        "销售额", "总订单", "广告花费", "广告销售额", "FBA可售库存",
        "FBA在途库存", "FBA预留库存", "FBA不可售库存", "FBM库存",
    ]
    for column in metrics:
        if column not in frame:
            frame[column] = 0.0
        else:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame["_unmatched_product"] = (
        frame["product_info_match_status"].eq("unmatched")
        if "product_info_match_status" in frame
        else False
    )
    if "inventory_match_status" not in frame:
        frame["inventory_match_status"] = "source_not_available"
    stock_total = frame[
        ["FBA可售库存", "FBA在途库存", "FBA预留库存", "FBA不可售库存", "FBM库存"]
    ].sum(axis=1)
    frame["_nonzero_identity_unmatched"] = (
        frame["inventory_match_status"].eq("identity_unmatched") & stock_total.gt(0)
    )
    grouped = frame.groupby(["store_key", "shop_id", "marketplace"], dropna=False)
    out = grouped.agg(
        daily_rows=("store_key", "size"),
        sales=("销售额", "sum"),
        orders=("总订单", "sum"),
        ad_spend=("广告花费", "sum"),
        ad_sales=("广告销售额", "sum"),
        inventory_snapshot_cumulative_fba_available=("FBA可售库存", "sum"),
        inventory_snapshot_cumulative_inbound=("FBA在途库存", "sum"),
        inventory_snapshot_cumulative_reserved=("FBA预留库存", "sum"),
        inventory_snapshot_cumulative_unsellable=("FBA不可售库存", "sum"),
        inventory_snapshot_cumulative_fbm=("FBM库存", "sum"),
        unmatched_product_records=("_unmatched_product", "sum"),
        default_shop_id_records=("default_shop_id_used", "sum"),
        default_marketplace_records=("default_marketplace_used", "sum"),
    ).reset_index()
    latest_date = frame.groupby("store_key", dropna=False)["report_date"].transform("max")
    latest = frame[frame["report_date"].eq(latest_date)].groupby(
        "store_key",
        dropna=False,
    )[metrics[4:]].sum()
    latest.columns = [
        "latest_fba_available", "latest_inbound", "latest_reserved",
        "latest_unsellable", "latest_fbm",
    ]
    out = out.merge(latest.reset_index(), on="store_key", how="left")
    unique_offer = frame.groupby("store_key", dropna=False)["offer_key"].nunique()
    cross_date_rows = frame.groupby("store_key", dropna=False).size() - unique_offer
    same_day_duplicates = (
        frame.groupby(["store_key", "report_date", "offer_key"], dropna=False)
        .size()
        .sub(1)
        .clip(lower=0)
        .groupby(level=0)
        .sum()
    )
    duplicate_daily = frame.groupby("store_key", dropna=False)["daily_offer_key"].apply(
        lambda series: int(series.dropna().duplicated().sum())
    )
    out["unique_offer_keys"] = out["store_key"].map(unique_offer).fillna(0).astype(int)
    out["cross_date_repeated_rows"] = out["store_key"].map(cross_date_rows).fillna(0).astype(int)
    out["same_date_offer_key_duplicates"] = out["store_key"].map(same_day_duplicates).fillna(0).astype(int)
    out["daily_offer_key_duplicates"] = out["store_key"].map(duplicate_daily).fillna(0).astype(int)
    for status in [
        "matched", "zero_inventory", "source_not_available", "pending_confirmation",
        "identity_unmatched", "conflict",
    ]:
        counts = frame["inventory_match_status"].eq(status).groupby(
            frame["store_key"],
            dropna=False,
        ).sum()
        out[f"{status}_count"] = out["store_key"].map(counts).fillna(0).astype(int)
    nonzero = frame["_nonzero_identity_unmatched"].groupby(
        frame["store_key"],
        dropna=False,
    ).sum()
    out["nonzero_identity_unmatched_count"] = out["store_key"].map(nonzero).fillna(0).astype(int)
    if "legacy_offer_match_rejected" in frame:
        rejected = frame["legacy_offer_match_rejected"].fillna(False).astype(bool).groupby(
            frame["store_key"],
            dropna=False,
        ).sum()
        out["legacy_offer_match_rejected_count"] = out["store_key"].map(rejected).fillna(0).astype(int)
    else:
        out["legacy_offer_match_rejected_count"] = 0
    out["architecture_status"] = "multi-store identity architecture enabled"
    return out


def build_parent_child_relationships(
    sources: Iterable[tuple[str, pd.DataFrame]],
    *,
    last_updated_at: str | None = None,
) -> pd.DataFrame:
    timestamp = last_updated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = []
    for source_name, source_df in sources:
        if source_df is None or source_df.empty:
            continue
        frame = standardize_identity_fields(source_df, log_default=False)
        part = pd.DataFrame({
            "shop_id": frame["shop_id"],
            "marketplace": frame["marketplace"],
            "parent_asin": frame["parent_asin"],
            "child_asin": frame["asin"],
            "msku": frame["msku"],
            "seller_sku": frame["seller_sku"],
            "relationship_source": source_name,
        })
        parts.append(part[part["parent_asin"].ne("") & part["child_asin"].ne("")])
    if not parts:
        return pd.DataFrame(columns=RELATIONSHIP_COLUMNS)
    out = pd.concat(parts, ignore_index=True).drop_duplicates()
    conflict_key = ["shop_id", "marketplace", "child_asin", "msku", "seller_sku"]
    parent_counts = out.groupby(conflict_key, dropna=False)["parent_asin"].transform("nunique")
    out["relationship_status"] = parent_counts.map(
        lambda count: "待确认" if count > 1 else "有效"
    )
    out["last_updated_at"] = timestamp
    return out[RELATIONSHIP_COLUMNS].sort_values(
        ["shop_id", "marketplace", "parent_asin", "child_asin", "msku", "relationship_source"]
    ).reset_index(drop=True)


def aggregate_children_without_parent_rows(
    df: pd.DataFrame,
    metric_columns: Iterable[str],
) -> pd.DataFrame:
    frame = standardize_identity_fields(df, log_default=False)
    children = frame[
        frame["asin"].ne("") & frame["asin"].ne(frame["parent_asin"])
    ].copy()
    for column in metric_columns:
        children[column] = pd.to_numeric(
            children.get(column, 0),
            errors="coerce",
        ).fillna(0)
    group_columns = ["shop_id", "marketplace", "report_date", "parent_asin"]
    return children.groupby(group_columns, as_index=False)[list(metric_columns)].sum()
