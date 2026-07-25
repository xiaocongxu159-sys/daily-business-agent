# -*- coding: utf-8 -*-
"""Load user-provided Amazon business reports without network access."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.data_identity import standardize_identity_fields
from src.date_parser import extract_date_from_filename, normalize_date
from src.report_detector import find_column

_ALIASES = None
IDENTITY_ALIASES = {
    "shop_id": ("shop_id", "店铺ID", "店铺 Id", "sid"),
    "shop_name": ("shop_name", "店铺", "店铺名称"),
    "marketplace": ("marketplace", "站点", "国家", "marketplace_id"),
    "parent_asin": ("parent_asin", "父ASIN", "Parent ASIN"),
    "msku": ("msku", "MSKU"),
    "seller_sku": ("seller_sku", "seller-sku", "Seller SKU", "SKU"),
}


def _load_aliases():
    global _ALIASES
    if _ALIASES is None:
        path = Path(__file__).resolve().parent.parent / "config" / "field_aliases.json"
        with path.open("r", encoding="utf-8") as handle:
            _ALIASES = json.load(handle)
    return _ALIASES.get("业务报告字段", {})


def _clean_numeric(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    text = str(value).strip()
    if text in {"", "-", "--", "nan", "N/A", "None", "null"}:
        return 0.0
    text = text.replace("US$", "").replace("$", "").replace(",", "").replace("%", "")
    try:
        return float(text)
    except ValueError:
        return 0.0


def _valid_date(value) -> str | None:
    normalized = normalize_date(str(value).strip()) if value is not None else None
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _read_report(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".txt", ".tsv"}:
        return pd.read_csv(path, sep="\t", dtype=str)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str)
    return pd.read_excel(path, dtype=str)


def load_business_report(filepath, date_str=None):
    """Load one business report and return ``(DataFrame, errors)``."""
    path = Path(filepath)
    errors = []
    try:
        dataframe = _read_report(path)
    except Exception as exc:
        return None, [f"读取失败: {exc}"]
    if dataframe.empty:
        return None, ["文件为空"]

    aliases = _load_aliases()
    date_column = find_column(dataframe, aliases.get("Date", ["Date", "日期"]))
    fallback_date = _valid_date(date_str) if date_str else _valid_date(extract_date_from_filename(path.name))
    if date_column:
        dates = dataframe[date_column].map(_valid_date)
        if fallback_date:
            dates = dates.fillna(fallback_date)
    elif fallback_date:
        dates = pd.Series([fallback_date] * len(dataframe), index=dataframe.index)
    else:
        return None, ["无法从日期字段或文件名识别日期"]
    if dates.isna().all():
        return None, ["日期字段全部为空或无法解析"]

    result = pd.DataFrame({"日期": dates}, index=dataframe.index)
    for output_column, candidates in aliases.items():
        if output_column == "Date":
            continue
        source = find_column(dataframe, candidates)
        result[output_column] = dataframe[source] if source else ""
    for output_column, candidates in IDENTITY_ALIASES.items():
        source = find_column(dataframe, candidates)
        result[output_column] = dataframe[source].astype(str).str.strip() if source else ""

    if "ASIN" not in result.columns or result["ASIN"].astype(str).str.strip().eq("").all():
        asin_column = find_column(
            dataframe,
            ["ASIN", "asin", "Child ASIN", "（子）ASIN", "子ASIN", "Parent ASIN", "父ASIN"],
        )
        result["ASIN"] = dataframe[asin_column].astype(str).str.strip() if asin_column else ""

    for column in [
        "Sessions", "Page Views", "Units Ordered", "Ordered Product Sales", "Total Order Items",
    ]:
        result[column] = result.get(column, 0).map(_clean_numeric)
    result["ASIN"] = result["ASIN"].astype(str).str.strip()
    result = result[result["日期"].notna()].copy()
    valid = result[
        result["ASIN"].ne("")
        | result["Sessions"].gt(0)
        | result["Ordered Product Sales"].gt(0)
    ].copy()
    if valid.empty:
        return None, ["没有有效数据行"]
    if valid["ASIN"].eq("").all():
        errors.append("所有行缺少 ASIN")

    valid["asin"] = valid["ASIN"]
    valid = standardize_identity_fields(valid.reset_index(drop=True))
    return valid, errors


def _mapping_lookups(mapping_df: pd.DataFrame) -> tuple[dict, dict]:
    mapping = standardize_identity_fields(mapping_df, log_default=False)
    mapping_sku = mapping.get("SKU", mapping.get("seller_sku", "")).astype(str).str.strip()
    mapping = mapping.assign(_mapping_sku=mapping_sku)

    scoped = {}
    for product_key, group in mapping[mapping["product_key"].notna()].groupby("product_key"):
        values = set(group["_mapping_sku"]) - {""}
        if len(values) == 1:
            scoped[product_key] = next(iter(values))

    global_unique = {}
    for asin, group in mapping[mapping["asin"].ne("")].groupby("asin"):
        values = set(group["_mapping_sku"]) - {""}
        if len(values) == 1:
            global_unique[asin] = next(iter(values))
    return scoped, global_unique


def load_all_business_reports(file_list, mapping_df=None):
    """Load and combine business reports without collapsing store identities."""
    frames = []
    errors = []
    for filepath in file_list:
        dataframe, file_errors = load_business_report(filepath)
        if dataframe is not None:
            frames.append(dataframe)
        for error in file_errors or []:
            errors.append({"file": Path(filepath).name, "error": error})
    if not frames:
        return None, errors

    combined = pd.concat(frames, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)
    if mapping_df is not None and not mapping_df.empty:
        scoped, global_unique = _mapping_lookups(mapping_df)
        current = combined.get("seller_sku", pd.Series("", index=combined.index)).astype(str).str.strip()
        missing = current.eq("")
        scoped_values = combined.get("product_key", pd.Series(pd.NA, index=combined.index)).map(scoped)
        global_values = combined.get("asin", pd.Series("", index=combined.index)).map(global_unique)
        combined.loc[missing, "seller_sku"] = scoped_values.where(scoped_values.notna(), global_values).fillna("")
        combined["SKU"] = combined["seller_sku"]
        combined = standardize_identity_fields(combined, log_default=False)
    return combined, errors
