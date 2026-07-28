# -*- coding: utf-8 -*-
"""Controlled follow-up for two unresolved Lingxing read-only probe calls.

This provider makes exactly one request shape per dataset. It never retries an
alternate shape in the same run and still reduces every response or exception
to the existing privacy-safe diagnostic schema.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from agent.lingxing_probe import (
    _as_mapping,
    _response_rows,
    skipped_summary,
    summarize_response,
)
from agent.lingxing_probe_diagnostics import (
    DiagnosticTlsSdkLingxingProbeProvider,
    _exception_chain,
    _numeric_remote_code,
    classify_diagnostic_probe_error,
    validate_diagnostic_summary,
)
from agent.lingxing_sync_foundation import (
    SalesTrafficReportRequest,
    request_sales_traffic_report,
)
from agent.lingxing_tls_proxy import secure_proxy_endpoint

SALES_TRAFFIC_OPTIONS_KEY = "reportOptions"
SHARED_INVENTORY_FILTERS = {
    "fulfillment_channel": "FBA",
    "exclude_zero_stock": 1,
    "exclude_deleted": 1,
    "offset": 0,
    "length": 1,
}


def classify_controlled_probe_error(dataset: str, exc: BaseException) -> dict[str, Any]:
    """Separate signing failures from API authorization failures."""

    chain = _exception_chain(exc)
    names = {type(item).__name__ for item in chain}
    if names & {"SignatureError", "SignatureExpiredError", "InvalidSignatureError"}:
        return validate_diagnostic_summary(
            {
                "dataset": dataset,
                "status": "failed",
                "fields": [],
                "sampled_rows": 0,
                "response_count": 0,
                "total_count": None,
                "date_from": None,
                "date_to": None,
                "error_code": "request_rejected",
                "diagnostic_code": "sdk_contract_mismatch",
                "remote_error_code": _numeric_remote_code(chain),
            }
        )
    return classify_diagnostic_probe_error(dataset, exc)


class ControlledValidationProbeProvider(DiagnosticTlsSdkLingxingProbeProvider):
    """Probe the second documented request shape without expanding data scope."""

    async def _capture(
        self,
        dataset: str,
        operation: Callable[[], Any],
        *,
        date_fields: Sequence[str] = (),
        requested_from: str | None = None,
        requested_to: str | None = None,
        success_status: str = "success",
    ) -> tuple[dict[str, Any], Any | None]:
        try:
            response = await operation()
            return (
                summarize_response(
                    dataset,
                    response,
                    date_fields=date_fields,
                    requested_from=requested_from,
                    requested_to=requested_to,
                    success_status=success_status,
                ),
                response,
            )
        except BaseException as exc:  # noqa: BLE001 - every probe remains isolated
            return classify_controlled_probe_error(dataset, exc), None

    async def _run_probe(
        self,
        credentials: Any,
        cached_shops: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        identity = self._shop_identity(cached_shops)
        results: list[dict[str, Any]] = []
        probe_datasets = (
            "listings",
            "orders",
            "ad_profiles",
            "ads_sp_product_daily",
            "ads_sb_campaign_daily",
            "ads_sd_product_daily",
            "fba_inventory_snapshot",
            "fba_inventory_shared_detail",
            "sales_traffic",
        )
        if identity is None:
            for dataset in probe_datasets:
                results.append(skipped_summary(dataset, "missing_shop_identity"))
            return {"status": "success", "datasets": results}

        try:
            from aiohttp_socks import ProxyConnector
            from lingxingapi import API
        except ImportError as exc:  # pragma: no cover - frozen runtime gate covers this
            raise RuntimeError("Lingxing SDK runtime unavailable") from exc

        today = date.today()
        report_date = (today - timedelta(days=2)).isoformat()
        order_from = (today - timedelta(days=7)).isoformat()
        order_to = (today - timedelta(days=1)).isoformat()
        traffic_day = today - timedelta(days=2)
        traffic_start = datetime.combine(traffic_day, time.min, tzinfo=timezone.utc).isoformat()
        traffic_end = datetime.combine(traffic_day, time.max, tzinfo=timezone.utc).isoformat()

        with secure_proxy_endpoint(credentials.proxy_url) as local_proxy_url:
            connector = ProxyConnector.from_url(local_proxy_url)
            async with API(
                credentials.app_id,
                credentials.app_secret,
                timeout=30,
                ignore_timeout=True,
                ignore_timeout_wait=2,
                ignore_timeout_retry=1,
                ignore_api_limit=True,
                ignore_api_limit_wait=2,
                ignore_api_limit_retry=1,
                proxy_connector=connector,
            ) as api:
                summary, _ = await self._capture(
                    "listings",
                    lambda: api.sales.Listings(identity["sid"], offset=0, length=1),
                    date_fields=("update_time_utc", "on_sale_date", "create_time"),
                )
                results.append(summary)

                summary, _ = await self._capture(
                    "orders",
                    lambda: api.source.Orders(
                        identity["sid"], order_from, order_to, date_type=2, offset=0, length=1
                    ),
                    date_fields=("purchase_date_loc", "purchase_time_utc", "purchase_time_loc"),
                    requested_from=order_from,
                    requested_to=order_to,
                )
                results.append(summary)

                profile_summary, profile_response = await self._capture(
                    "ad_profiles",
                    lambda: api.ads.AdProfiles("seller", offset=0, length=100),
                )
                results.append(profile_summary)
                profile_id = self._matching_profile(profile_response, identity["sid"]) if profile_response else None

                ad_calls = (
                    ("ads_sp_product_daily", "SpProductReports"),
                    ("ads_sb_campaign_daily", "SbCampaignReports"),
                    ("ads_sd_product_daily", "SdProductReports"),
                )
                for dataset, method_name in ad_calls:
                    if profile_id is None:
                        results.append(skipped_summary(dataset, "missing_ad_profile"))
                        continue
                    method = getattr(api.ads, method_name, None)
                    if not callable(method):
                        results.append(skipped_summary(dataset, "unsupported"))
                        continue
                    summary, _ = await self._capture(
                        dataset,
                        lambda method=method: method(
                            report_date,
                            identity["sid"],
                            profile_id,
                            offset=0,
                            length=1,
                        ),
                        date_fields=("report_date",),
                        requested_from=report_date,
                        requested_to=report_date,
                    )
                    results.append(summary)

                summary, _ = await self._capture(
                    "fba_inventory_snapshot",
                    lambda: api.warehouse.FbaInventory(identity["sid"], offset=0, length=1),
                    requested_from=today.isoformat(),
                    requested_to=today.isoformat(),
                )
                results.append(summary)

                summary, _ = await self._capture(
                    "fba_inventory_shared_detail",
                    lambda: api.warehouse.FbaInventoryDetails(**SHARED_INVENTORY_FILTERS),
                    requested_from=today.isoformat(),
                    requested_to=today.isoformat(),
                )
                results.append(summary)

                request = SalesTrafficReportRequest(
                    seller_id=identity["seller_id"],
                    marketplace_ids=(identity["marketplace_id"],),
                    region=identity["region"],
                    start_time=traffic_start,
                    end_time=traffic_end,
                )
                summary, _ = await self._capture(
                    "sales_traffic",
                    lambda: request_sales_traffic_report(
                        api.source,
                        request,
                        options_key=SALES_TRAFFIC_OPTIONS_KEY,
                    ),
                    requested_from=traffic_day.isoformat(),
                    requested_to=traffic_day.isoformat(),
                    success_status="task_created",
                )
                results.append(summary)

        return {"status": "success", "datasets": results}
