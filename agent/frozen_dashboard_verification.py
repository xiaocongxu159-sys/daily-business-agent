# -*- coding: utf-8 -*-
"""Synthetic end-to-end check for the frozen dashboard child worker."""
from __future__ import annotations

from pathlib import Path

from agent.job_store import JobStore
from agent.lingxing_dashboard_bridge import prepare_lingxing_dashboard_job
from agent.lingxing_dashboard_worker import run_dashboard_job_isolated
from agent.lingxing_sync_foundation import LingxingDatasetStore


def verify_frozen_dashboard_worker(data_root: Path) -> None:
    store = LingxingDatasetStore(data_root)
    store.commit(
        "shops",
        [
            {
                "mid": 1,
                "sid": 101,
                "seller_id": "SYNTHETIC-SELLER",
                "seller_name": "Synthetic Store",
                "marketplace_id": "SYNTHETIC-MARKET",
                "region": "NA",
                "country": "US",
                "ads_authorized": True,
            }
        ],
        mode="replace",
    )
    store.commit(
        "listings",
        [
            {
                "sid": 101,
                "asin": "B0SYNTHETIC",
                "parent_asin": "B0PARENT",
                "msku": "MSKU-1",
                "lsku": "LSKU-1",
                "fnsku": "FNSKU-1",
                "product_name": "Synthetic Product",
                "fulfillment_channel": "FBA",
                "status": "Active",
                "deleted": False,
                "update_time_utc": "2026-07-29T00:00:00+00:00",
                "your_price": 19.99,
            }
        ],
        mode="replace",
    )
    store.commit(
        "orders",
        [
            {
                "sid": 101,
                "amazon_order_id": "SYNTHETIC-ORDER-1",
                "order_status": "Shipped",
                "asin": "B0SYNTHETIC",
                "msku": "MSKU-1",
                "lsku": "LSKU-1",
                "order_qty": 2,
                "sales_amt": 39.98,
                "currency_code": "USD",
                "purchase_time_utc": "2026-07-28T01:00:00+00:00",
                "purchase_date_loc": "2026-07-28",
                "update_time_ts": 1785200000,
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    store.commit(
        "ad_profiles",
        [{"sid": 101, "profile_id": 9001}],
        mode="replace",
    )
    store.commit(
        "ads_sp_product_daily",
        [
            {
                "report_date": "2026-07-28",
                "sid": 101,
                "profile_id": 9001,
                "campaign_id": 11,
                "ad_group_id": 12,
                "ad_id": 13,
                "asin": "B0SYNTHETIC",
                "msku": "MSKU-1",
                "impressions": 100,
                "clicks": 10,
                "cost": 5.0,
                "orders": 1,
                "sales": 19.99,
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    store.commit(
        "ads_sb_campaign_daily",
        [],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    store.commit(
        "ads_sd_product_daily",
        [],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    store.commit(
        "fba_inventory_snapshot",
        [
            {
                "snapshot_date": "2026-07-29",
                "sid": 101,
                "asin": "B0SYNTHETIC",
                "msku": "MSKU-1",
                "lsku": "LSKU-1",
                "fnsku": "FNSKU-1",
                "afn_fulfillable_qty": 10,
                "afn_unsellable_qty": 1,
                "afn_reserved_fc_processing_qty": 2,
                "afn_reserved_fc_transfers_qty": 3,
                "afn_reserved_customer_order_qty": 4,
                "afn_inbound_working_qty": 5,
                "afn_inbound_shipped_qty": 6,
                "afn_inbound_receiving_qty": 7,
                "product_name": "Synthetic Product",
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-29", "date_to": "2026-07-29"},
    )

    jobs = JobStore(data_root)
    job, manifest, context = prepare_lingxing_dashboard_job(jobs, data_root)
    run_dashboard_job_isolated(
        jobs,
        job["job_id"],
        manifest,
        context,
        timeout_seconds=180,
    )
    completed = jobs.load_job(job["job_id"])
    if completed.get("status") != "success":
        raise RuntimeError(
            "frozen dashboard child did not complete: "
            + str(completed.get("error") or completed.get("status"))
        )
    result = completed.get("result") or {}
    if int((result.get("row_counts") or {}).get("daily_sku") or 0) < 1:
        raise RuntimeError("frozen dashboard child produced no daily rows")
    if not Path(str(result.get("dashboard_html") or "")).is_file():
        raise RuntimeError("frozen dashboard child did not create HTML")
