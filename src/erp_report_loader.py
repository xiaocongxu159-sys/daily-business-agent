# -*- coding: utf-8 -*-
"""Load explicitly selected Lingxing-style ERP product performance reports."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.data_identity import standardize_identity_fields


def _norm(value) -> str:
    return str(value).strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _find(dataframe: pd.DataFrame, aliases) -> str | None:
    normalized = {column: _norm(column) for column in dataframe.columns}
    for alias in aliases:
        key = _norm(alias)
        for column, value in normalized.items():
            if value == key:
                return column
    for alias in aliases:
        key = _norm(alias)
        for column, value in normalized.items():
            if key and key in value:
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
    values = (
        dataframe[column]
        .fillna("")
        .astype(str)
        .str.replace("US$", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(values, errors="coerce").fillna(0.0)


def _date(dataframe: pd.DataFrame) -> pd.Series:
    column = _find(dataframe, ["日期", "Date", "report_date", "统计日期"])
    if column is None:
        return pd.Series("", index=dataframe.index)
    return pd.to_datetime(dataframe[column], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")


def _read(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".csv", ".txt", ".tsv"}:
        separator = "\t" if path.suffix.lower() in {".txt", ".tsv"} else ","
        return pd.read_csv(path, sep=separator, dtype=object)
    excel = pd.ExcelFile(path)
    sheet = "sheet1" if "sheet1" in excel.sheet_names else excel.sheet_names[0]
    return pd.read_excel(path, sheet_name=sheet, dtype=object)


def load_erp_report(filepath, mapping_df=None):
    """Return ``(business_df, ad_df, errors)`` for one explicit ERP file."""
    path = Path(filepath)
    errors = []
    try:
        raw = _read(path)
    except Exception as exc:
        return None, None, [f"读取失败: {exc}"]
    if raw.empty:
        return None, None, ["文件为空"]

    identity = pd.DataFrame(index=raw.index)
    identity["日期"] = _date(raw)
    identity["shop_id"] = _text(raw, ["shop_id", "店铺ID", "sid"])
    identity["shop_name"] = _text(raw, ["shop_name", "店铺", "店铺名称"])
    identity["marketplace"] = _text(raw, ["marketplace", "站点", "国家"])
    identity["ASIN"] = _text(raw, ["ASIN", "asin", "子ASIN"])
    identity["父ASIN"] = _text(raw, ["父ASIN", "Parent ASIN", "parent_asin"])
    identity["MSKU"] = _text(raw, ["MSKU", "msku"])
    identity["ERP内部SKU"] = _text(raw, ["ERP内部SKU", "seller_sku", "seller-sku", "系统SKU"])
    identity["SKU"] = identity["MSKU"].where(identity["MSKU"].ne(""), identity["ERP内部SKU"])
    identity["产品名称"] = _text(raw, ["标题", "产品名称", "商品名称", "item-name"])
    identity["asin"] = identity["ASIN"]
    identity["parent_asin"] = identity["父ASIN"]
    identity["msku"] = identity["MSKU"]
    identity["seller_sku"] = identity["ERP内部SKU"].where(
        identity["ERP内部SKU"].ne(""), identity["SKU"]
    )
    identity = standardize_identity_fields(identity)

    valid = identity["report_date"].ne("") & identity["asin"].ne("") & identity["offer_identity"].ne("")
    if not valid.any():
        return None, None, ["没有同时包含有效日期、ASIN 和 SKU/MSKU 的数据行"]
    identity = identity.loc[valid].reset_index(drop=True)
    raw = raw.loc[valid].reset_index(drop=True)

    business = identity.copy()
    business["日期"] = identity["report_date"]
    business["ASIN"] = identity["asin"]
    business["父ASIN"] = identity["parent_asin"]
    business["SKU"] = identity["offer_identity"]
    business["ERP内部SKU"] = identity["seller_sku"]
    business["MSKU"] = identity["msku"]
    business["产品名称"] = identity.get("产品名称", "")
    business["Sessions"] = _number(raw, ["Sessions-Total", "Sessions", "会话数"])
    business["Page Views"] = _number(raw, ["PV-Total", "Page Views", "页面浏览量"])
    business["Units Ordered"] = _number(raw, ["订单量", "Units Ordered", "销量"])
    business["Ordered Product Sales"] = _number(raw, ["销售额", "Ordered Product Sales", "净销售额"])
    business["Total Order Items"] = business["Units Ordered"]
    business["业务转化率"] = np.where(
        business["Sessions"].gt(0), business["Units Ordered"] / business["Sessions"] * 100, 0.0
    )
    business["销量"] = business["Units Ordered"]
    business["净销售额"] = business["Ordered Product Sales"]
    business["自然点击量"] = _number(raw, ["自然点击量", "自然点击"])
    business["自然订单量"] = _number(raw, ["自然订单量", "自然订单"])
    business["自然CVR"] = np.where(
        business["自然点击量"].gt(0), business["自然订单量"] / business["自然点击量"] * 100, 0.0
    )
    business["FBA可售库存"] = _number(raw, ["FBA可售库存", "FBA可售"])
    business["FBA在途库存"] = _number(raw, ["FBA在途库存", "FBA在途", "在途库存"])
    business["FBA预留库存"] = _number(raw, ["FBA预留库存", "预留库存"])
    business["FBA不可售库存"] = _number(raw, ["FBA不可售库存", "不可售库存"])
    business["FBM库存"] = _number(raw, ["FBM库存", "本地库存"])
    business["总库存"] = business[
        ["FBA可售库存", "FBA在途库存", "FBA预留库存", "FBA不可售库存", "FBM库存"]
    ].sum(axis=1)
    business["inventory_source_present"] = business["总库存"].ge(0)

    advertising = identity.copy()
    advertising["日期"] = identity["report_date"]
    advertising["ASIN"] = identity["asin"]
    advertising["父ASIN"] = identity["parent_asin"]
    advertising["SKU"] = identity["offer_identity"]
    advertising["ERP内部SKU"] = identity["seller_sku"]
    advertising["MSKU"] = identity["msku"]
    advertising["Campaign"] = "ERP Product Performance"
    advertising["Ad Group"] = ""
    advertising["Impressions"] = _number(raw, ["展示", "Impressions", "广告曝光"])
    advertising["Clicks"] = _number(raw, ["点击", "Clicks", "广告点击"])
    advertising["Spend"] = _number(raw, ["广告花费", "Spend"])
    advertising["Orders"] = _number(raw, ["广告订单量", "广告订单", "Orders"])
    advertising["Sales"] = _number(raw, ["广告销售额", "Sales"])
    advertising["CTR"] = np.where(
        advertising["Impressions"].gt(0), advertising["Clicks"] / advertising["Impressions"], 0.0
    )
    advertising["CPC"] = np.where(
        advertising["Clicks"].gt(0), advertising["Spend"] / advertising["Clicks"], 0.0
    )
    advertising["CVR"] = np.where(
        advertising["Clicks"].gt(0), advertising["Orders"] / advertising["Clicks"], 0.0
    )
    advertising["ACOS"] = np.where(
        advertising["Sales"].gt(0), advertising["Spend"] / advertising["Sales"], 0.0
    )
    advertising["TACOS"] = np.where(
        business["Ordered Product Sales"].gt(0),
        advertising["Spend"] / business["Ordered Product Sales"],
        0.0,
    )
    advertising["广告订单占比"] = np.where(
        business["Units Ordered"].gt(0), advertising["Orders"] / business["Units Ordered"], 0.0
    )
    advertising["ROAS"] = np.where(
        advertising["Spend"].gt(0), advertising["Sales"] / advertising["Spend"], 0.0
    )
    return business.reset_index(drop=True), advertising.reset_index(drop=True), errors


def load_all_erp_reports(
    file_list,
    mapping_df=None,
    history_output_dir=None,
    include_history=False,
):
    """Combine only explicitly selected ERP files; automatic history scanning is disabled."""
    business_frames, ad_frames, errors = [], [], []
    if include_history:
        errors.append({
            "file": "",
            "error": "公开版不会自动扫描历史输出；请显式选择需要合并的 ERP 文件。",
        })
    for filepath in file_list:
        business, advertising, file_errors = load_erp_report(filepath, mapping_df=mapping_df)
        if business is not None:
            business_frames.append(business)
        if advertising is not None:
            ad_frames.append(advertising)
        for error in file_errors or []:
            errors.append({"file": Path(filepath).name, "error": error})
    business = (
        pd.concat(business_frames, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)
        if business_frames
        else None
    )
    advertising = (
        pd.concat(ad_frames, ignore_index=True, sort=False).drop_duplicates().reset_index(drop=True)
        if ad_frames
        else None
    )
    return business, advertising, errors
