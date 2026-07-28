# -*- coding: utf-8 -*-
"""Local-only synchronization for Lingxing datasets already proven available.

The synchronizer intentionally excludes the two unresolved sources:
- sales_traffic (Lingxing SDK signing contract mismatch)
- fba_inventory_shared_detail (Lingxing upstream HTTP 500)

All successful generations are written through LingxingDatasetStore. A failed
dataset keeps its previous current generation readable. Source rows are projected
to an explicit allowlist before they reach local storage, so order-address fields
and other unrelated response values are never persisted.
"""
from __future__ import annotations

import asyncio
import enum
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from agent.lingxing_business_contract import BUSINESS_DATASETS
from agent.lingxing_probe import _as_mapping
from agent.lingxing_secure_store import LingxingCredentials, LingxingLocalStore
from agent.lingxing_service import LingxingProvider
from agent.lingxing_sync_foundation import (
    LingxingDatasetStore,
    collect_next_token_pages,
    collect_offset_pages,
    utc_now,
)
from agent.lingxing_tls_proxy import (
    TlsSdkLingxingProvider,
    TlsLingxingSyncService,
    secure_proxy_endpoint,
)

AVAILABLE_DATASETS = (
    "shops",
    "listings",
    "orders",
    "ad_profiles",
    "ads_sp_product_daily",
    "ads_sb_campaign_daily",
    "ads_sd_product_daily",
    "fba_inventory_snapshot",
)
UNAVAILABLE_DATASETS = {
    "sales_traffic": "sdk_contract_mismatch",
    "fba_inventory_shared_detail": "lingxing_server_error",
}
_AD_DATASETS = {
    "ads_sp_product_daily": "SpProductReports",
    "ads_sb_campaign_daily": "SbCampaignReports",
    "ads_sd_product_daily": "SdProductReports",
}
_APPROVED_OPTIONAL_FIELDS: dict[str, frozenset[str]] = {
    "listings": frozenset(
        {
            "brand",
            "category",
            "category_rank",
            "currency_code",
            "landed_price",
            "list_price",
            "sale_price",
            "your_price",
            "b2b_price",
            "on_sale_date",
            "create_time",
            "review_count",
            "review_stars",
            "sales_qty_1d",
            "sales_qty_7d",
            "sales_qty_14d",
            "sales_qty_30d",
            "sales_amt_1d",
            "sales_amt_7d",
            "sales_amt_14d",
            "sales_amt_30d",
            "thumbnail_url",
        }
    ),
    "orders": frozenset(
        {
            "fulfillment_channel",
            "sales_channel",
            "order_item_status",
            "purchase_time_loc",
            "shipment_time_loc",
        }
    ),
    "ads_sp_product_daily": frozenset(
        {"direct_orders", "direct_sales", "direct_units", "units"}
    ),
    "ads_sb_campaign_daily": frozenset(
        {
            "direct_orders",
            "direct_sales",
            "direct_units",
            "units",
            "new_to_brand_orders",
            "new_to_brand_sales",
        }
    ),
    "ads_sd_product_daily": frozenset(
        {"direct_orders", "direct_sales", "direct_units", "units"}
    ),
    "fba_inventory_snapshot": frozenset(
        {
            "product_name",
            "brand_id",
            "brand_name",
            "category_id",
            "category_name",
            "warehouse_name",
            "afn_fulfillable_total_qty",
            "afn_actual_shipped_qty",
            "afn_researching_qty",
            "age_0_to_30_days_qty",
            "age_31_to_60_days_qty",
            "age_61_to_90_days_qty",
            "age_0_to_90_days_qty",
            "age_91_to_180_days_qty",
            "age_181_to_270_days_qty",
            "age_271_to_330_days_qty",
            "age_271_to_365_days_qty",
            "age_331_to_365_days_qty",
            "age_365_plus_days_qty",
            "estimated_30d_storage_fee",
            "estimated_excess_qty",
            "inventory_cost_amt",
            "inventory_value_amt",
            "inventory_health_status",
            "inventory_low_level_fee_status",
            "sell_through_rate",
            "historical_days_of_supply",
            "historical_st_days_of_supply",
            "historical_lt_days_of_supply",
            "estimated_days_of_supply",
            "recommended_minimum_qty",
            "recommended_action",
            "currency_code",
        }
    ),
}


