# -*- coding: utf-8 -*-
"""Public, credential-free contract for future Lingxing business-data sync.

This module intentionally contains no network client and no customer data.  It
records the endpoints, normalized grains, identity rules, pagination rules and
validation gates that must be satisfied before live account calls are added.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ContractStatus(str, Enum):
    """How ready a dataset is for implementation."""

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


# Stable identity rules.  Do not silently fall back to a display name.
SHOP_IDENTITY_FIELDS = (
    "sid",
    "seller_id",
    "marketplace_id",
    "region",
)
PRODUCT_IDENTITY_FIELDS = (
    "sid",
    "msku",
    "asin",
    "fnsku",
    "lsku",
)
AD_IDENTITY_FIELDS = (
    "sid",
    "profile_id",
)


# The report type is supported by Amazon Reports API and can be requested
# through Lingxing's asynchronous report-export route.  lingxingapi 2.1.7 does
# not expose reportOptions, therefore the adapter must be extended before this
# dataset can be trusted at daily SKU grain.
SALES_TRAFFIC_REPORT_TYPE = "GET_SALES_AND_TRAFFIC_REPORT"
SALES_TRAFFIC_REPORT_OPTIONS = (
    ("dateGranularity", "DAY"),
    ("asinGranularity", "SKU"),
)


BUSINESS_DATASETS: dict[str, DatasetContract] = {
    "shops": DatasetContract(
        key="shops",
        source_method="api.basic.Sellers",
        endpoint="/erp/sc/data/seller/lists",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one row per Lingxing seller/site",
        identity_fields=SHOP_IDENTITY_FIELDS,
        required_output_fields=(
            "mid",
            "sid",
            "seller_id",
            "seller_name",
            "marketplace_id",
            "region",
            "country",
            "ads_authorized",
        ),
        pagination=PaginationKind.NONE,
        date_rule="dimension snapshot; retain the last successful snapshot",
        overlap_days=0,
    ),
    "listings": DatasetContract(
        key="listings",
        source_method="api.sales.Listings",
        endpoint="/erp/sc/data/mws/listing",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one current listing row per sid + msku",
        identity_fields=PRODUCT_IDENTITY_FIELDS,
        required_output_fields=(
            "sid",
            "asin",
            "parent_asin",
            "msku",
            "lsku",
            "fnsku",
            "product_name",
            "fulfillment_channel",
            "status",
            "deleted",
            "update_time_utc",
        ),
        pagination=PaginationKind.OFFSET,
        date_rule="current dimension snapshot; page until offset reaches total_count",
        overlap_days=0,
        notes=(
            "Use this endpoint for identity and current listing state, not for daily traffic history.",
            "The stable product join key is sid + msku; ASIN-only joins are not allowed.",
        ),
    ),
    "orders": DatasetContract(
        key="orders",
        source_method="api.source.Orders",
        endpoint="/erp/sc/data/mws_report/allOrders",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one order-item row per sid + amazon_order_id + msku",
        identity_fields=("sid", "amazon_order_id", "msku", "asin"),
        required_output_fields=(
            "sid",
            "amazon_order_id",
            "order_status",
            "asin",
            "msku",
            "lsku",
            "order_qty",
            "sales_amt",
            "currency_code",
            "purchase_time_utc",
            "purchase_date_loc",
            "update_time_ts",
        ),
        pagination=PaginationKind.OFFSET,
        date_rule=(
            "initial backfill uses bounded date windows; incremental sync uses Amazon update date "
            "with an overlap and derives dashboard day from purchase_date_loc"
        ),
        overlap_days=3,
        notes=(
            "Upsert instead of append so status, quantity and amount corrections replace older rows.",
            "Canceled/refunded treatment must be reconciled against the Lingxing UI before final metric formulas are locked.",
        ),
    ),
    "sales_traffic": DatasetContract(
        key="sales_traffic",
        source_method="api.source.ExportReportTask / ExportReportResult",
        endpoint="/basicOpen/report/create/reportExportTask",
        status=ContractStatus.REQUIRES_SDK_EXTENSION,
        grain="one day + marketplace + SKU (or controlled CHILD fallback) row",
        identity_fields=("seller_id", "marketplace_id", "report_date", "sku", "child_asin"),
        required_output_fields=(
            "report_date",
            "sku",
            "child_asin",
            "sessions",
            "page_views",
            "units_ordered",
            "ordered_product_sales",
            "currency_code",
        ),
        pagination=PaginationKind.ASYNC_TASK,
        date_rule="request site-day ranges, persist request fingerprint, poll task, then cache the downloaded result",
        overlap_days=3,
        notes=(
            "Current SDK 2.1.7 omits reportOptions; do not accept default parent-ASIN aggregation silently.",
            "Use 7-30 day request windows and rate-limit report creation; never recreate an identical completed task unnecessarily.",
            "The downloaded report parser must reject missing date/SKU/ASIN grain instead of fabricating zero traffic.",
        ),
        report_type=SALES_TRAFFIC_REPORT_TYPE,
        report_options=SALES_TRAFFIC_REPORT_OPTIONS,
    ),
    "ad_profiles": DatasetContract(
        key="ad_profiles",
        source_method="api.ads.Profiles",
        endpoint="/basicOpen/baseData/account/list",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one advertising profile per sid + profile_id",
        identity_fields=AD_IDENTITY_FIELDS,
        required_output_fields=("sid", "profile_id"),
        pagination=PaginationKind.OFFSET,
        date_rule="dimension snapshot; retain profile-to-sid mapping",
        overlap_days=0,
    ),
    "ads_sp_product_daily": DatasetContract(
        key="ads_sp_product_daily",
        source_method="api.ads.SpProductReports",
        endpoint="/pb/openapi/newad/spProductAdReports",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one report day + sid + profile_id + advertised ASIN/MSKU row",
        identity_fields=("report_date", "sid", "profile_id", "campaign_id", "ad_group_id", "ad_id", "asin", "msku"),
        required_output_fields=(
            "report_date",
            "profile_id",
            "asin",
            "msku",
            "impressions",
            "clicks",
            "cost",
            "orders",
            "sales",
        ),
        pagination=PaginationKind.NEXT_TOKEN,
        date_rule="one site-local report date per request; follow next_token until empty",
        overlap_days=14,
        notes=("Attribution can revise recent dates; the overlap is a proposed default and remains configurable.",),
    ),
    "ads_sb_campaign_daily": DatasetContract(
        key="ads_sb_campaign_daily",
        source_method="api.ads.SbCampaignReports",
        endpoint="/pb/openapi/newad/hsaCampaignReports",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one report day + sid + profile_id + SB campaign row",
        identity_fields=("report_date", "sid", "profile_id", "campaign_id"),
        required_output_fields=("report_date", "profile_id", "campaign_id", "impressions", "clicks", "cost", "orders", "sales"),
        pagination=PaginationKind.NEXT_TOKEN,
        date_rule="one site-local report date per request; follow next_token until empty",
        overlap_days=14,
        notes=("Campaign-level data is sufficient for shop totals; product allocation requires a separately validated SB creative report.",),
    ),
    "ads_sd_product_daily": DatasetContract(
        key="ads_sd_product_daily",
        source_method="api.ads.SdProductReports",
        endpoint="/pb/openapi/newad/sdProductAdReports",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one report day + sid + profile_id + advertised ASIN row",
        identity_fields=("report_date", "sid", "profile_id", "campaign_id", "ad_group_id", "ad_id", "asin"),
        required_output_fields=("report_date", "profile_id", "asin", "impressions", "clicks", "cost", "orders", "sales"),
        pagination=PaginationKind.NEXT_TOKEN,
        date_rule="one site-local report date per request; follow next_token until empty",
        overlap_days=14,
    ),
    "fba_inventory_snapshot": DatasetContract(
        key="fba_inventory_snapshot",
        source_method="api.warehouse.FbaInventory",
        endpoint="/erp/sc/routing/fba/fbaStock/fbaList",
        status=ContractStatus.CONFIRMED_SDK,
        grain="one snapshot day + sid + msku row",
        identity_fields=("snapshot_date", "sid", "msku", "asin", "fnsku"),
        required_output_fields=(
            "sid",
            "asin",
            "msku",
            "lsku",
            "fnsku",
            "afn_fulfillable_qty",
            "afn_unsellable_qty",
            "afn_reserved_fc_processing_qty",
            "afn_reserved_fc_transfers_qty",
            "afn_reserved_customer_order_qty",
            "afn_inbound_working_qty",
            "afn_inbound_shipped_qty",
            "afn_inbound_receiving_qty",
        ),
        pagination=PaginationKind.OFFSET,
        date_rule="write an immutable site-local snapshot date; never add snapshots from different dates",
        overlap_days=0,
        notes=(
            "reserved_qty = processing + transfers + customer_order",
            "inbound_qty = working + shipped + receiving",
            "on_hand_qty = fulfillable + unsellable + reserved",
            "total_with_inbound_qty = on_hand + inbound",
            "Do not add afn_actual_shipped_qty to inbound until controlled reconciliation proves it is non-overlapping.",
        ),
    ),
    "fba_inventory_shared_detail": DatasetContract(
        key="fba_inventory_shared_detail",
        source_method="api.warehouse.FbaInventoryDetails",
        endpoint="/basicOpen/openapi/storage/fbaWarehouseDetail",
        status=ContractStatus.REQUIRES_CONTROLLED_VALIDATION,
        grain="one current shared-stock detail row; sid can be zero",
        identity_fields=("warehouse_name", "sid", "msku", "asin", "fnsku"),
        required_output_fields=("sid", "msku", "asin", "fnsku", "stock_share_type", "afn_fulfillable_locals_qty"),
        pagination=PaginationKind.OFFSET,
        date_rule="supplemental current snapshot only",
        overlap_days=0,
        notes=(
            "When sid is zero, never assign stock to a store by name or ASIN alone.",
            "Validate marketplace allocation against afn_fulfillable_locals_qty before using shared-stock rows in totals.",
        ),
    ),
    "monthly_sales_plan": DatasetContract(
        key="monthly_sales_plan",
        source_method="",
        endpoint="",
        status=ContractStatus.NOT_EXPOSED,
        grain="one month + sid + optional SKU target row",
        identity_fields=("month", "sid", "msku"),
        required_output_fields=("month", "sid", "sales_target", "order_target", "ad_budget"),
        pagination=PaginationKind.NONE,
        date_rule="continue using the local monthly-plan input until an official OpenAPI endpoint is confirmed",
        overlap_days=0,
        notes=(
            "lingxingapi 2.1.7 exposes no confirmed operations-plan or monthly-sales-plan read method.",
            "Do not scrape the Lingxing web UI and do not guess a private endpoint.",
        ),
    ),
}


NORMALIZED_DAILY_METRICS = (
    "sales_amount",
    "order_count",
    "units_ordered",
    "sessions",
    "page_views",
    "ad_impressions",
    "ad_clicks",
    "ad_spend",
    "ad_sales",
    "ad_orders",
    "fba_fulfillable",
    "fba_reserved",
    "fba_unsellable",
    "fba_inbound",
    "inventory_total",
)


def validate_contracts() -> tuple[str, ...]:
    """Return deterministic contract errors without making network calls."""

    errors: list[str] = []
    if set(BUSINESS_DATASETS) != {item.key for item in BUSINESS_DATASETS.values()}:
        errors.append("dataset dictionary keys must match DatasetContract.key")

    for key, item in BUSINESS_DATASETS.items():
        if item.overlap_days < 0:
            errors.append(f"{key}: overlap_days must be non-negative")
        if not item.identity_fields:
            errors.append(f"{key}: identity fields are required")
        if item.status is not ContractStatus.NOT_EXPOSED and not item.endpoint.startswith("/"):
            errors.append(f"{key}: confirmed or planned endpoints must be absolute paths")
        if item.status is ContractStatus.NOT_EXPOSED and (item.endpoint or item.source_method):
            errors.append(f"{key}: not-exposed datasets must not invent an endpoint or SDK method")
        if len(set(item.required_output_fields)) != len(item.required_output_fields):
            errors.append(f"{key}: duplicate required output field")

    traffic = BUSINESS_DATASETS["sales_traffic"]
    if traffic.report_type != SALES_TRAFFIC_REPORT_TYPE:
        errors.append("sales_traffic: wrong report type")
    if traffic.options() != dict(SALES_TRAFFIC_REPORT_OPTIONS):
        errors.append("sales_traffic: daily SKU report options are required")
    if traffic.status is not ContractStatus.REQUIRES_SDK_EXTENSION:
        errors.append("sales_traffic: SDK extension gate must remain explicit")

    inventory = BUSINESS_DATASETS["fba_inventory_snapshot"]
    if "never add snapshots from different dates" not in inventory.date_rule:
        errors.append("fba_inventory_snapshot: cross-date protection is missing")

    return tuple(errors)


def assert_contract_integrity() -> None:
    errors = validate_contracts()
    if errors:
        raise ValueError("; ".join(errors))
