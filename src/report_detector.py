# -*- coding: utf-8 -*-
"""Detect report types using local file headers and public alias rules."""
import json
from pathlib import Path

import pandas as pd

_ALIASES = None


def _load_aliases():
    global _ALIASES
    if _ALIASES is None:
        path = Path(__file__).resolve().parent.parent / "config" / "field_aliases.json"
        with path.open("r", encoding="utf-8") as handle:
            _ALIASES = json.load(handle)
    return _ALIASES


def find_column(dataframe, candidates):
    columns = {
        str(column).strip().lower().replace(" ", "").replace("\u3000", ""): column
        for column in dataframe.columns
    }
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "").replace("\u3000", "")
        if key in columns:
            return columns[key]
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "").replace("\u3000", "")
        for normalized, raw in columns.items():
            if key in normalized or normalized in key:
                return raw
    return None


def detect_report_type(filepath):
    try:
        suffix = Path(filepath).suffix.lower()
        if suffix in {".txt", ".tsv"}:
            dataframe = pd.read_csv(filepath, sep="\t", nrows=0, dtype=str)
        elif suffix == ".csv":
            dataframe = pd.read_csv(filepath, nrows=0, dtype=str)
        else:
            dataframe = pd.read_excel(filepath, nrows=0, dtype=str)
    except Exception:
        return None

    columns = " ".join(str(column).lower().strip() for column in dataframe.columns)
    aliases = _load_aliases()
    business_keywords = aliases.get("业务报告表头关键词", [])
    advertising_keywords = aliases.get("广告报告表头关键词", [])
    mapping_keywords = aliases.get("产品对照表表头关键词", [])

    business_score = sum(1 for keyword in business_keywords if keyword.lower() in columns) * 2
    advertising_score = sum(1 for keyword in advertising_keywords if keyword.lower() in columns) * 2
    mapping_score = sum(1 for keyword in mapping_keywords if keyword.lower() in columns) * 2

    business_score += sum(
        1 for field in ["sessions", "page views", "units ordered", "ordered product sales"]
        if field in columns
    ) * 3
    advertising_score += sum(
        1 for field in ["impressions", "clicks", "spend", "acos"]
        if field in columns
    ) * 3
    mapping_score += sum(
        1 for field in ["seller-sku", "asin1", "item-name", "quantity"]
        if field in columns
    ) * 3

    if mapping_score >= 3 and mapping_score >= business_score and mapping_score >= advertising_score:
        return "mapping"
    if business_score >= 3 and business_score >= advertising_score:
        return "business"
    if advertising_score >= 3 and advertising_score >= business_score:
        return "ad"
    return None