class AvailableDatasetProvider(Protocol):
    def sync(
        self,
        credentials: LingxingCredentials,
        cached_shops: Sequence[Mapping[str, Any]],
        store: LingxingDatasetStore,
    ) -> dict[str, Any]: ...


class AvailableSyncError(RuntimeError):
    pass


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return _json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
    if hasattr(value, "dict"):
        return _json_safe(value.dict())
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return str(value)


def _row_mapping(value: Any) -> dict[str, Any]:
    mapped = _as_mapping(value)
    return dict(_json_safe(mapped)) if mapped else {}


def _require_identity(dataset: str, row: Mapping[str, Any]) -> None:
    missing = [
        field
        for field in BUSINESS_DATASETS[dataset].identity_fields
        if row.get(field) in (None, "")
    ]
    if missing:
        raise AvailableSyncError(
            f"{dataset}: source row is missing required identity fields"
        )


def _normalized_row(
    dataset: str,
    raw: Any,
    *,
    additions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source = _row_mapping(raw)
    if additions:
        source.update(
            {str(key): _json_safe(value) for key, value in additions.items()}
        )
    contract = BUSINESS_DATASETS[dataset]
    allowed = (
        set(contract.identity_fields)
        | set(contract.required_output_fields)
        | set(_APPROVED_OPTIONAL_FIELDS.get(dataset, ()))
    )
    row = {field: source.get(field) for field in sorted(allowed)}
    _require_identity(dataset, row)
    return row


def _valid_shops(cached_shops: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    shops: list[dict[str, Any]] = []
    for raw in cached_shops:
        item = dict(raw)
        try:
            sid = int(item.get("sid") or 0)
        except (TypeError, ValueError):
            continue
        if sid <= 0:
            continue
        item["sid"] = sid
        shops.append(item)
    return shops


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _bounded_window(
    store: LingxingDatasetStore,
    dataset: str,
    *,
    initial_days: int,
    overlap_days: int,
    max_days: int,
    end_day: date,
) -> tuple[date, date]:
    current = store.load(dataset)
    previous_to = _parse_day(current.checkpoint.get("date_to")) if current else None
    if previous_to is None:
        start = end_day - timedelta(days=max(0, initial_days - 1))
    else:
        start = previous_to - timedelta(days=overlap_days)
    floor = end_day - timedelta(days=max(0, max_days - 1))
    return max(start, floor), end_day


def _safe_error_code(exc: BaseException) -> str:
    names: set[str] = set()
    current: BaseException | None = exc
    for _ in range(6):
        if current is None or type(current).__name__ in names:
            break
        names.add(type(current).__name__)
        current = current.__cause__ or current.__context__
    joined = " ".join(sorted(names)).lower()
    if "timeout" in joined:
        return "timeout"
    if "limit" in joined or "toomanyrequests" in joined:
        return "rate_limited"
    if "authorization" in joined or "unauthorized" in joined or "token" in joined:
        return "unauthorized"
    if "validation" in joined or "response" in joined:
        return "schema_mismatch"
    if "connection" in joined or "payload" in joined:
        return "transport_error"
    return "sync_failed"


class TlsSdkAvailableDatasetProvider:
    """Sequential, bounded synchronization over one pinned-TLS SDK session."""

    def sync(
        self,
        credentials: LingxingCredentials,
        cached_shops: Sequence[Mapping[str, Any]],
        store: LingxingDatasetStore,
    ) -> dict[str, Any]:
        return asyncio.run(self._sync(credentials, cached_shops, store))

    async def _sync(
        self,
        credentials: LingxingCredentials,
        cached_shops: Sequence[Mapping[str, Any]],
        store: LingxingDatasetStore,
    ) -> dict[str, Any]:
        shops = _valid_shops(cached_shops)
        if not shops:
            raise AvailableSyncError("no valid cached shops are available")

        try:
            from aiohttp_socks import ProxyConnector
            from lingxingapi import API
        except ImportError as exc:  # pragma: no cover - frozen gate covers this
            raise RuntimeError("Lingxing SDK runtime unavailable") from exc

        results: dict[str, dict[str, Any]] = {}
        await self._capture(
            store,
            results,
            "shops",
            lambda: self._shops_payload(shops),
        )

        with secure_proxy_endpoint(credentials.proxy_url) as local_proxy_url:
            connector = ProxyConnector.from_url(local_proxy_url)
            async with API(
                credentials.app_id,
                credentials.app_secret,
                timeout=45,
                ignore_timeout=True,
                ignore_timeout_wait=3,
                ignore_timeout_retry=2,
                ignore_api_limit=True,
                ignore_api_limit_wait=3,
                ignore_api_limit_retry=3,
                proxy_connector=connector,
            ) as api:
                await self._capture(
                    store,
                    results,
                    "listings",
                    lambda: self._listings_payload(api, shops),
                )
                await self._capture(
                    store,
                    results,
                    "orders",
                    lambda: self._orders_payload(api, shops, store),
                )
                profiles: list[dict[str, Any]] = []
                await self._capture(
                    store,
                    results,
                    "ad_profiles",
                    lambda: self._profiles_payload(api, shops, profiles),
                )
                if results.get("ad_profiles", {}).get("status") == "success":
                    for dataset, method_name in _AD_DATASETS.items():
                        await self._capture(
                            store,
                            results,
                            dataset,
                            lambda dataset=dataset, method_name=method_name: self._ads_payload(
                                api,
                                shops,
                                profiles,
                                store,
                                dataset,
                                method_name,
                            ),
                        )
                else:
                    for dataset in _AD_DATASETS:
                        self._dependency_failure(store, results, dataset)
                await self._capture(
                    store,
                    results,
                    "fba_inventory_snapshot",
                    lambda: self._inventory_payload(api, shops),
                )

        return {
            "status": "success",
            "datasets": results,
            "available_count": sum(
                1 for item in results.values() if item.get("status") == "success"
            ),
            "failed_count": sum(
                1 for item in results.values() if item.get("status") == "failed"
            ),
            "unavailable": dict(UNAVAILABLE_DATASETS),
        }

    @staticmethod
    def _dependency_failure(
        store: LingxingDatasetStore,
        results: dict[str, dict[str, Any]],
        dataset: str,
    ) -> None:
        status = store.record_failure(
            dataset,
            "同步依赖失败，已保留上次成功数据。",
            error_code="dependency_failed",
        )
        current = store.load(dataset)
        results[dataset] = {
            "dataset": dataset,
            "status": "failed",
            "row_count": len(current.rows) if current else 0,
            "last_success_at": status.get("last_success_at"),
            "error_code": "dependency_failed",
        }

    async def _capture(
        self,
        store: LingxingDatasetStore,
        results: dict[str, dict[str, Any]],
        dataset: str,
        operation: Callable[
            [], Awaitable[tuple[list[dict[str, Any]], str, dict[str, Any]]]
        ],
    ) -> None:
        store.mark_syncing(dataset)
        try:
            rows, mode, checkpoint = await operation()
            snapshot = store.commit(
                dataset,
                rows,
                mode=mode,
                checkpoint=checkpoint,
            )
            results[dataset] = {
                "dataset": dataset,
                "status": "success",
                "row_count": len(snapshot.rows),
                "last_success_at": snapshot.checkpoint.get("last_success_at"),
                "date_from": snapshot.checkpoint.get("date_from"),
                "date_to": snapshot.checkpoint.get("date_to"),
            }
        except BaseException as exc:  # noqa: BLE001 - isolate every dataset
            code = _safe_error_code(exc)
            status = store.record_failure(
                dataset,
                "同步失败，已保留上次成功数据。",
                error_code=code,
            )
            current = store.load(dataset)
            results[dataset] = {
                "dataset": dataset,
                "status": "failed",
                "row_count": len(current.rows) if current else 0,
                "last_success_at": status.get("last_success_at"),
                "error_code": code,
            }

    async def _shops_payload(
        self, shops: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        rows = [_normalized_row("shops", shop) for shop in shops]
        return rows, "replace", {"shops_count": len(rows)}

    async def _listings_payload(
        self, api: Any, shops: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for shop in shops:
            sid = int(shop["sid"])

            async def fetch_page(offset: int, length: int, sid: int = sid):
                return await api.sales.Listings(sid, offset=offset, length=length)

            for raw in await collect_offset_pages(fetch_page, page_size=200):
                rows.append(_normalized_row("listings", raw, additions={"sid": sid}))
        return rows, "replace", {
            "snapshot_date": date.today().isoformat(),
            "shops_count": len(shops),
        }

    async def _orders_payload(
        self,
        api: Any,
        shops: Sequence[Mapping[str, Any]],
        store: LingxingDatasetStore,
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        end_day = date.today() - timedelta(days=1)
        start_day, end_day = _bounded_window(
            store,
            "orders",
            initial_days=30,
            overlap_days=3,
            max_days=30,
            end_day=end_day,
        )
        rows: list[dict[str, Any]] = []
        for shop in shops:
            sid = int(shop["sid"])

            async def fetch_page(offset: int, length: int, sid: int = sid):
                return await api.source.Orders(
                    sid,
                    start_day.isoformat(),
                    end_day.isoformat(),
                    date_type=2,
                    offset=offset,
                    length=length,
                )

            for raw in await collect_offset_pages(fetch_page, page_size=500):
                row = _normalized_row("orders", raw, additions={"sid": sid})
                if not row.get("purchase_date_loc"):
                    row["purchase_date_loc"] = str(
                        row.get("purchase_time_loc")
                        or row.get("purchase_time_utc")
                        or ""
                    )[:10]
                rows.append(row)
        return rows, "upsert", {
            "date_from": start_day.isoformat(),
            "date_to": end_day.isoformat(),
            "overlap_days": 3,
            "shops_count": len(shops),
        }

    async def _profiles_payload(
        self,
        api: Any,
        shops: Sequence[Mapping[str, Any]],
        output: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        allowed_sids = {int(shop["sid"]) for shop in shops}

        async def fetch_page(offset: int, length: int):
            return await api.ads.AdProfiles("seller", offset=offset, length=length)

        rows: list[dict[str, Any]] = []
        for raw in await collect_offset_pages(fetch_page, page_size=100):
            row = _normalized_row("ad_profiles", raw)
            if int(row.get("sid") or 0) not in allowed_sids:
                continue
            rows.append(row)
        output.extend(rows)
        return rows, "replace", {"shops_count": len(shops)}

    async def _ads_payload(
        self,
        api: Any,
        shops: Sequence[Mapping[str, Any]],
        profiles: Sequence[Mapping[str, Any]],
        store: LingxingDatasetStore,
        dataset: str,
        method_name: str,
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        yesterday = date.today() - timedelta(days=1)
        start_day, end_day = _bounded_window(
            store,
            dataset,
            initial_days=1,
            overlap_days=3,
            max_days=7,
            end_day=yesterday,
        )
        allowed_sids = {int(shop["sid"]) for shop in shops}
        method = getattr(api.ads, method_name, None)
        if not callable(method):
            raise AvailableSyncError(f"{dataset}: SDK method is unavailable")
        rows: list[dict[str, Any]] = []
        current_day = start_day
        while current_day <= end_day:
            report_date = current_day.isoformat()
            for profile in profiles:
                sid = int(profile.get("sid") or 0)
                profile_id = int(profile.get("profile_id") or 0)
                if sid not in allowed_sids or profile_id <= 0:
                    continue

                async def fetch_page(
                    token: str | None,
                    sid: int = sid,
                    profile_id: int = profile_id,
                    report_date: str = report_date,
                ):
                    return await method(
                        report_date,
                        sid,
                        profile_id,
                        next_token=token,
                        length=100,
                    )

                for raw in await collect_next_token_pages(fetch_page):
                    rows.append(
                        _normalized_row(
                            dataset,
                            raw,
                            additions={
                                "sid": sid,
                                "profile_id": profile_id,
                                "report_date": report_date,
                            },
                        )
                    )
            current_day += timedelta(days=1)
        return rows, "upsert", {
            "date_from": start_day.isoformat(),
            "date_to": end_day.isoformat(),
            "overlap_days": 3,
            "profile_count": len(profiles),
        }

    async def _inventory_payload(
        self, api: Any, shops: Sequence[Mapping[str, Any]]
    ) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
        snapshot_date = date.today().isoformat()
        rows: list[dict[str, Any]] = []
        for shop in shops:
            sid = int(shop["sid"])

            async def fetch_page(offset: int, length: int, sid: int = sid):
                return await api.warehouse.FbaInventory(
                    sid,
                    offset=offset,
                    length=length,
                )

            for raw in await collect_offset_pages(fetch_page, page_size=200):
                rows.append(
                    _normalized_row(
                        "fba_inventory_snapshot",
                        raw,
                        additions={"sid": sid, "snapshot_date": snapshot_date},
                    )
                )
        return rows, "upsert", {
            "date_from": snapshot_date,
            "date_to": snapshot_date,
            "shops_count": len(shops),
        }


class AvailableLingxingSyncService(TlsLingxingSyncService):
    """Refresh shops, then synchronize only the proven local business datasets."""

    def __init__(
        self,
        store: LingxingLocalStore,
        provider_factory: Callable[[], LingxingProvider] = TlsSdkLingxingProvider,
        dataset_provider_factory: Callable[[], AvailableDatasetProvider] = (
            TlsSdkAvailableDatasetProvider
        ),
        **kwargs: Any,
    ) -> None:
        super().__init__(store, provider_factory=provider_factory, **kwargs)
        self.dataset_store = LingxingDatasetStore(store.root.parent)
        self.dataset_provider_factory = dataset_provider_factory

    def _perform_sync(self, reason: str) -> dict:
        base_status = super()._perform_sync(reason)
        if base_status.get("status") != "success":
            return self.public_status()

        state = self.store.load_state()
        state.update(
            {
                "status": "syncing",
                "message": "店铺列表已更新，正在同步可用经营数据……",
                "business_sync_started_at": utc_now(),
            }
        )
        self.store.save_state(state)
        try:
            summary = self.dataset_provider_factory().sync(
                self.store.load_credentials(),
                self.store.load_shops(),
                self.dataset_store,
            )
            failed_count = int(summary.get("failed_count") or 0)
            state.update(
                {
                    "status": "success" if failed_count == 0 else "partial_success",
                    "message": (
                        "可用经营数据已更新。"
                        if failed_count == 0
                        else f"经营数据部分更新，{failed_count} 个数据集失败并保留旧数据。"
                    ),
                    "last_success_at": utc_now(),
                    "business_last_success_at": utc_now(),
                    "business_failed_count": failed_count,
                    "last_error": "",
                }
            )
        except BaseException as exc:  # noqa: BLE001 - preserve all old data
            state.update(
                {
                    "status": "failed",
                    "message": "店铺列表已更新，但经营数据同步失败；旧数据保持可用。",
                    "last_error": _safe_error_code(exc),
                    "last_failure_at": utc_now(),
                }
            )
        finally:
            state["business_sync_finished_at"] = utc_now()
            self.store.save_state(state)
        return self.public_status()

    def public_status(self) -> dict:
        state = super().public_status()
        datasets: list[dict[str, Any]] = []
        for dataset in AVAILABLE_DATASETS:
            status = self.dataset_store.load_status(dataset)
            current = self.dataset_store.load(dataset)
            datasets.append(
                {
                    "dataset": dataset,
                    "status": status.get("status", "idle"),
                    "row_count": len(current.rows) if current else 0,
                    "last_success_at": status.get("last_success_at"),
                    "date_from": current.checkpoint.get("date_from") if current else None,
                    "date_to": current.checkpoint.get("date_to") if current else None,
                    "error_code": status.get("error_code", ""),
                }
            )
        for dataset, reason in UNAVAILABLE_DATASETS.items():
            datasets.append(
                {
                    "dataset": dataset,
                    "status": "unavailable",
                    "row_count": 0,
                    "last_success_at": None,
                    "date_from": None,
                    "date_to": None,
                    "error_code": reason,
                }
            )
        state["business_datasets"] = datasets
        state["business_storage"] = "local_atomic_generations"
        return state
