# -*- coding: utf-8 -*-
"""Load user-provided monthly goals without changing actual business data."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass
class PlanLoadResult:
    plan_summary_df: pd.DataFrame
    daily_plan_df: pd.DataFrame
    sku_plan_df: pd.DataFrame
    action_plan_df: pd.DataFrame
    plan_inventory_df: pd.DataFrame
    errors: list[str]


def _empty(errors=None) -> PlanLoadResult:
    return PlanLoadResult(
        pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), errors or []
    )


def find_plan_files(input_dir: str | Path) -> list[Path]:
    root = Path(input_dir)
    return sorted(
        path
        for path in root.glob("*.xlsx")
        if path.is_file() and not path.name.startswith("~$") and "计划" in path.name
    )


def _sheet(excel: pd.ExcelFile, prefix: str, header_row: int) -> pd.DataFrame:
    name = next((item for item in excel.sheet_names if item.startswith(prefix)), None)
    if name is None:
        return pd.DataFrame()
    frame = pd.read_excel(excel, sheet_name=name, header=header_row - 1, dtype=object)
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame.dropna(how="all").reset_index(drop=True)


def _number(series: pd.Series) -> pd.Series:
    text = (
        series.fillna("").astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(text, errors="coerce")


def _date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")


def _column(frame: pd.DataFrame, name: str, default="") -> pd.Series:
    if name in frame.columns:
        return frame[name]
    return pd.Series(default, index=frame.index)


def load_monthly_plan(path: str | Path) -> PlanLoadResult:
    path = Path(path)
    try:
        excel = pd.ExcelFile(path)
        summary_raw = _sheet(excel, "03_", 3)
        daily_raw = _sheet(excel, "05_", 3)
        sku_raw = _sheet(excel, "11_", 3)
        action_raw = _sheet(excel, "13_", 3)
        inventory_raw = _sheet(excel, "14_", 6)
    except Exception as exc:
        return _empty([f"{path.name}: {exc}"])

    summary = pd.DataFrame({
        "模块": _column(summary_raw, "模块"),
        "指标": _column(summary_raw, "指标"),
        "目标值": _number(_column(summary_raw, "本月目标/输入")),
        "上月实际": _number(_column(summary_raw, "上月实际/基线")),
        "差额": _number(_column(summary_raw, "差额")),
        "增长率/占比": _number(_column(summary_raw, "增长率/占比")),
        "说明": _column(summary_raw, "说明"),
        "负责人": _column(summary_raw, "责任人"),
    }) if not summary_raw.empty else pd.DataFrame()
    if not summary.empty:
        summary = summary[summary["指标"].fillna("").astype(str).str.strip().ne("")].reset_index(drop=True)

    daily = pd.DataFrame({
        "日期": _date(_column(daily_raw, "日期")),
        "星期": _column(daily_raw, "星期"),
        "销售额目标": _number(_column(daily_raw, "销售额目标")),
        "订单目标": _number(_column(daily_raw, "订单目标")),
        "广告预算": _number(_column(daily_raw, "广告预算")),
        "广告销售额目标": _number(_column(daily_raw, "广告销售额目标")),
        "备注": _column(daily_raw, "备注"),
    }) if not daily_raw.empty else pd.DataFrame()
    if not daily.empty:
        daily = daily[daily["日期"].notna()].reset_index(drop=True)

    sku = sku_raw.copy()
    if not sku.empty:
        sku["SKU"] = _column(sku, "SKU").fillna("").astype(str).str.strip()
        if "月份" in sku.columns:
            sku["月份"] = _date(sku["月份"])
        for column in [
            "上月销售额", "上月订单量", "上月Sessions", "上月转化率", "上月广告销售额",
            "上月广告花费", "上月ACOS", "上月TACOS", "本月销售目标", "目标增长率",
            "日均目标销售额", "目标广告花费", "本月实际销售额", "本月实际订单量",
            "本月广告花费", "本月ACOS", "达成率", "销售缺口",
        ]:
            sku[column] = _number(_column(sku, column))
        sku = sku[sku["SKU"].ne("")].reset_index(drop=True)

    action = action_raw.copy()
    if not action.empty:
        action["SKU"] = _column(action, "SKU").fillna("").astype(str).str.strip()
        for column in ["月份", "开始日期", "截止日期"]:
            if column in action.columns:
                action[column] = _date(action[column])
        action = action[action["SKU"].ne("")].reset_index(drop=True)

    inventory = inventory_raw.copy()
    if not inventory.empty:
        inventory["SKU"] = _column(inventory, "SKU").fillna("").astype(str).str.strip()
        if "月份" in inventory.columns:
            inventory["月份"] = _date(inventory["月份"])
        inventory = inventory[inventory["SKU"].ne("")].reset_index(drop=True)

    return PlanLoadResult(summary, daily, sku, action, inventory, [])


def load_all_monthly_plans(input_dir: str | Path) -> PlanLoadResult:
    files = find_plan_files(input_dir)
    if not files:
        return _empty(["未找到月度计划表"])
    results = [load_monthly_plan(path) for path in files]
    return PlanLoadResult(
        pd.concat([item.plan_summary_df for item in results if not item.plan_summary_df.empty], ignore_index=True) if any(not item.plan_summary_df.empty for item in results) else pd.DataFrame(),
        pd.concat([item.daily_plan_df for item in results if not item.daily_plan_df.empty], ignore_index=True) if any(not item.daily_plan_df.empty for item in results) else pd.DataFrame(),
        pd.concat([item.sku_plan_df for item in results if not item.sku_plan_df.empty], ignore_index=True) if any(not item.sku_plan_df.empty for item in results) else pd.DataFrame(),
        pd.concat([item.action_plan_df for item in results if not item.action_plan_df.empty], ignore_index=True) if any(not item.action_plan_df.empty for item in results) else pd.DataFrame(),
        pd.concat([item.plan_inventory_df for item in results if not item.plan_inventory_df.empty], ignore_index=True) if any(not item.plan_inventory_df.empty for item in results) else pd.DataFrame(),
        [error for item in results for error in item.errors],
    )
