# -*- coding: utf-8 -*-
"""Build a local dashboard job from already committed Lingxing snapshots.

The bridge never performs network requests. It reads only the current atomic
snapshot generations created by the 0.7 synchronization service, projects them
into the existing local-engine input contract, and runs the existing dashboard
pipeline in a normal JobStore workspace.
"""
from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from agent.job_store import JobStore
from agent.lingxing_sync_foundation import LingxingDatasetStore

LOGGER = logging.getLogger(__name__)

SOURCE_KEY = "lingxing_local_sync"
_REQUIRED_DATASETS = ("shops", "listings", "orders", "fba_inventory_snapshot")
_PRODUCT_AD_DATASETS = ("ads_sp_product_daily", "ads_sd_product_daily")
_CANCELED_TOKENS = ("cancel", "取消")


class LingxingDashboardError(RuntimeError):
    """A privacy-safe error suitable for the local UI boundary."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "none", "null", "nan"} else text


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _valid_day(value: Any) -> str:
    text = _text(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _shop_identity(row: Mapping[str, Any]) -> dict[str, str]:
    sid = _positive_int(row.get("sid"))
    if sid is None:
        raise LingxingDashboardError("本机店铺快照缺少可用店铺身份。")
    return {
        "sid": str(sid),
        "shop_id": str(sid),
        "shop_name": _text(row.get("seller_name")) or f"店铺 {sid}",
        "marketplace": (
            _text(row.get("country"))
            or _text(row.get("region"))
            or _text(row.get("marketplace_id"))
        ),
    }


def _load_snapshots(data_root: Path) -> dict[str, Any]:
    store = LingxingDatasetStore(data_root)
    snapshots: dict[str, Any] = {}
    missing = []
    for dataset in _REQUIRED_DATASETS:
        snapshot = store.load(dataset)
        if snapshot is None:
            missing.append(dataset)
        else:
            snapshots[dataset] = snapshot
    if missing:
        raise LingxingDashboardError(
            "本机经营数据尚未完整同步，请先完成一次“立即同步”。"
        )
    for dataset in (
        "ad_profiles",
        "ads_sp_product_daily",
        "ads_sb_campaign_daily",
        "ads_sd_product_daily",
    ):
        snapshots[dataset] = store.load(dataset)
    return snapshots


def _listing_indexes(
    listings: tuple[dict[str, Any], ...],
) -> tuple[
    list[dict[str, Any]],
    dict[tuple[int, str], dict[str, Any]],
    dict[tuple[int, str], dict[str, Any]],
]:
    active: list[dict[str, Any]] = []
    by_msku: dict[tuple[int, str], dict[str, Any]] = {}
    asin_groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)

    for raw in listings:
        sid = _positive_int(raw.get("sid"))
        msku = _text(raw.get("msku"))
        asin = _text(raw.get("asin"))
        if sid is None or not msku or not asin:
            continue
        deleted = raw.get("deleted")
        if deleted is True or _text(deleted).lower() in {"1", "true", "yes"}:
            continue
        row = dict(raw)
        active.append(row)
        by_msku[(sid, msku)] = row
        asin_groups[(sid, asin)].append(row)

    unique_by_asin = {
        key: rows[0]
        for key, rows in asin_groups.items()
        if len({_text(item.get("msku")) for item in rows}) == 1
    }
    if not active:
        raise LingxingDashboardError("本机商品快照没有可安全用于看板的商品行。")
    return active, by_msku, unique_by_asin


def _write_csv(
    job_store: JobStore,
    job_id: str,
    category: str,
    filename: str,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> Path:
    if not rows:
        raise LingxingDashboardError(f"{category} 看板输入为空。")
    temp_path, final_path = job_store.reserve_file_path(job_id, category, filename)
    try:
        with temp_path.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        temp_path.replace(final_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return final_path


def _mapping_rows(
    listings: list[dict[str, Any]],
    shops: dict[int, dict[str, str]],
) -> list[dict[str, Any]]:
    rows = []
    for item in listings:
        sid = int(item["sid"])
        shop = shops.get(sid)
        if shop is None:
            continue
        rows.append(
            {
                "shop_id": shop["shop_id"],
                "shop_name": shop["shop_name"],
                "marketplace": shop["marketplace"],
                "parent_asin": _text(item.get("parent_asin")),
                "seller-sku": _text(item.get("msku")),
                "MSKU": _text(item.get("msku")),
                "asin1": _text(item.get("asin")),
                "item-name": _text(item.get("product_name")),
                "fulfillment-channel": _text(item.get("fulfillment_channel")),
                "status": _text(item.get("status")),
                "price": (
                    _text(item.get("your_price"))
                    or _text(item.get("sale_price"))
                    or _text(item.get("list_price"))
                ),
                "quantity": "",
            }
        )
    if not rows:
        raise LingxingDashboardError("商品与店铺身份无法安全匹配。")
    return rows


def _base_daily_row(
    day: str,
    sid: int,
    msku: str,
    asin: str,
    listing: Mapping[str, Any],
    shop: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "日期": day,
        "shop_id": shop["shop_id"],
        "shop_name": shop["shop_name"],
        "marketplace": shop["marketplace"],
        "ASIN": asin,
        "父ASIN": _text(listing.get("parent_asin")),
        "MSKU": msku,
        "ERP内部SKU": msku,
        "标题": _text(listing.get("product_name")),
        "销售额": 0.0,
        "订单量": 0.0,
        "Sessions-Total": "",
        "PV-Total": "",
        "展示": 0.0,
        "点击": 0.0,
        "广告花费": 0.0,
        "广告销售额": 0.0,
        "广告订单量": 0.0,
    }


def _resolve_product(
    raw: Mapping[str, Any],
    by_msku: Mapping[tuple[int, str], Mapping[str, Any]],
    by_asin: Mapping[tuple[int, str], Mapping[str, Any]],
) -> tuple[int, str, str, Mapping[str, Any]] | None:
    sid = _positive_int(raw.get("sid"))
    if sid is None:
        return None
    msku = _text(raw.get("msku"))
    asin = _text(raw.get("asin"))
    listing: Mapping[str, Any] | None = None
    if msku:
        listing = by_msku.get((sid, msku))
        if listing is not None and not asin:
            asin = _text(listing.get("asin"))
    if listing is None and asin:
        listing = by_asin.get((sid, asin))
        if listing is not None and not msku:
            msku = _text(listing.get("msku"))
    if listing is None or not msku or not asin:
        return None
    if _text(listing.get("msku")) != msku or _text(listing.get("asin")) != asin:
        return None
    return sid, msku, asin, listing


def _supplemental_bucket(
    buckets: dict[tuple[str, int], dict[str, Any]],
    day: str,
    sid: int,
    shop: Mapping[str, str],
) -> dict[str, Any]:
    key = (day, sid)
    return buckets.setdefault(
        key,
        {
            "date": day,
            "shop_id": shop["shop_id"],
            "shop_name": shop["shop_name"],
            "marketplace": shop["marketplace"],
            "adImpressions": 0.0,
            "adClicks": 0.0,
            "adSpend": 0.0,
            "adOrders": 0.0,
            "adSales": 0.0,
            "sourceRows": 0,
        },
    )


def _daily_and_supplemental(
    snapshots: Mapping[str, Any],
    shops: Mapping[int, dict[str, str]],
    by_msku: Mapping[tuple[int, str], Mapping[str, Any]],
    by_asin: Mapping[tuple[int, str], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    daily: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    supplemental: dict[tuple[str, int], dict[str, Any]] = {}
    excluded_canceled = 0
    unallocated_ads = 0

    for raw in snapshots["orders"].rows:
        status = _text(raw.get("order_status")).lower()
        if any(token in status for token in _CANCELED_TOKENS):
            excluded_canceled += 1
            continue
        resolved = _resolve_product(raw, by_msku, by_asin)
        day = _valid_day(raw.get("purchase_date_loc") or raw.get("purchase_time_utc"))
        if resolved is None or not day:
            continue
        sid, msku, asin, listing = resolved
        shop = shops.get(sid)
        if shop is None:
            continue
        key = (day, sid, msku, asin)
        row = daily.setdefault(key, _base_daily_row(day, sid, msku, asin, listing, shop))
        row["订单量"] += _number(raw.get("order_qty"))
        row["销售额"] += _number(raw.get("sales_amt"))

    for dataset in _PRODUCT_AD_DATASETS:
        snapshot = snapshots.get(dataset)
        if snapshot is None:
            continue
        for raw in snapshot.rows:
            day = _valid_day(raw.get("report_date"))
            sid = _positive_int(raw.get("sid"))
            if not day or sid is None or sid not in shops:
                continue
            resolved = _resolve_product(raw, by_msku, by_asin)
            if resolved is None:
                unallocated_ads += 1
                bucket = _supplemental_bucket(supplemental, day, sid, shops[sid])
                bucket["adImpressions"] += _number(raw.get("impressions"))
                bucket["adClicks"] += _number(raw.get("clicks"))
                bucket["adSpend"] += _number(raw.get("cost"))
                bucket["adOrders"] += _number(raw.get("orders"))
                bucket["adSales"] += _number(raw.get("sales"))
                bucket["sourceRows"] += 1
                continue
            resolved_sid, msku, asin, listing = resolved
            key = (day, resolved_sid, msku, asin)
            row = daily.setdefault(
                key,
                _base_daily_row(day, resolved_sid, msku, asin, listing, shops[resolved_sid]),
            )
            row["展示"] += _number(raw.get("impressions"))
            row["点击"] += _number(raw.get("clicks"))
            row["广告花费"] += _number(raw.get("cost"))
            row["广告订单量"] += _number(raw.get("orders"))
            row["广告销售额"] += _number(raw.get("sales"))

    sb_snapshot = snapshots.get("ads_sb_campaign_daily")
    if sb_snapshot is not None:
        for raw in sb_snapshot.rows:
            day = _valid_day(raw.get("report_date"))
            sid = _positive_int(raw.get("sid"))
            if not day or sid is None or sid not in shops:
                continue
            unallocated_ads += 1
            bucket = _supplemental_bucket(supplemental, day, sid, shops[sid])
            bucket["adImpressions"] += _number(raw.get("impressions"))
            bucket["adClicks"] += _number(raw.get("clicks"))
            bucket["adSpend"] += _number(raw.get("cost"))
            bucket["adOrders"] += _number(raw.get("orders"))
            bucket["adSales"] += _number(raw.get("sales"))
            bucket["sourceRows"] += 1

    rows = [daily[key] for key in sorted(daily)]
    if not rows:
        raise LingxingDashboardError(
            "本机订单和可分配商品广告快照没有可用于看板的每日商品行。"
        )
    return (
        rows,
        [supplemental[key] for key in sorted(supplemental)],
        {
            "excluded_canceled_order_rows": excluded_canceled,
            "unallocated_ad_rows": unallocated_ads,
        },
    )


def _inventory_rows(
    snapshot: Any,
    shops: Mapping[int, dict[str, str]],
    by_msku: Mapping[tuple[int, str], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    days = [_valid_day(row.get("snapshot_date")) for row in snapshot.rows]
    latest_day = max((day for day in days if day), default="")
    if not latest_day:
        raise LingxingDashboardError("普通 FBA 库存快照缺少有效日期。")
    rows = []
    for raw in snapshot.rows:
        if _valid_day(raw.get("snapshot_date")) != latest_day:
            continue
        sid = _positive_int(raw.get("sid"))
        msku = _text(raw.get("msku"))
        if sid is None or not msku or sid not in shops:
            continue
        listing = by_msku.get((sid, msku), {})
        asin = _text(raw.get("asin")) or _text(listing.get("asin"))
        if not asin:
            continue
        reserved = (
            _number(raw.get("afn_reserved_fc_processing_qty"))
            + _number(raw.get("afn_reserved_fc_transfers_qty"))
            + _number(raw.get("afn_reserved_customer_order_qty"))
        )
        inbound = (
            _number(raw.get("afn_inbound_working_qty"))
            + _number(raw.get("afn_inbound_shipped_qty"))
            + _number(raw.get("afn_inbound_receiving_qty"))
        )
        fulfillable = _number(raw.get("afn_fulfillable_qty"))
        unsellable = _number(raw.get("afn_unsellable_qty"))
        rows.append(
            {
                "shop_id": shops[sid]["shop_id"],
                "shop_name": shops[sid]["shop_name"],
                "marketplace": shops[sid]["marketplace"],
                "MSKU": msku,
                "seller_sku": msku,
                "SKU": msku,
                "ASIN": asin,
                "产品名称": _text(raw.get("product_name")) or _text(listing.get("product_name")),
                "FBA可售库存": fulfillable,
                "FBA在途库存": inbound,
                "FBA预留库存": reserved,
                "FBA不可售库存": unsellable,
                "FBM库存": 0.0,
                "总库存": fulfillable + inbound + reserved + unsellable,
            }
        )
    if not rows:
        raise LingxingDashboardError("最新普通 FBA 库存快照没有可匹配商品行。")
    return rows, latest_day


def _context(
    supplemental: list[dict[str, Any]],
    stats: Mapping[str, int],
    latest_inventory_day: str,
    snapshots: Mapping[str, Any],
) -> dict[str, Any]:
    quality = [
        {
            "类别": "数据边界",
            "内容": "Sessions/PV 暂不可用；看板显示“暂不可用”，不会将缺失流量伪造成 0。",
        },
        {
            "类别": "订单口径",
            "内容": (
                "订单量按领星订单行的 order_qty 汇总；明确取消状态已排除；"
                "退款与退货净额冲销仍等待独立对账契约。"
            ),
        },
    ]
    unallocated = int(stats.get("unallocated_ad_rows") or 0)
    if unallocated:
        quality.append(
            {
                "类别": "广告归属",
                "内容": (
                    f"{unallocated} 条 SB 或无法安全匹配商品的广告行仅计入店铺级"
                    "广告总额和趋势，不分配到具体 SKU。"
                ),
            }
        )
    canceled = int(stats.get("excluded_canceled_order_rows") or 0)
    if canceled:
        quality.append(
            {
                "类别": "订单清理",
                "内容": f"已排除 {canceled} 条明确取消状态的订单行。",
            }
        )
    source_times = {
        dataset: snapshot.committed_at
        for dataset, snapshot in snapshots.items()
        if snapshot is not None
    }
    return {
        "meta": {
            "source_mode": SOURCE_KEY,
            "source_label": "本机领星同步快照",
            "inventory_snapshot_date": latest_inventory_day,
            "source_dataset_committed_at": source_times,
        },
        "metric_availability": {
            "sessions": {
                "status": "unavailable",
                "reason": "领星 SDK 签名契约不兼容",
            },
            "page_views": {
                "status": "unavailable",
                "reason": "领星 SDK 签名契约不兼容",
            },
        },
        "supplemental_daily": supplemental,
        "quality_notes": quality,
    }


def prepare_lingxing_dashboard_job(
    job_store: JobStore,
    data_root: Path,
) -> tuple[dict, Path, dict[str, Any]]:
    """Create and stage one normal local-engine job from current snapshots."""
    snapshots = _load_snapshots(data_root)
    shops = {}
    for raw in snapshots["shops"].rows:
        identity = _shop_identity(raw)
        shops[int(identity["sid"])] = identity
    if not shops:
        raise LingxingDashboardError("本机店铺快照为空。")

    listings, by_msku, by_asin = _listing_indexes(snapshots["listings"].rows)
    mapping_rows = _mapping_rows(listings, shops)
    daily_rows, supplemental, stats = _daily_and_supplemental(
        snapshots, shops, by_msku, by_asin
    )
    inventory_rows, inventory_day = _inventory_rows(
        snapshots["fba_inventory_snapshot"], shops, by_msku
    )

    job = job_store.create_job(
        label=f"领星本机经营看板 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        options={
            "write_excel": True,
            "write_html": True,
            "source": SOURCE_KEY,
        },
    )
    job_id = job["job_id"]
    try:
        _write_csv(
            job_store,
            job_id,
            "mapping",
            "lingxing_product_mapping.csv",
            [
                "shop_id", "shop_name", "marketplace", "parent_asin",
                "seller-sku", "MSKU", "asin1", "item-name",
                "fulfillment-channel", "status", "price", "quantity",
            ],
            mapping_rows,
        )
        _write_csv(
            job_store,
            job_id,
            "erp",
            "lingxing_daily_product_performance.csv",
            [
                "日期", "shop_id", "shop_name", "marketplace", "ASIN", "父ASIN",
                "MSKU", "ERP内部SKU", "标题", "销售额", "订单量",
                "Sessions-Total", "PV-Total", "展示", "点击", "广告花费",
                "广告销售额", "广告订单量",
            ],
            daily_rows,
        )
        _write_csv(
            job_store,
            job_id,
            "inventory",
            f"lingxing_inventory_{inventory_day.replace('-', '')}.csv",
            [
                "shop_id", "shop_name", "marketplace", "MSKU", "seller_sku",
                "SKU", "ASIN", "产品名称", "FBA可售库存", "FBA在途库存",
                "FBA预留库存", "FBA不可售库存", "FBM库存", "总库存",
            ],
            inventory_rows,
        )
        job_store.refresh_files(job_store.load_job(job_id))
        manifest = job_store.build_manifest(job_id)
    except BaseException:
        job_store.update_status(
            job_id,
            "failed",
            result=None,
            error="LingxingDashboardPreparationError",
        )
        raise

    return job_store.load_job(job_id), manifest, _context(
        supplemental,
        stats,
        inventory_day,
        snapshots,
    )


def execute_lingxing_dashboard_job(
    job_store: JobStore,
    job_id: str,
    manifest_path: Path,
    dashboard_context: Mapping[str, Any],
) -> None:
    """Run the existing engine and regenerate only its dashboard payload context."""
    job_store.update_status(job_id, "running", result=None, error=None)
    try:
        from src.dashboard_ux_patch import patch_dashboard_html_file
        from src.html_dashboard_writer import write_html_dashboard
        from src.local_engine import run_local_engine_from_manifest

        result = run_local_engine_from_manifest(manifest_path)
        if not result.output_excel:
            raise LingxingDashboardError("本机分析未生成工作簿。")
        output_dir = Path(result.output_excel).resolve().parent
        html_path, json_path = write_html_dashboard(
            result.output_excel,
            output_dir,
            dashboard_context=dict(dashboard_context),
        )
        patch_dashboard_html_file(html_path)
        result.dashboard_html = str(html_path)
        result.dashboard_json = str(json_path)
        job_store.update_status(
            job_id,
            "success",
            result=result.to_dict(),
            error=None,
        )
    except BaseException as exc:  # noqa: BLE001 - background isolation boundary
        LOGGER.error(
            "Lingxing dashboard job failed: %s: %s",
            job_id,
            type(exc).__name__,
        )
        job_store.update_status(
            job_id,
            "failed",
            result=None,
            error=f"{type(exc).__name__}: 本机领星看板生成失败。",
        )


def latest_lingxing_dashboard_job(job_store: JobStore) -> dict[str, Any] | None:
    for job in job_store.list_jobs():
        options = job.get("options") or {}
        if options.get("source") == SOURCE_KEY:
            return public_lingxing_dashboard_job(job)
    return None


def active_lingxing_dashboard_job(job_store: JobStore) -> dict[str, Any] | None:
    for job in job_store.list_jobs():
        options = job.get("options") or {}
        if (
            options.get("source") == SOURCE_KEY
            and job.get("status") in {"queued", "running"}
        ):
            return public_lingxing_dashboard_job(job)
    return None


def public_lingxing_dashboard_job(job: Mapping[str, Any]) -> dict[str, Any]:
    status_value = _text(job.get("status")) or "unknown"
    job_id = _text(job.get("job_id"))
    result = job.get("result") if isinstance(job.get("result"), Mapping) else {}
    return {
        "job_id": job_id,
        "status": status_value,
        "created_at": _text(job.get("created_at")),
        "updated_at": _text(job.get("updated_at")),
        "dashboard_url": (
            f"/v1/jobs/{job_id}/dashboard/" if status_value == "success" else ""
        ),
        "row_counts": dict(result.get("row_counts") or {}),
        "error_code": (
            _text(job.get("error")).split(":", 1)[0]
            if status_value == "failed"
            else ""
        ),
    }
