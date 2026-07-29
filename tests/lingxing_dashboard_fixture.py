# -*- coding: utf-8 -*-
"""Credential-free atomic snapshots shared by browser acceptance tests."""
from __future__ import annotations

from pathlib import Path

from agent.lingxing_sync_foundation import LingxingDatasetStore

BROWSER_FORBIDDEN_BUYER = "BUYER-BROWSER-MUST-NOT-APPEAR"


def seed_browser_dashboard_snapshots(data_root: Path) -> None:
    store = LingxingDatasetStore(data_root)
    store.commit(
        "shops",
        [
            {
                "mid": 1,
                "sid": 501,
                "seller_id": "SELLER-BROWSER-PRIVATE",
                "seller_name": "Synthetic Browser Store",
                "marketplace_id": "MARKETPLACE-BROWSER-1",
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
                "sid": 501,
                "asin": "B0BROWSER01",
                "parent_asin": "B0BROWSERP",
                "msku": "BROWSER-SKU-1",
                "lsku": "BROWSER-LSKU-1",
                "fnsku": "BROWSER-FNSKU-1",
                "product_name": "Browser Synthetic Product",
                "fulfillment_channel": "FBA",
                "status": "Active",
                "deleted": False,
                "update_time_utc": "2026-07-29T00:00:00+00:00",
            }
        ],
        mode="replace",
    )
    store.commit(
        "orders",
        [
            {
                "sid": 501,
                "amazon_order_id": "BROWSER-ORDER-1",
                "order_status": "Shipped",
                "asin": "B0BROWSER01",
                "msku": "BROWSER-SKU-1",
                "lsku": "BROWSER-LSKU-1",
                "order_qty": 3,
                "sales_amt": 59.97,
                "currency_code": "USD",
                "purchase_time_utc": "2026-07-28T03:00:00+00:00",
                "purchase_date_loc": "2026-07-28",
                "update_time_ts": 1785217200,
                "buyer_name": BROWSER_FORBIDDEN_BUYER,
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    store.commit(
        "ad_profiles",
        [{"sid": 501, "profile_id": 9001}],
        mode="replace",
    )
    store.commit(
        "ads_sp_product_daily",
        [
            {
                "report_date": "2026-07-28",
                "sid": 501,
                "profile_id": 9001,
                "campaign_id": 101,
                "ad_group_id": 102,
                "ad_id": 103,
                "asin": "B0BROWSER01",
                "msku": "BROWSER-SKU-1",
                "impressions": 100,
                "clicks": 10,
                "cost": 5,
                "orders": 1,
                "sales": 19.99,
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    store.commit(
        "ads_sb_campaign_daily",
        [
            {
                "report_date": "2026-07-28",
                "sid": 501,
                "profile_id": 9001,
                "campaign_id": 201,
                "impressions": 20,
                "clicks": 2,
                "cost": 2.75,
                "orders": 1,
                "sales": 10,
            }
        ],
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
                "sid": 501,
                "asin": "B0BROWSER01",
                "msku": "BROWSER-SKU-1",
                "lsku": "BROWSER-LSKU-1",
                "fnsku": "BROWSER-FNSKU-1",
                "afn_fulfillable_qty": 12,
                "afn_unsellable_qty": 1,
                "afn_reserved_fc_processing_qty": 1,
                "afn_reserved_fc_transfers_qty": 2,
                "afn_reserved_customer_order_qty": 3,
                "afn_inbound_working_qty": 4,
                "afn_inbound_shipped_qty": 5,
                "afn_inbound_receiving_qty": 6,
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-29", "date_to": "2026-07-29"},
    )
