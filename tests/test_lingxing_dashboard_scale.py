# -*- coding: utf-8 -*-
"""Production-sized regression for local Lingxing dashboard generation."""
from __future__ import annotations

import multiprocessing
import os
import time
from pathlib import Path

from agent.job_store import JobStore
from agent.lingxing_dashboard_bridge import (
    execute_lingxing_dashboard_job,
    prepare_lingxing_dashboard_job,
)
from agent.lingxing_sync_foundation import LingxingDatasetStore


def _seed_realistic_snapshots(data_root: Path) -> None:
    store = LingxingDatasetStore(data_root)
    shops = []
    listings = []
    for index in range(17):
        sid = 501 + index
        shops.append({
            "mid": 1 + index,
            "sid": sid,
            "seller_id": f"SELLER-{index + 1}",
            "seller_name": f"Synthetic Store {index + 1}",
            "marketplace_id": f"MARKET-{index + 1}",
            "region": "NA",
            "country": "US",
            "ads_authorized": index < 14,
        })
    for index in range(1074):
        sid = 501 + (index % 17)
        listings.append({
            "sid": sid,
            "asin": f"B0{index:08d}",
            "parent_asin": f"P0{index // 4:08d}",
            "msku": f"MSKU-{index:05d}",
            "lsku": f"LSKU-{index:05d}",
            "fnsku": f"FNSKU-{index:05d}",
            "product_name": f"Synthetic Product {index}",
            "fulfillment_channel": "FBA",
            "status": "Active",
            "deleted": False,
            "update_time_utc": "2026-07-29T00:00:00+00:00",
            "your_price": 19.99,
        })
    orders = []
    days = ("2026-07-25", "2026-07-26", "2026-07-27", "2026-07-28")
    for index in range(7049):
        item = listings[index % len(listings)]
        orders.append({
            "sid": item["sid"],
            "amazon_order_id": f"ORDER-{index:07d}",
            "order_status": "Shipped",
            "asin": item["asin"],
            "msku": item["msku"],
            "lsku": item["lsku"],
            "order_qty": 1,
            "sales_amt": 19.99,
            "currency_code": "USD",
            "purchase_date_loc": days[index % len(days)],
            "purchase_time_utc": f"{days[index % len(days)]}T12:00:00+00:00",
            "update_time_ts": 1785000000 + index,
        })
    profiles = [
        {"sid": 501 + index, "profile_id": 9000 + index}
        for index in range(14)
    ]
    sp_rows = []
    for index in range(2022):
        item = listings[index % len(listings)]
        sp_rows.append({
            "report_date": days[index % len(days)],
            "sid": item["sid"],
            "profile_id": 9000 + (item["sid"] - 501) % 14,
            "campaign_id": 100000 + index,
            "ad_group_id": 200000 + index,
            "ad_id": 300000 + index,
            "asin": item["asin"],
            "msku": item["msku"],
            "impressions": 100,
            "clicks": 10,
            "cost": 5.5,
            "orders": 1,
            "sales": 19.99,
        })
    sb_rows = [
        {
            "report_date": days[index % len(days)],
            "sid": 501 + (index % 14),
            "profile_id": 9000 + (index % 14),
            "campaign_id": 400000 + index,
            "impressions": 50,
            "clicks": 5,
            "cost": 2.75,
            "orders": 1,
            "sales": 12.0,
        }
        for index in range(32)
    ]
    sd_rows = []
    for index in range(2):
        item = listings[index]
        sd_rows.append({
            "report_date": "2026-07-28",
            "sid": item["sid"],
            "profile_id": 9000 + index,
            "campaign_id": 500000 + index,
            "ad_group_id": 600000 + index,
            "ad_id": 700000 + index,
            "asin": item["asin"],
            "msku": item["msku"],
            "impressions": 25,
            "clicks": 2,
            "cost": 1.25,
            "orders": 0,
            "sales": 8.0,
        })
    inventory = []
    for index, item in enumerate(listings[:826]):
        inventory.append({
            "snapshot_date": "2026-07-29",
            "sid": item["sid"],
            "asin": item["asin"],
            "msku": item["msku"],
            "lsku": item["lsku"],
            "fnsku": item["fnsku"],
            "afn_fulfillable_qty": 10 + index % 20,
            "afn_unsellable_qty": 1,
            "afn_reserved_fc_processing_qty": 2,
            "afn_reserved_fc_transfers_qty": 3,
            "afn_reserved_customer_order_qty": 4,
            "afn_inbound_working_qty": 5,
            "afn_inbound_shipped_qty": 6,
            "afn_inbound_receiving_qty": 7,
            "product_name": item["product_name"],
        })

    store.commit("shops", shops, mode="replace")
    store.commit("listings", listings, mode="replace")
    store.commit("orders", orders, mode="replace", checkpoint={"date_from": days[0], "date_to": days[-1]})
    store.commit("ad_profiles", profiles, mode="replace")
    store.commit("ads_sp_product_daily", sp_rows, mode="replace", checkpoint={"date_from": days[0], "date_to": days[-1]})
    store.commit("ads_sb_campaign_daily", sb_rows, mode="replace", checkpoint={"date_from": days[0], "date_to": days[-1]})
    store.commit("ads_sd_product_daily", sd_rows, mode="replace", checkpoint={"date_from": days[0], "date_to": days[-1]})
    store.commit("fba_inventory_snapshot", inventory, mode="replace", checkpoint={"date_from": "2026-07-29", "date_to": "2026-07-29"})


def _run_job(data_root: str, job_id: str, manifest: str, context: dict) -> None:
    execute_lingxing_dashboard_job(JobStore(Path(data_root)), job_id, Path(manifest), context)


def test_realistic_snapshot_dashboard_finishes_within_three_minutes(tmp_path: Path) -> None:
    data_root = tmp_path / "agent-data"
    _seed_realistic_snapshots(data_root)
    jobs = JobStore(data_root)
    job, manifest, context = prepare_lingxing_dashboard_job(jobs, data_root)

    started = time.monotonic()
    method = "spawn" if os.name == "nt" else "fork"
    process = multiprocessing.get_context(method).Process(
        target=_run_job,
        args=(str(data_root), job["job_id"], str(manifest), context),
        daemon=True,
    )
    process.start()
    process.join(timeout=180)
    elapsed = time.monotonic() - started
    if process.is_alive():
        process.terminate()
        process.join(timeout=10)
        raise AssertionError(f"realistic dashboard generation exceeded 180 seconds (elapsed={elapsed:.1f})")
    assert process.exitcode == 0
    completed = jobs.load_job(job["job_id"])
    assert completed["status"] == "success", completed.get("error")
    assert completed["result"]["row_counts"]["daily_sku"] >= 4000
    assert elapsed < 180
