# -*- coding: utf-8 -*-
"""Synthetic privacy and reconciliation tests for the local snapshot dashboard."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agent.job_store import JobStore
from agent.lingxing_dashboard_bridge import (
    LingxingDashboardError,
    execute_lingxing_dashboard_job,
    prepare_lingxing_dashboard_job,
)
from agent.lingxing_sync_foundation import LingxingDatasetStore


FORBIDDEN_VALUES = (
    "BUYER-NAME-MUST-NOT-APPEAR",
    "buyer@example.invalid",
    "PRIVATE-CITY-MUST-NOT-APPEAR",
    "99999-PRIVATE",
)


def _seed_snapshots(data_root: Path) -> None:
    store = LingxingDatasetStore(data_root)
    store.commit(
        "shops",
        [
            {
                "mid": 1,
                "sid": 501,
                "seller_id": "SELLER-PRIVATE-MUST-NOT-APPEAR",
                "seller_name": "Synthetic Store",
                "marketplace_id": "ATVPDKIKX0DER",
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
                "sid": 501,
                "amazon_order_id": "ORDER-NORMAL",
                "order_status": "Shipped",
                "asin": "B0SYNTHETIC",
                "msku": "MSKU-1",
                "lsku": "LSKU-1",
                "order_qty": 2,
                "sales_amt": 39.98,
                "currency_code": "USD",
                "purchase_time_utc": "2026-07-28T03:00:00+00:00",
                "purchase_date_loc": "2026-07-28",
                "update_time_ts": 1785217200,
                "buyer_name": FORBIDDEN_VALUES[0],
                "buyer_email": FORBIDDEN_VALUES[1],
                "buyer_city": FORBIDDEN_VALUES[2],
                "buyer_postcode": FORBIDDEN_VALUES[3],
            },
            {
                "sid": 501,
                "amazon_order_id": "ORDER-CANCELED",
                "order_status": "Canceled",
                "asin": "B0SYNTHETIC",
                "msku": "MSKU-1",
                "lsku": "LSKU-1",
                "order_qty": 99,
                "sales_amt": 9999,
                "currency_code": "USD",
                "purchase_time_utc": "2026-07-28T04:00:00+00:00",
                "purchase_date_loc": "2026-07-28",
                "update_time_ts": 1785220800,
            },
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-25", "date_to": "2026-07-28"},
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
                "campaign_id": 11,
                "ad_group_id": 12,
                "ad_id": 13,
                "asin": "B0SYNTHETIC",
                "msku": "MSKU-1",
                "impressions": 100,
                "clicks": 10,
                "cost": 5.5,
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
                "campaign_id": 21,
                "impressions": 50,
                "clicks": 5,
                "cost": 2.75,
                "orders": 1,
                "sales": 12,
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    store.commit(
        "ads_sd_product_daily",
        [
            {
                "report_date": "2026-07-28",
                "sid": 501,
                "profile_id": 9001,
                "campaign_id": 31,
                "ad_group_id": 32,
                "ad_id": 33,
                "asin": "B0UNMAPPED",
                "impressions": 25,
                "clicks": 2,
                "cost": 1.25,
                "orders": 0,
                "sales": 8,
            }
        ],
        mode="replace",
        checkpoint={"date_from": "2026-07-28", "date_to": "2026-07-28"},
    )
    inventory_rows = []
    for day, fulfillable in (("2026-07-28", 1), ("2026-07-29", 10)):
        inventory_rows.append(
            {
                "snapshot_date": day,
                "sid": 501,
                "asin": "B0SYNTHETIC",
                "msku": "MSKU-1",
                "lsku": "LSKU-1",
                "fnsku": "FNSKU-1",
                "afn_fulfillable_qty": fulfillable,
                "afn_unsellable_qty": 1,
                "afn_reserved_fc_processing_qty": 2,
                "afn_reserved_fc_transfers_qty": 3,
                "afn_reserved_customer_order_qty": 4,
                "afn_inbound_working_qty": 5,
                "afn_inbound_shipped_qty": 6,
                "afn_inbound_receiving_qty": 7,
                "product_name": "Synthetic Product",
            }
        )
    store.commit(
        "fba_inventory_snapshot",
        inventory_rows,
        mode="replace",
        checkpoint={"date_from": "2026-07-29", "date_to": "2026-07-29"},
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_bridge_stages_only_safe_local_inputs_and_latest_inventory(tmp_path: Path) -> None:
    data_root = tmp_path / "agent-data"
    _seed_snapshots(data_root)
    jobs = JobStore(data_root)

    job, manifest_path, context = prepare_lingxing_dashboard_job(jobs, data_root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    job_dir = jobs._job_dir(job["job_id"])

    mapping_rows = _read_csv(job_dir / manifest["mapping_file"])
    erp_rows = _read_csv(job_dir / manifest["erp_files"][0])
    inventory_path = job_dir / manifest["inventory_files"][0]
    inventory_rows = _read_csv(inventory_path)

    assert len(mapping_rows) == 1
    assert mapping_rows[0]["shop_id"] == "501"
    assert mapping_rows[0]["MSKU"] == "MSKU-1"
    assert "SELLER-PRIVATE-MUST-NOT-APPEAR" not in json.dumps(mapping_rows)

    normal = next(row for row in erp_rows if row["日期"] == "2026-07-28")
    assert float(normal["订单量"]) == pytest.approx(2)
    assert float(normal["销售额"]) == pytest.approx(39.98)
    assert float(normal["广告花费"]) == pytest.approx(5.5)
    assert float(normal["广告销售额"]) == pytest.approx(19.99)
    assert normal["Sessions-Total"] == ""
    assert normal["PV-Total"] == ""
    assert "ORDER-CANCELED" not in json.dumps(erp_rows)

    assert "20260729" in inventory_path.name
    assert len(inventory_rows) == 1
    inventory = inventory_rows[0]
    assert float(inventory["FBA可售库存"]) == pytest.approx(10)
    assert float(inventory["FBA预留库存"]) == pytest.approx(9)
    assert float(inventory["FBA在途库存"]) == pytest.approx(18)
    assert float(inventory["总库存"]) == pytest.approx(38)

    supplemental = context["supplemental_daily"]
    assert len(supplemental) == 1
    assert supplemental[0]["date"] == "2026-07-28"
    assert supplemental[0]["adSpend"] == pytest.approx(4.0)
    assert supplemental[0]["adSales"] == pytest.approx(20.0)
    assert supplemental[0]["sourceRows"] == 2
    assert context["metric_availability"]["sessions"]["status"] == "unavailable"
    assert context["metric_availability"]["page_views"]["status"] == "unavailable"

    encoded = json.dumps(
        {"mapping": mapping_rows, "erp": erp_rows, "inventory": inventory_rows, "context": context},
        ensure_ascii=False,
    )
    for forbidden in FORBIDDEN_VALUES + ("SELLER-PRIVATE-MUST-NOT-APPEAR",):
        assert forbidden not in encoded


def test_bridge_runs_existing_engine_and_marks_unavailable_metrics(tmp_path: Path) -> None:
    data_root = tmp_path / "agent-data"
    _seed_snapshots(data_root)
    jobs = JobStore(data_root)
    job, manifest, context = prepare_lingxing_dashboard_job(jobs, data_root)

    execute_lingxing_dashboard_job(jobs, job["job_id"], manifest, context)

    completed = jobs.load_job(job["job_id"])
    assert completed["status"] == "success"
    result = completed["result"]
    dashboard_json = Path(result["dashboard_json"])
    dashboard_html = Path(result["dashboard_html"])
    payload = json.loads(dashboard_json.read_text(encoding="utf-8"))
    html = dashboard_html.read_text(encoding="utf-8")

    assert payload["meta"]["source_mode"] == "lingxing_local_sync"
    assert payload["meta"]["source_label"] == "本机领星同步快照"
    assert payload["metric_availability"]["sessions"]["status"] == "unavailable"
    assert payload["metric_availability"]["page_views"]["status"] == "unavailable"
    assert payload["supplemental_daily"][0]["adSpend"] == pytest.approx(4.0)

    daily_28 = next(row for row in payload["daily"] if row["日期"] == "2026-07-28")
    assert daily_28["Sessions"] is None
    assert daily_28["PV"] is None
    assert float(daily_28["销售额"]) == pytest.approx(39.98)
    assert float(daily_28["广告花费"]) == pytest.approx(5.5)

    inventory_day = [row for row in payload["daily"] if row["日期"] == "2026-07-29"]
    assert inventory_day, "latest inventory snapshot day must appear in dashboard rows"
    assert any(float(row["FBA可售库存"]) == pytest.approx(10) for row in inventory_day)

    assert "Sessions / PV 暂不可用" in html
    assert "不会将缺失数据绘制为 0" in html
    assert "data-axis-upright" in html
    assert "data-chart-tooltip" in html
    for forbidden in FORBIDDEN_VALUES + ("SELLER-PRIVATE-MUST-NOT-APPEAR",):
        assert forbidden not in dashboard_json.read_text(encoding="utf-8")
        assert forbidden not in html


def test_bridge_fails_closed_before_creating_dashboard_without_required_snapshot(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "agent-data"
    store = LingxingDatasetStore(data_root)
    store.commit(
        "shops",
        [
            {
                "mid": 1,
                "sid": 501,
                "seller_id": "SELLER",
                "seller_name": "Synthetic Store",
                "marketplace_id": "ATVPDKIKX0DER",
                "region": "NA",
                "country": "US",
                "ads_authorized": True,
            }
        ],
        mode="replace",
    )

    with pytest.raises(LingxingDashboardError, match="尚未完整同步"):
        prepare_lingxing_dashboard_job(JobStore(data_root), data_root)
