# -*- coding: utf-8 -*-
"""Build compact, serializable data-quality summaries."""
from __future__ import annotations

import pandas as pd


CATEGORIES = [
    "无法识别报表类型的文件",
    "无法识别日期的文件",
    "产品对照表缺少SKU/ASIN的行",
    "广告数据无法匹配SKU的行",
    "有广告数据但无业务数据的行",
    "有业务数据但无广告数据的行",
    "业务订单大于0但销售额为0",
    "广告订单大于0但销售额为0",
    "同一日期和商品重复行",
    "被跳过的文件及原因",
    "其他问题",
]


def _items(value, limit=100) -> list:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.head(limit).to_dict("records")
    if isinstance(value, pd.Series):
        return value.head(limit).tolist()
    if isinstance(value, (list, tuple, set)):
        return list(value)[:limit]
    return [value]


def _issue_text(issue) -> str:
    if isinstance(issue, dict):
        file_name = str(issue.get("file", "")).strip()
        error = str(issue.get("error", issue)).strip()
        return f"{file_name}: {error}" if file_name else error
    return str(issue)


def generate_enhanced_quality_report(
    biz_issues=None,
    ad_issues=None,
    ad_stats=None,
    ad_only_rows=None,
    biz_only_rows=None,
    biz_order_no_sales=None,
    ad_order_no_sales=None,
    dup_rows=None,
    ad_unmapped_skus=None,
    mapping_errors=None,
    skipped_files=None,
):
    report = {category: [] for category in CATEGORIES}
    for issue in _items(biz_issues) + _items(ad_issues):
        text = _issue_text(issue)
        if "日期" in text:
            report["无法识别日期的文件"].append(text)
        else:
            report["其他问题"].append(text)
    report["产品对照表缺少SKU/ASIN的行"].extend(
        _issue_text(item) for item in _items(mapping_errors)
    )
    report["广告数据无法匹配SKU的行"].extend(
        f"SKU: {item}" for item in _items(ad_unmapped_skus)
    )
    report["有广告数据但无业务数据的行"].extend(_items(ad_only_rows))
    report["有业务数据但无广告数据的行"].extend(_items(biz_only_rows))
    report["业务订单大于0但销售额为0"].extend(_items(biz_order_no_sales, 50))
    report["广告订单大于0但销售额为0"].extend(_items(ad_order_no_sales, 50))
    report["同一日期和商品重复行"].extend(_items(dup_rows, 50))
    report["被跳过的文件及原因"].extend(_items(skipped_files))
    if ad_stats:
        report["其他问题"].append({"广告统计": dict(ad_stats)})
    for category, values in report.items():
        unique, seen = [], set()
        for value in values:
            marker = repr(value)
            if marker not in seen:
                unique.append(value)
                seen.add(marker)
        report[category] = unique
    return report


def generate_quality_report(**kwargs):
    return generate_enhanced_quality_report(
        biz_issues=kwargs.get("biz_errors"),
        ad_issues=kwargs.get("ad_errors"),
        ad_only_rows=kwargs.get("ad_only_rows"),
        mapping_errors=kwargs.get("mapping_errors"),
        skipped_files=kwargs.get("skipped_files"),
    )


def find_unmapped_asins(business_df, asin_to_sku_dict):
    if business_df is None or business_df.empty or "ASIN" not in business_df.columns:
        return set()
    return {
        asin
        for asin in business_df["ASIN"].dropna().astype(str).str.strip().unique()
        if asin and asin not in (asin_to_sku_dict or {})
    }


def check_ad_date_field(ad_df):
    if ad_df is None or ad_df.empty:
        return False, "广告数据为空"
    if "日期" not in ad_df.columns:
        return False, "缺少日期字段"
    dates = ad_df["日期"].dropna().astype(str).unique()
    return bool(len(dates)), f"含 {len(dates)} 个不同日期"


def check_inventory_quality(inventory_files, inventory_agg, inventory_detail, daily_sku, **_):
    checks = {}
    if not inventory_files:
        checks["未检测到库存报表"] = ["本次任务没有选择库存报表"]
    elif inventory_detail is None or inventory_detail.empty:
        checks["库存报表无有效行"] = [str(item) for item in inventory_files]
    return checks
