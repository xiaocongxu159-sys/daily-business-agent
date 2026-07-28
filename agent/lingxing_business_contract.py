# -*- coding: utf-8 -*-
"""Credential-free contract for future Lingxing business-data synchronization.

The module contains no network client and no customer data.  It fixes the
public endpoint, grain, identity, pagination and validation rules that must be
satisfied before live account calls are introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ContractStatus(str, Enum):
    CONFIRMED_SDK = "confirmed_sdk"
    REQUIRES_SDK_EXTENSION = "requires_sdk_extension"
    REQUIRES_CONTROLLED_VALIDATION = "requires_controlled_validation"
    NOT_EXPOSED = "not_exposed"


class PaginationKind(str, Enum):
    NONE = "none"
    OFFSET = "offset_length"
    NEXT_TOKEN = "next_token"
    ASYNC_TASK = "async_task"


@dataclass(frozen=True)
class DatasetContract:
    key: str
    source_method: str
    endpoint: str
    status: ContractStatus
    grain: str
    identity_fields: tuple[str, ...]
    required_output_fields: tuple[str, ...]
    pagination: PaginationKind
    date_rule: str
    overlap_days: int
    notes: tuple[str, ...] = ()
    report_type: str = ""
    report_options: tuple[tuple[str, str], ...] = ()

    def options(self) -> Mapping[str, str]:
        return dict(self.report_options)


SHOP_IDENTITY_FIELDS = ("sid", "seller_id", "marketplace_id", "region")
PRODUCT_IDENTITY_FIELDS = ("sid", "msku", "asin", "fnsku", "lsku")
AD_IDENTITY_FIELDS = ("sid", "profile_id")

SALES_TRAFFIC_REPORT_TYPE = "GET_SALES_AND_TRAFFIC_REPORT"
SALES_TRAFFIC_REPORT_OPTIONS = (
    ("dateGranularity", "DAY"),
    ("asinGranularity", "SKU"),
)


BUSINESS_DATASETS: dict[str, DatasetContract] = {
    "shops": DatasetContract(
        "shops",
        "api.basic.Sellers",
        "/erp/sc/data/seller/lists",
        ContractStatus.CONFIRMED_SDK,
        "one row per Lingxing seller/site",
        SHOP_IDENTITY_FIELDS,
        ("mid", "sid", "seller_id", "seller_name", "marketplace_id", "region", "country", "ads_authorized"),
        PaginationKind.NONE,
        "dimension snapshot; retain the last successful snapshot",
        0,
    ),
    "listings": DatasetContract(
        "listings",
        "api.sales.Listings",
        "/erp/sc/data/mws/listing",
        ContractStatus.CONFIRMED_SDK,
        "one current listing row per sid + msku",
        PRODUCT_IDENTITY_FIELDS,
        ("sid", "asin", "parent_asin", "msku", "lsku", "fnsku", "product_name", "fulfillment_channel", "status", "deleted", "update_time_utc"),
        PaginationKind.OFFSET,
        "current dimension snapshot; page until offset reaches total_count",
        0,
        (
            "Use this endpoint for identity and current listing state, not for daily traffic history.",
            "The stable product join key is sid + msku; ASIN-only joins are not allowed.",
        ),
    ),
    "orders": DatasetContract(
        "orders",
        "api.source.Orders",
        "/erp/sc/data/mws_report/allOrders",
        ContractStatus.CONFIRMED_SDK,
        "one order-item row per sid + amazon_order_id + msku",
        ("sid", "amazon_order_id", "msku", "asin"),
        ("sid", "amazon_order_id", "order_status", "asin", "msku", "lsku", "order_qty", "sales_amt", "currency_code", "purchase_time_utc", "purchase_date_loc", "update_time_ts"),
        PaginationKind.OFFSET,
        "initial backfill uses bounded windows; increment by update date and derive dashboard day from purchase_date_loc",
        3,
        (
            "Upsert instead of append so corrections replace older rows.",
            "Canceled/refunded treatment requires controlled reconciliation before formulas are locked.",
        ),
    ),
    "sales_traffic": DatasetContract(
        "sales_traffic",
        "api.source.ExportReportTask / ExportReportResult",
        "/basicOpen/report/create/reportExportTask",
        ContractStatus.REQUIRES_SDK_EXTENSION,
        "one day + marketplace + SKU, with controlled CHILD fallback only",
        ("seller_id", "marketplace_id", "report_date", "sku", "child_asin"),
        ("report_date", "sku", "child_asin", "sessions", "page_views", "units_ordered", "ordered_product_sales", "currency_code"),
        PaginationKind.ASYNC_TASK,
        "request site-day ranges, persist request fingerprint, poll task, then cache the downloaded result",
        3,
        (
            "Current SDK 2.1.7 omits reportOptions; never accept default parent-ASIN aggregation silently.",
            "Use bounded 7-30 day requests and do not recreate an identical completed task.",
            "Reject missing date/SKU/ASIN grain rather than fabricating zero traffic.",
        ),
        SALES_TRAFFIC_REPORT_TYPE,
        SALES_TRAFFIC_REPORT_OPTIONS,
    ),
    "ad_profiles": DatasetContract(
        "ad_profiles",
        "api.ads.AdProfiles",
        "/basicOpen/baseData/account/list",
        ContractStatus.CONFIRMED_SDK,
        "one advertising profile per sid + profile_id",
        AD_IDENTITY_FIELDS,
        ("sid", "profile_id"),
        PaginationKind.OFFSET,
        "dimension snapshot; retain profile-to-sid mapping",
        0,
    ),
    "ads_sp_product_daily": DatasetContract(
        "ads_sp_product_daily",
        "api.ads.SpProductReports",
        "/pb/openapi/newad/spProductAdReports",
        ContractStatus.CONFIRMED_SDK,
        "one report day + sid + profile_id + advertised ASIN/MSKU row",
        ("report_date", "sid", "profile_id", "campaign_id", "ad_group_id", "ad_id", "asin", "msku"),
        ("report_date", "profile_id", "asin", "msku", "impressions", "clicks", "cost", "orders", "sales"),
        PaginationKind.NEXT_TOKEN,
        "one site-local report date per request; follow next_token until empty",
        14,
        ("Attribution overlap is a configurable engineering default and requires real-account reconciliation.",),
    ),
    "ads_sb_campaign_daily": DatasetContract(
        "ads_sb_campaign_daily",
        "api.ads.SbCampaignReports",
        "/pb/openapi/newad/hsaCampaignReports",
        ContractStatus.CONFIRMED_SDK,
        "one report day + sid + profile_id + SB campaign row",
        ("report_date", "sid", "profile_id", "campaign_id"),
        ("report_date", "profile_id", "campaign_id", "impressions", "clicks", "cost", "orders", "sales"),
        PaginationKind.NEXT_TOKEN,
        "one site-local report date per request; follow next_token until empty",
        14,
        ("Campaign data supports shop totals; SKU allocation needs a separately validated creative report.",),
    ),
    "ads_sd_product_daily": DatasetContract(
        "ads_sd_product_daily",
        "api.ads.SdProductReports",
        "/pb/openapi/newad/sdProductAdReports",
        ContractStatus.CONFIRMED_SDK,
        "one report day + sid + profile_id + advertised ASIN row",
        ("report_date", "sid", "profile_id", "campaign_id", "ad_group_id", "ad_id", "asin"),
        ("report_date", "profile_id", "asin", "impressions", "clicks", "cost", "orders", "sales"),
        PaginationKind.NEXT_TOKEN,
        "one site-local report date per request; follow next_token until empty",
        14,
    ),
    "fba_inventory_snapshot": DatasetContract(
        "fba_inventory_snapshot",
        "api.warehouse.FbaInventory",
        "/erp/sc/routing/fba/fbaStock/fbaList",
        ContractStatus.CONFIRMED_SDK,
        "one snapshot day + sid + msku row",
        ("snapshot_date", "sid", "msku", "asin", "fnsku"),
        (
            "sid", "asin", "msku", "lsku", "fnsku", "afn_fulfillable_qty", "afn_unsellable_qty",
            "afn_reserved_fc_processing_qty", "afn_reserved_fc_transfers_qty", "afn_reserved_customer_order_qty",
            "afn_inbound_working_qty", "afn_inbound_shipped_qty", "afn_inbound_receiving_qty",
        ),
        PaginationKind.OFFSET,
        "write an immutable site-local snapshot date; never add snapshots from different dates",
        0,
        (
            "reserved_qty = processing + transfers + customer_order",
            "inbound_qty = working + shipped + receiving",
            "on_hand_qty = fulfillable + unsellable + reserved",
            "total_with_inbound_qty = on_hand + inbound",
            "Do not add afn_actual_shipped_qty until reconciliation proves it is non-overlapping.",
        ),
    ),
    "fba_inventory_shared_detail": DatasetContract(
        "fba_inventory_shared_detail",
        "api.warehouse.FbaInventoryDetails",
        "/basicOpen/openapi/storage/fbaWarehouseDetail",
        ContractStatus.REQUIRES_CONTROLLED_VALIDATION,
        "one current shared-stock detail row; sid can be zero",
        ("warehouse_name", "sid", "msku", "asin", "fnsku"),
        ("sid", "msku", "asin", "fnsku", "stock_share_type", "afn_fulfillable_locals_qty"),
        PaginationKind.OFFSET,
        "supplemental current snapshot only",
        0,
        (
            "When sid is zero, never assign stock to a store by name or ASIN alone.",
            "Validate marketplace allocation before using shared-stock rows in totals.",
        ),
    ),
    "monthly_sales_plan": DatasetContract(
        "monthly_sales_plan",
        "",
        "",
        ContractStatus.NOT_EXPOSED,
        "one month + sid + optional SKU target row",
        ("month", "sid", "msku"),
        ("month", "sid", "sales_target", "order_target", "ad_budget"),
        PaginationKind.NONE,
        "continue using the local monthly-plan input until an official OpenAPI endpoint is confirmed",
        0,
        (
            "lingxingapi 2.1.7 exposes no confirmed operations-plan or monthly-sales-plan read method.",
            "Do not scrape the Lingxing web UI and do not guess a private endpoint.",
        ),
    ),
}


NORMALIZED_DAILY_METRICS = (
    "sales_amount", "order_count", "units_ordered", "sessions", "page_views",
    "ad_impressions", "ad_clicks", "ad_spend", "ad_sales", "ad_orders",
    "fba_fulfillable", "fba_reserved", "fba_unsellable", "fba_inbound", "inventory_total",
)


def validate_contracts() -> tuple[str, ...]:
    errors: list[str] = []
    if set(BUSINESS_DATASETS) != {item.key for item in BUSINESS_DATASETS.values()}:
        errors.append("dataset dictionary keys must match DatasetContract.key")

    for key, item in BUSINESS_DATASETS.items():
        if item.overlap_days < 0:
            errors.append(f"{key}: overlap_days must be non-negative")
        if not item.identity_fields:
            errors.append(f"{key}: identity fields are required")
        if item.status is not ContractStatus.NOT_EXPOSED and not item.endpoint.startswith("/"):
            errors.append(f"{key}: planned endpoints must be absolute paths")
        if item.status is ContractStatus.NOT_EXPOSED and (item.endpoint or item.source_method):
            errors.append(f"{key}: not-exposed datasets must not invent an endpoint")
        if len(set(item.required_output_fields)) != len(item.required_output_fields):
            errors.append(f"{key}: duplicate required output field")

    traffic = BUSINESS_DATASETS["sales_traffic"]
    if traffic.report_type != SALES_TRAFFIC_REPORT_TYPE:
        errors.append("sales_traffic: wrong report type")
    if traffic.options() != dict(SALES_TRAFFIC_REPORT_OPTIONS):
        errors.append("sales_traffic: daily SKU report options are required")
    if traffic.status is not ContractStatus.REQUIRES_SDK_EXTENSION:
        errors.append("sales_traffic: SDK extension gate must remain explicit")

    profiles = BUSINESS_DATASETS["ad_profiles"]
    if profiles.source_method != "api.ads.AdProfiles":
        errors.append("ad_profiles: confirmed SDK method is api.ads.AdProfiles")

    inventory = BUSINESS_DATASETS["fba_inventory_snapshot"]
    if "never add snapshots from different dates" not in inventory.date_rule:
        errors.append("fba_inventory_snapshot: cross-date protection is missing")

    return tuple(errors)


def assert_contract_integrity() -> None:
    errors = validate_contracts()
    if errors:
        raise ValueError("; ".join(errors))
