from agent.lingxing_business_contract import (
    BUSINESS_DATASETS,
    NORMALIZED_DAILY_METRICS,
    SALES_TRAFFIC_REPORT_OPTIONS,
    SALES_TRAFFIC_REPORT_TYPE,
    ContractStatus,
    PaginationKind,
    assert_contract_integrity,
    validate_contracts,
)


def test_business_contract_is_internally_consistent():
    assert validate_contracts() == ()
    assert_contract_integrity()


def test_current_implemented_provider_scope_is_not_overstated():
    assert BUSINESS_DATASETS["shops"].status is ContractStatus.CONFIRMED_SDK
    assert BUSINESS_DATASETS["orders"].status is ContractStatus.CONFIRMED_SDK
    assert BUSINESS_DATASETS["ads_sp_product_daily"].status is ContractStatus.CONFIRMED_SDK
    assert BUSINESS_DATASETS["fba_inventory_snapshot"].status is ContractStatus.CONFIRMED_SDK

    traffic = BUSINESS_DATASETS["sales_traffic"]
    assert traffic.status is ContractStatus.REQUIRES_SDK_EXTENSION
    assert traffic.report_type == SALES_TRAFFIC_REPORT_TYPE
    assert traffic.options() == dict(SALES_TRAFFIC_REPORT_OPTIONS)
    assert traffic.pagination is PaginationKind.ASYNC_TASK


def test_monthly_plan_does_not_guess_a_private_endpoint():
    plan = BUSINESS_DATASETS["monthly_sales_plan"]
    assert plan.status is ContractStatus.NOT_EXPOSED
    assert plan.endpoint == ""
    assert plan.source_method == ""
    assert "local monthly-plan" in plan.date_rule
    assert any("Do not scrape" in note for note in plan.notes)


def test_product_and_ad_contracts_keep_store_identity():
    for key in (
        "listings",
        "orders",
        "ads_sp_product_daily",
        "ads_sb_campaign_daily",
        "ads_sd_product_daily",
        "fba_inventory_snapshot",
    ):
        assert "sid" in BUSINESS_DATASETS[key].identity_fields

    assert BUSINESS_DATASETS["listings"].identity_fields[:2] == ("sid", "msku")
    assert "profile_id" in BUSINESS_DATASETS["ads_sp_product_daily"].identity_fields


def test_inventory_contract_prevents_cross_date_and_double_count_regressions():
    inventory = BUSINESS_DATASETS["fba_inventory_snapshot"]
    assert "snapshot_date" in inventory.identity_fields
    assert "never add snapshots from different dates" in inventory.date_rule
    assert any(note.startswith("reserved_qty =") for note in inventory.notes)
    assert any(note.startswith("inbound_qty =") for note in inventory.notes)
    assert any("afn_actual_shipped_qty" in note for note in inventory.notes)

    shared = BUSINESS_DATASETS["fba_inventory_shared_detail"]
    assert shared.status is ContractStatus.REQUIRES_CONTROLLED_VALIDATION
    assert any("sid is zero" in note for note in shared.notes)


def test_dashboard_metric_contract_covers_target_business_data():
    required = {
        "sales_amount",
        "order_count",
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
    }
    assert required.issubset(set(NORMALIZED_DAILY_METRICS))


def test_incremental_contracts_use_bounded_reconciliation_windows():
    assert BUSINESS_DATASETS["orders"].overlap_days == 3
    assert BUSINESS_DATASETS["sales_traffic"].overlap_days == 3
    assert BUSINESS_DATASETS["ads_sp_product_daily"].overlap_days == 14
    assert BUSINESS_DATASETS["ads_sb_campaign_daily"].overlap_days == 14
    assert BUSINESS_DATASETS["ads_sd_product_daily"].overlap_days == 14
