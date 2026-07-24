# -*- coding: utf-8 -*-
"""Pure local-file execution entrypoint for the daily business data engine.

The module performs no network requests. A local agent must first stage all
selected files inside one job workspace, then pass explicit paths here.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

SUPPORTED_INPUT_SUFFIXES = {".csv", ".txt", ".tsv", ".xlsx", ".xls"}


@dataclass(frozen=True)
class LocalEngineRequest:
    workspace: Path
    mapping_file: Path
    erp_files: tuple[Path, ...] = ()
    business_files: tuple[Path, ...] = ()
    ad_files: tuple[Path, ...] = ()
    inventory_files: tuple[Path, ...] = ()
    plan_files: tuple[Path, ...] = ()
    include_history: bool = False
    write_excel: bool = True
    write_html: bool = True

    @classmethod
    def from_dict(cls, payload: dict, base_dir: Path | None = None) -> "LocalEngineRequest":
        manifest_base = Path(base_dir).resolve() if base_dir is not None else Path.cwd().resolve()
        workspace = Path(payload["workspace"])
        if not workspace.is_absolute():
            workspace = manifest_base / workspace
        workspace = workspace.resolve()

        def local_path(value: str | Path) -> Path:
            path = Path(value)
            return path if path.is_absolute() else workspace / path

        def paths(key: str) -> tuple[Path, ...]:
            return tuple(local_path(value) for value in payload.get(key, []) or [])

        return cls(
            workspace=workspace,
            mapping_file=local_path(payload["mapping_file"]),
            erp_files=paths("erp_files"),
            business_files=paths("business_files"),
            ad_files=paths("ad_files"),
            inventory_files=paths("inventory_files"),
            plan_files=paths("plan_files"),
            include_history=bool(payload.get("include_history", False)),
            write_excel=bool(payload.get("write_excel", True)),
            write_html=bool(payload.get("write_html", True)),
        )


@dataclass
class LocalEngineResult:
    status: str
    mode: str
    workspace: str
    output_excel: str | None = None
    dashboard_html: str | None = None
    dashboard_json: str | None = None
    row_counts: dict[str, int] = field(default_factory=dict)
    issues: list[dict | str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _resolved_inside(workspace: Path, path: Path, label: str) -> Path:
    resolved_workspace = workspace.resolve()
    resolved = path.resolve()
    if resolved != resolved_workspace and resolved_workspace not in resolved.parents:
        raise ValueError(f"{label} must be inside workspace: {resolved}")
    return resolved


def _validate_file(workspace: Path, path: Path, label: str) -> Path:
    resolved = _resolved_inside(workspace, path, label)
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    if resolved.name.startswith("~$"):
        raise ValueError(f"{label} is a temporary Office file: {resolved.name}")
    if resolved.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
        raise ValueError(f"{label} has unsupported suffix: {resolved.suffix}")
    return resolved


def validate_request(request: LocalEngineRequest) -> LocalEngineRequest:
    workspace = request.workspace.resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(f"workspace not found: {workspace}")

    mapping = _validate_file(workspace, request.mapping_file, "mapping_file")
    erp = tuple(_validate_file(workspace, path, "erp_file") for path in request.erp_files)
    business = tuple(
        _validate_file(workspace, path, "business_file") for path in request.business_files
    )
    ads = tuple(_validate_file(workspace, path, "ad_file") for path in request.ad_files)
    inventory = tuple(
        _validate_file(workspace, path, "inventory_file") for path in request.inventory_files
    )
    plans = tuple(_validate_file(workspace, path, "plan_file") for path in request.plan_files)

    if erp and (business or ads):
        raise ValueError("erp_files cannot be combined with business_files/ad_files")
    if not erp and not business and not ads:
        raise ValueError("provide erp_files or at least one business/ad file")
    if request.write_html and not request.write_excel:
        raise ValueError("write_html requires write_excel")

    return LocalEngineRequest(
        workspace=workspace,
        mapping_file=mapping,
        erp_files=erp,
        business_files=business,
        ad_files=ads,
        inventory_files=inventory,
        plan_files=plans,
        include_history=request.include_history,
        write_excel=request.write_excel,
        write_html=request.write_html,
    )


def _combine_plan_files(paths: Iterable[Path]) -> tuple[dict, list[str]]:
    import pandas as pd

    from src.plan_loader import load_monthly_plan

    results = [load_monthly_plan(path) for path in paths]
    if not results:
        return {}, []

    def combine(attribute: str):
        frames = [getattr(result, attribute) for result in results]
        frames = [frame for frame in frames if frame is not None and not frame.empty]
        return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()

    errors = [error for result in results for error in result.errors]
    return {
        "plan_summary_df": combine("plan_summary_df"),
        "daily_plan_df": combine("daily_plan_df"),
        "sku_plan_df": combine("sku_plan_df"),
        "action_plan_df": combine("action_plan_df"),
        "plan_inventory_df": combine("plan_inventory_df"),
    }, errors


def _date_range(frame):
    import pandas as pd

    if frame is None or frame.empty:
        return None
    for column in ("日期", "Date"):
        if column in frame.columns:
            values = pd.to_datetime(frame[column], errors="coerce").dropna()
            if not values.empty:
                return values.min(), values.max()
    return None


def run_local_engine(request: LocalEngineRequest) -> LocalEngineResult:
    import pandas as pd

    from src.ad_report_loader import get_ad_quality_checks, load_all_ad_reports, process_ad_for_merge
    from src.business_report_loader import load_all_business_reports
    from src.data_identity import standardize_identity_fields
    from src.data_merger import (
        _normalize_ad_agg_cols,
        aggregate_business_by_sku,
        check_ad_order_no_sales,
        check_biz_order_no_sales,
        check_duplicate_rows,
        find_ad_only_rows,
        find_biz_only_rows,
        generate_full_sku_date_grid,
        merge_business_and_ad,
        merge_inventory_to_daily,
        merge_onto_full_grid,
    )
    from src.erp_report_loader import load_all_erp_reports
    from src.excel_writer import write_output_excel
    from src.html_dashboard_writer import write_html_dashboard
    from src.inventory_report_loader import aggregate_inventory, load_inventory_report
    from src.product_mapping_loader import build_sku_asin_dict, load_mapping
    from src.quality_checker import generate_enhanced_quality_report

    request = validate_request(request)
    workspace = request.workspace
    output_dir = workspace / "output"
    runtime_data_dir = workspace / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_data_dir.mkdir(parents=True, exist_ok=True)

    mapping_df, mapping_issues = load_mapping(request.mapping_file)
    if mapping_df is None or mapping_df.empty:
        raise ValueError(f"mapping file produced no usable rows: {mapping_issues}")
    mapping_df = standardize_identity_fields(mapping_df, log_default=False)
    asin_to_sku = build_sku_asin_dict(mapping_df)

    issues: list[dict | str] = list(mapping_issues or [])
    mode = "erp" if request.erp_files else "separate_reports"
    if request.erp_files:
        business_raw, ad_raw, load_issues = load_all_erp_reports(
            request.erp_files,
            mapping_df=mapping_df,
            history_output_dir=output_dir,
            include_history=request.include_history,
        )
        issues.extend(load_issues or [])
    else:
        business_raw, business_issues = load_all_business_reports(
            request.business_files,
            mapping_df,
        )
        ad_raw, ad_issues = load_all_ad_reports(request.ad_files)
        issues.extend(business_issues or [])
        issues.extend(ad_issues or [])

    if (business_raw is None or business_raw.empty) and (ad_raw is None or ad_raw.empty):
        raise ValueError("no usable business or advertising rows were loaded")

    business_agg = (
        aggregate_business_by_sku(business_raw)
        if business_raw is not None
        else pd.DataFrame()
    )
    ad_agg, ad_detail, ad_unmapped, ad_stats = process_ad_for_merge(ad_raw, asin_to_sku)
    if ad_agg is not None and not ad_agg.empty:
        ad_agg = _normalize_ad_agg_cols(ad_agg)
    ad_checks = get_ad_quality_checks(ad_raw, ad_agg, ad_unmapped, asin_to_sku)

    daily_sku = merge_business_and_ad(business_agg, ad_agg, mapping_df)
    grid_result = generate_full_sku_date_grid(mapping_df, business_raw, ad_raw, None)
    if isinstance(grid_result, tuple):
        full_grid = grid_result[0]
        if full_grid is not None and not full_grid.empty:
            daily_sku = merge_onto_full_grid(
                full_grid,
                business_agg,
                ad_agg,
                mapping_df,
                None,
            )

    inventory_agg = None
    inventory_detail = None
    inventory_frames = []
    for path in request.inventory_files:
        frame = load_inventory_report(path, None, mapping_df)
        if frame is not None and not frame.empty:
            inventory_frames.append(frame)
    if inventory_frames:
        inventory_agg, inventory_detail = aggregate_inventory(inventory_frames)
    daily_sku, inventory_issues = merge_inventory_to_daily(
        daily_sku,
        inventory_agg,
        inventory_detail,
    )
    issues.extend(inventory_issues or [])

    plan_data, plan_issues = _combine_plan_files(request.plan_files)
    issues.extend(plan_issues)

    business_names = {path.name for path in request.business_files}
    ad_names = {path.name for path in request.ad_files}
    quality_report = generate_enhanced_quality_report(
        biz_issues=[
            item
            for item in issues
            if isinstance(item, dict) and item.get("file") in business_names
        ],
        ad_issues=[
            item
            for item in issues
            if isinstance(item, dict) and item.get("file") in ad_names
        ],
        ad_stats=ad_stats,
        ad_only_rows=find_ad_only_rows(daily_sku),
        biz_only_rows=find_biz_only_rows(daily_sku),
        biz_order_no_sales=check_biz_order_no_sales(business_agg),
        ad_order_no_sales=check_ad_order_no_sales(ad_agg),
        dup_rows=check_duplicate_rows(daily_sku),
        ad_unmapped_skus=ad_unmapped,
        skipped_files=[],
    )

    result = LocalEngineResult(
        status="success",
        mode=mode,
        workspace=str(workspace),
        row_counts={
            "mapping": len(mapping_df),
            "business_raw": 0 if business_raw is None else len(business_raw),
            "ad_raw": 0 if ad_raw is None else len(ad_raw),
            "daily_sku": len(daily_sku),
            "inventory_detail": 0 if inventory_detail is None else len(inventory_detail),
        },
        issues=issues,
    )

    if request.write_excel:
        excel_path = write_output_excel(
            daily_sku=daily_sku,
            business_detail=business_raw,
            ad_detail=ad_detail,
            mapping_df=mapping_df,
            ad_agg_df=ad_agg,
            quality_report=quality_report,
            ad_quality_checks=ad_checks,
            biz_date_range=_date_range(business_raw),
            ad_date_range=_date_range(ad_raw),
            inventory_agg=inventory_agg,
            inventory_detail=inventory_detail,
            plan_data=plan_data,
            output_dir=output_dir,
            runtime_data_dir=runtime_data_dir,
        )
        result.output_excel = str(excel_path)
        if request.write_html:
            html_path, json_path = write_html_dashboard(excel_path, output_dir)
            result.dashboard_html = str(html_path)
            result.dashboard_json = str(json_path)

    result_path = workspace / "job_result.json"
    result_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def run_local_engine_from_manifest(manifest_path: str | Path) -> LocalEngineResult:
    manifest = Path(manifest_path).resolve()
    if not manifest.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest}")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return run_local_engine(
        LocalEngineRequest.from_dict(payload, base_dir=manifest.parent)
    )
