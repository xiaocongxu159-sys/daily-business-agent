# -*- coding: utf-8 -*-
"""Load and standardize user-provided product mapping files."""
import json
from pathlib import Path

import pandas as pd

from src.data_identity import standardize_identity_fields
from src.report_detector import find_column

_ALIASES = None

IDENTITY_ALIASES = {
    "shop_id": ("shop_id", "店铺ID", "店铺 Id", "sid"),
    "shop_name": ("shop_name", "店铺", "店铺名称"),
    "marketplace": ("marketplace", "站点", "国家", "marketplace_id"),
    "parent_asin": ("parent_asin", "父ASIN", "Parent ASIN"),
    "msku": ("msku", "MSKU"),
}


def _load_aliases():
    global _ALIASES
    if _ALIASES is None:
        path = Path(__file__).resolve().parent.parent / "config" / "field_aliases.json"
        with path.open("r", encoding="utf-8") as handle:
            _ALIASES = json.load(handle)
    return _ALIASES.get("产品对照表字段", {})


def load_mapping(filepath):
    """Load a product mapping file and return a standardized DataFrame."""
    path = Path(filepath)
    aliases = _load_aliases()
    try:
        if path.suffix.lower() in {".txt", ".tsv"}:
            dataframe = pd.read_csv(path, sep="\t", dtype=str)
        elif path.suffix.lower() == ".csv":
            dataframe = pd.read_csv(path, dtype=str)
        else:
            dataframe = pd.read_excel(path, dtype=str)
    except Exception as exc:
        return None, f"读取失败: {exc}"
    if dataframe.empty:
        return None, "文件为空"

    result = {}
    errors = []
    for output_column, candidates in aliases.items():
        source = find_column(dataframe, candidates)
        result[output_column] = (
            dataframe[source].astype(str).str.strip()
            if source
            else pd.Series([""] * len(dataframe), index=dataframe.index)
        )
    result_df = pd.DataFrame(result, index=dataframe.index)
    for output_column, candidates in IDENTITY_ALIASES.items():
        source = find_column(dataframe, candidates)
        result_df[output_column] = (
            dataframe[source].astype(str).str.strip()
            if source
            else pd.Series([""] * len(dataframe), index=dataframe.index)
        )

    result_df["ASIN"] = result_df.get("asin1", "")
    if "ASIN" not in result_df or result_df["ASIN"].eq("").all():
        asin_column = find_column(
            dataframe,
            aliases.get("asin1", ["asin1", "asin", "ASIN"]),
        )
        result_df["ASIN"] = (
            dataframe[asin_column].astype(str).str.strip()
            if asin_column
            else ""
        )
    result_df["SKU"] = result_df.get("seller-sku", "")

    missing_sku = result_df["SKU"].isna() | result_df["SKU"].eq("")
    missing_asin = result_df["ASIN"].isna() | result_df["ASIN"].eq("")
    if missing_sku.any():
        errors.append(f"有 {int(missing_sku.sum())} 行缺少 SKU")
    if missing_asin.any():
        errors.append(f"有 {int(missing_asin.sum())} 行缺少 ASIN")

    valid = result_df[~missing_sku & ~missing_asin].copy()
    valid = valid.drop_duplicates(
        subset=["shop_id", "marketplace", "SKU", "ASIN"]
    ).reset_index(drop=True)

    output = pd.DataFrame(index=valid.index)
    output["SKU"] = valid.get("seller-sku", valid.get("SKU", ""))
    output["ASIN"] = valid.get("ASIN", "")
    output["产品名称"] = valid.get("item-name", "")
    output["售价"] = valid.get("price", "")
    output["库存"] = valid.get("quantity", "")
    output["配送方式"] = valid.get("fulfillment-channel", "")
    output["是否在售"] = valid.get("status", "")
    output["备注"] = ""
    output["shop_id"] = valid.get("shop_id", "")
    output["shop_name"] = valid.get("shop_name", "")
    output["marketplace"] = valid.get("marketplace", "")
    output["parent_asin"] = valid.get("parent_asin", "")
    output["seller_sku"] = output["SKU"]
    output["msku"] = valid.get("msku", "")
    output["mapping_source"] = (
        "All_Listings_Report"
        if "all_listings" in path.stem.lower()
        else "user_provided_mapping"
    )
    output = standardize_identity_fields(output)

    conflict_counts = output.groupby("offer_key", dropna=True)["ASIN"].transform("nunique")
    output["mapping_status"] = "confirmed"
    output.loc[
        output["identity_status"].ne("confirmed") | conflict_counts.gt(1),
        "mapping_status",
    ] = "pending_confirmation"
    return output, errors


def build_sku_asin_dict(mapping_df):
    """Build an ASIN-to-SKU lookup from a standardized mapping table."""
    if mapping_df is None or mapping_df.empty:
        return {}
    result = {}
    for _, row in mapping_df.iterrows():
        sku = str(row.get("SKU", "")).strip()
        asin = str(row.get("ASIN", "")).strip()
        if sku and asin:
            result[asin] = sku
    return result
