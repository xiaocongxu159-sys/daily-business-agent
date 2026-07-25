# -*- coding: utf-8 -*-
"""Load and aggregate user-provided advertising reports locally."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from src.data_identity import standardize_identity_fields
from src.date_parser import normalize_date
from src.report_detector import find_column

_ALIASES = None
IDENTITY_ALIASES = {
    "shop_id": ("shop_id", "店铺ID", "店铺 Id", "sid"),
    "shop_name": ("shop_name", "店铺", "店铺名称"),
    "marketplace": ("marketplace", "站点", "国家", "marketplace_id"),
    "parent_asin": ("parent_asin", "父ASIN", "Parent ASIN"),
    "msku": ("msku", "MSKU"),
    "seller_sku": ("seller_sku", "seller-sku", "Seller SKU"),
}


def _load_aliases():
    global _ALIASES
    if _ALIASES is None:
        path = Path(__file__).resolve().parent.parent / "config" / "field_aliases.json"
        with path.open("r", encoding="utf-8") as handle:
            _ALIASES = json.load(handle)
    return _ALIASES.get("广告报告字段", {})


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


def _clean_ratio(value):
    if value is None:
        return 0.0
    text = str(value).strip()
    percent = text.endswith("%")
    number = _clean_numeric(value)
    if percent or 1 < number <= 100:
        return number / 100.0
    return number


def _valid_date(value) -> str | None:
    normalized = normalize_date(str(value).strip()) if value is not None else None
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _find_date_column(dataframe):
    return find_column(
        dataframe,
        ["Date", "date", "日期", "Start Date", "End Date", "Report Date", "Day", "Day Date"],
    )


def _detect_date_range_from_filename(filepath):
    name = Path(filepath).stem
    for pattern in [
        r"(\d{8})[-_~](\d{8})",
        r"(\d{4}-\d{2}-\d{2})[-_~](\d{4}-\d{2}-\d{2})",
    ]:
        match = re.search(pattern, name)
        if match:
            first, second = _valid_date(match.group(1)), _valid_date(match.group(2))
            if first and second:
                return first, second
    return None


def _read_report(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".txt", ".tsv"}:
        return pd.read_csv(path, sep="\t", dtype=str)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str)
    return pd.read_excel(path, dtype=str)


def load_ad_report(filepath):
    path = Path(filepath)
    errors = []
    try:
        dataframe = _read_report(path)
    except Exception as exc:
        return None, [f"读取失败: {exc}"]
    if dataframe.empty:
        return None, ["文件为空"]

    date_column = _find_date_column(dataframe)
    if date_column is None:
        date_range = _detect_date_range_from_filename(path)
        if date_range:
            return None, [f"广告报告缺少每日日期字段，文件名仅包含范围 {date_range[0]}~{date_range[1]}"]
        return None, ["广告报告缺少 Date 字段，无法按日期汇总"]
    dates = dataframe[date_column].map(_valid_date)
    if dates.isna().all():
        return None, ["日期字段全部为空或无法解析"]

    result = pd.DataFrame({"日期": dates}, index=dataframe.index)
    for output_column, candidates in _load_aliases().items():
        if output_column == "Date":
            continue
        source = find_column(dataframe, candidates)
        result[output_column] = dataframe[source] if source else ""
    for output_column, candidates in IDENTITY_ALIASES.items():
        source = find_column(dataframe, candidates)
        result[output_column] = dataframe[source].astype(str).str.strip() if source else ""

    if "Campaign" in result.columns and result["Campaign"].astype(str).str.strip().eq("").all():
        campaign_column = find_column(dataframe, ["Campaign", "Campaign Name", "广告活动", "广告活动名称"])
        if campaign_column:
            result["Campaign"] = dataframe[campaign_column]

    for column in ["Impressions", "Clicks", "Spend", "Orders", "Sales"]:
        result[column] = result.get(column, 0).map(_clean_numeric)
    for column in ["ACOS", "CTR", "CPC", "ROAS", "CVR"]:
        result[column] = result.get(column, 0).map(_clean_ratio)

    result["ACOS"] = np.where(
        result["Sales"].gt(0), result["Spend"] / result["Sales"], result["ACOS"]
    )
    result["CTR"] = np.where(
        result["Impressions"].gt(0), result["Clicks"] / result["Impressions"], result["CTR"]
    )
    result["CPC"] = np.where(
        result["Clicks"].gt(0), result["Spend"] / result["Clicks"], result["CPC"]
    )
    result["CVR"] = np.where(
        result["Clicks"].gt(0), result["Orders"] / result["Clicks"], result["CVR"]
    )
    result["ROAS"] = np.where(
        result["Spend"].gt(0), result["Sales"] / result["Spend"], result["ROAS"]
    )

    campaign_present = result.get("Campaign", pd.Series("", index=result.index)).astype(str).str.strip().ne("")
    metric_present = result[["Impressions", "Clicks", "Spend"]].gt(0).any(axis=1)
    valid = result[result["日期"].notna() & (campaign_present | metric_present)].copy()
    if valid.empty:
        return None, ["没有有效数据行"]

    valid["ASIN"] = valid.get("ASIN", "").astype(str).str.strip()
    valid["SKU"] = valid.get("SKU", "").astype(str).str.strip()
    valid["asin"] = valid["ASIN"]
    valid["seller_sku"] = valid["seller_sku"].where(
        valid["seller_sku"].astype(str).str.strip().ne(""), valid["SKU"]
    )
    valid = standardize_identity_fields(valid.reset_index(drop=True))

    if not valid["ASIN"].ne("").any() and not valid["seller_sku"].ne("").any():
        errors.append("缺少 SKU/ASIN，需要维护广告活动商品映射")
    unique_dates = valid["日期"].dropna().unique()
    if len(unique_dates) > 1:
        errors.append(f"广告报告包含多天数据: {len(unique_dates)} 个不同日期")
    return valid, errors


def load_all_ad_reports(file_list):
    frames = []
    errors = []
    for filepath in file_list:
        dataframe, file_errors = load_ad_report(filepath)
        if dataframe is not None:
            frames.append(dataframe)
        for error in file_errors or []:
            errors.append({"file": Path(filepath).name, "error": error})
    if not frames:
        return None, errors
    combined = pd.concat(frames, ignore_index=True, sort=False)
    return combined.drop_duplicates().reset_index(drop=True), errors


def process_ad_for_merge(ad_raw, asin_to_sku_dict=None):
    if ad_raw is None or ad_raw.empty:
        return pd.DataFrame(), pd.DataFrame(), [], {}
    dataframe = ad_raw.copy()
    dataframe["SKU"] = dataframe.get("广告SKU", dataframe.get("SKU", "")).astype(str).str.strip()
    dataframe["ASIN"] = dataframe.get("广告ASIN", dataframe.get("ASIN", "")).astype(str).str.strip()
    if asin_to_sku_dict:
        missing = dataframe["SKU"].eq("")
        dataframe.loc[missing, "SKU"] = dataframe.loc[missing, "ASIN"].map(asin_to_sku_dict).fillna("")
    dataframe["seller_sku"] = dataframe.get("seller_sku", "").astype(str).str.strip()
    dataframe["seller_sku"] = dataframe["seller_sku"].where(
        dataframe["seller_sku"].ne(""), dataframe["SKU"]
    )
    dataframe["asin"] = dataframe["ASIN"]
    dataframe = standardize_identity_fields(dataframe, log_default=False)

    for column in ["Impressions", "Clicks", "Spend", "Orders", "Sales"]:
        dataframe[column] = pd.to_numeric(dataframe.get(column, 0), errors="coerce").fillna(0.0)
    dataframe["CTR"] = np.where(dataframe["Impressions"].gt(0), dataframe["Clicks"] / dataframe["Impressions"], 0.0)
    dataframe["CPC"] = np.where(dataframe["Clicks"].gt(0), dataframe["Spend"] / dataframe["Clicks"], 0.0)
    dataframe["CVR"] = np.where(dataframe["Clicks"].gt(0), dataframe["Orders"] / dataframe["Clicks"], 0.0)
    dataframe["ACOS"] = np.where(dataframe["Sales"].gt(0), dataframe["Spend"] / dataframe["Sales"], 0.0)
    dataframe["ROAS"] = np.where(dataframe["Spend"].gt(0), dataframe["Sales"] / dataframe["Spend"], 0.0)
    detail = dataframe.copy()

    has_sku = dataframe["SKU"].ne("")
    stats = {
        "total": len(dataframe),
        "with_sku": int(has_sku.sum()),
        "without_sku": int((~has_sku).sum()),
    }
    if has_sku.any():
        identity_columns = [
            column
            for column in [
                "shop_id", "shop_name", "marketplace", "report_date", "parent_asin", "asin",
                "seller_sku", "msku", "store_key", "product_key", "offer_key", "daily_offer_key",
                "daily_asin_key", "key_source",
            ]
            if column in dataframe.columns
        ]
        group_columns = identity_columns + ["日期", "SKU", "ASIN"]
        aggregate = dataframe[has_sku].groupby(group_columns, as_index=False, dropna=False).agg(
            {"Impressions": "sum", "Clicks": "sum", "Spend": "sum", "Orders": "sum", "Sales": "sum"}
        )
        aggregate["CTR"] = np.where(aggregate["Impressions"].gt(0), aggregate["Clicks"] / aggregate["Impressions"], 0.0)
        aggregate["CPC"] = np.where(aggregate["Clicks"].gt(0), aggregate["Spend"] / aggregate["Clicks"], 0.0)
        aggregate["CVR"] = np.where(aggregate["Clicks"].gt(0), aggregate["Orders"] / aggregate["Clicks"], 0.0)
        aggregate["ACOS"] = np.where(aggregate["Sales"].gt(0), aggregate["Spend"] / aggregate["Sales"], 0.0)
        aggregate["ROAS"] = np.where(aggregate["Spend"].gt(0), aggregate["Sales"] / aggregate["Spend"], 0.0)
    else:
        aggregate = pd.DataFrame()

    used_skus = set(dataframe.loc[has_sku, "SKU"].unique())
    mapping_skus = set(asin_to_sku_dict.values()) if asin_to_sku_dict else set()
    unmapped = sorted(used_skus - mapping_skus) if mapping_skus else []
    stats["agg"] = len(aggregate)
    stats["unmapped"] = len(unmapped)
    return aggregate, detail, unmapped, stats


def get_ad_quality_checks(ad_raw, ad_agg, ad_unmapped, asin_to_sku_dict):
    checks = []
    if ad_raw is None or len(ad_raw) == 0:
        return [("识别到推广商品每日明细", False, "无结果")]
    checks.append(("识别到推广商品每日明细", True, f"{len(ad_raw)}行"))
    checks.append(("广告报表包含日期字段", "日期" in ad_raw.columns and ad_raw["日期"].notna().any(), ""))
    checks.append(("广告数据按日期和商品汇总", ad_agg is not None and not ad_agg.empty, f"{len(ad_agg)}行" if ad_agg is not None else ""))
    checks.append(("广告商品匹配产品对照表", not bool(ad_unmapped), "全部匹配" if not ad_unmapped else f"{len(ad_unmapped)}个无法匹配"))
    return checks
