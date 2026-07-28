# -*- coding: utf-8 -*-
"""Privacy-safe, read-only Lingxing capability probe.

The probe uses the existing encrypted credentials and pinned fixed-egress
transport. It immediately reduces every API response to a schema-only summary:
authorization status, field names, row counts and date range. No business row,
identifier, amount, credential, proxy setting or signed download URL is stored.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from agent.lingxing_business_contract import BUSINESS_DATASETS
from agent.lingxing_secure_store import LingxingCredentials, LingxingLocalStore
from agent.lingxing_sync_foundation import (
    SalesTrafficReportRequest,
    redact_sensitive_text,
    request_sales_traffic_report,
    utc_now,
)
from agent.lingxing_tls_proxy import secure_proxy_endpoint

_PROBE_DATASETS = (
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
_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_SAFE_STATUSES = {
    "success",
    "no_data",
    "task_created",
    "not_applicable",
    "unauthorized",
    "rate_limited",
    "timeout",
    "unsupported",
    "failed",
}
_SAFE_ERROR_CODES = {
    "not_configured",
    "missing_shop_identity",
    "missing_ad_profile",
    "unauthorized",
    "rate_limited",
    "timeout",
    "unsupported",
    "invalid_response",
    "request_rejected",
    "probe_failed",
}


class ProbeError(RuntimeError):
    pass


class LingxingProbeProvider(Protocol):
    def run_probe(
        self,
        credentials: LingxingCredentials,
        cached_shops: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]: ...


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        data = value.model_dump()
        if isinstance(data, Mapping):
            return data
    if hasattr(value, "dict"):
        data = value.dict()
        if isinstance(data, Mapping):
            return data
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


def _response_rows(response: Any) -> list[Any]:
    if isinstance(response, Mapping):
        value = response.get("data", [])
    else:
        value = getattr(response, "data", [])
    return list(value or []) if isinstance(value, (list, tuple)) else []


def _response_count(response: Any, rows: Sequence[Any]) -> tuple[int, int | None]:
    mapping = _as_mapping(response)
    count_raw = mapping.get("response_count", len(rows))
    total_raw = mapping.get("total_count")
    try:
        count = max(0, int(count_raw))
    except (TypeError, ValueError):
        count = len(rows)
    try:
        total = None if total_raw is None else max(0, int(total_raw))
    except (TypeError, ValueError):
        total = None
    return count, total


def _row_fields(row: Any) -> list[str]:
    names = [str(key) for key in _as_mapping(row).keys()]
    return sorted({name for name in names if _FIELD_RE.fullmatch(name)})[:200]


def _safe_date(value: Any) -> str | None:
    match = _DATE_RE.match(str(value or "").strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def summarize_response(
    dataset: str,
    response: Any,
    *,
    date_fields: Sequence[str] = (),
    requested_from: str | None = None,
    requested_to: str | None = None,
    success_status: str = "success",
) -> dict[str, Any]:
    """Reduce a response to safe schema metadata and discard row values."""

    if dataset not in _PROBE_DATASETS:
        raise ProbeError("unknown probe dataset")
    rows = _response_rows(response)
    sampled = rows[:1]
    fields = _row_fields(sampled[0]) if sampled else []
    count, total = _response_count(response, rows)
    dates: list[str] = []
    for row in sampled:
        mapping = _as_mapping(row)
        for field_name in date_fields:
            parsed = _safe_date(mapping.get(field_name))
            if parsed:
                dates.append(parsed)
    summary = {
        "dataset": dataset,
        "status": success_status if rows or success_status == "task_created" else "no_data",
        "fields": fields,
        "sampled_rows": min(len(rows), 1),
        "response_count": count,
        "total_count": total,
        "date_from": min(dates) if dates else _safe_date(requested_from),
        "date_to": max(dates) if dates else _safe_date(requested_to),
        "error_code": "",
    }
    return validate_probe_summary(summary)


def skipped_summary(dataset: str, code: str) -> dict[str, Any]:
    status = "not_applicable" if code in {"missing_ad_profile", "missing_shop_identity"} else "failed"
    return validate_probe_summary(
        {
            "dataset": dataset,
            "status": status,
            "fields": [],
            "sampled_rows": 0,
            "response_count": 0,
            "total_count": None,
            "date_from": None,
            "date_to": None,
            "error_code": code,
        }
    )


def classify_probe_error(dataset: str, exc: BaseException) -> dict[str, Any]:
    safe = redact_sensitive_text(f"{type(exc).__name__}: {exc}", limit=300).lower()
    if any(token in safe for token in ("401", "403", "unauthorized", "permission", "forbidden")):
        status, code = "unauthorized", "unauthorized"
    elif "429" in safe or "rate" in safe or "limit" in safe:
        status, code = "rate_limited", "rate_limited"
    elif "timeout" in safe:
        status, code = "timeout", "timeout"
    elif any(token in safe for token in ("not found", "unsupported", "attributeerror")):
        status, code = "unsupported", "unsupported"
    elif any(token in safe for token in ("validation", "invalid", "parameter", "400")):
        status, code = "failed", "request_rejected"
    else:
        status, code = "failed", "probe_failed"
    return validate_probe_summary(
        {
            "dataset": dataset,
            "status": status,
            "fields": [],
            "sampled_rows": 0,
            "response_count": 0,
            "total_count": None,
            "date_from": None,
            "date_to": None,
            "error_code": code,
        }
    )


def validate_probe_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    dataset = str(value.get("dataset") or "")
    status = str(value.get("status") or "")
    error_code = str(value.get("error_code") or "")
    if dataset not in _PROBE_DATASETS:
        raise ProbeError("invalid probe dataset")
    if status not in _SAFE_STATUSES:
        raise ProbeError("invalid probe status")
    if error_code and error_code not in _SAFE_ERROR_CODES:
        raise ProbeError("invalid probe error code")
    fields = value.get("fields") or []
    if not isinstance(fields, list) or len(fields) > 200:
        raise ProbeError("invalid probe fields")
    normalized_fields = sorted({str(item) for item in fields})
    if any(not _FIELD_RE.fullmatch(item) for item in normalized_fields):
        raise ProbeError("invalid probe field name")
    normalized: dict[str, Any] = {
        "dataset": dataset,
        "status": status,
        "fields": normalized_fields,
        "sampled_rows": max(0, min(1, int(value.get("sampled_rows") or 0))),
        "response_count": max(0, int(value.get("response_count") or 0)),
        "total_count": None,
        "date_from": _safe_date(value.get("date_from")),
        "date_to": _safe_date(value.get("date_to")),
        "error_code": error_code,
    }
    total = value.get("total_count")
    if total is not None:
        normalized["total_count"] = max(0, int(total))
    return normalized


def validate_probe_result(value: Mapping[str, Any]) -> dict[str, Any]:
    status = str(value.get("status") or "")
    if status not in {"idle", "running", "success", "failed"}:
        raise ProbeError("invalid probe result status")
    datasets_raw = value.get("datasets") or []
    if not isinstance(datasets_raw, list):
        raise ProbeError("probe datasets must be a list")
    datasets = [validate_probe_summary(item) for item in datasets_raw]
    if len({item["dataset"] for item in datasets}) != len(datasets):
        raise ProbeError("duplicate probe dataset")
    return {
        "schema_version": 1,
        "mode": "read_only_schema_probe",
        "status": status,
        "running": bool(value.get("running", status == "running")),
        "started_at": str(value.get("started_at") or "")[:40],
        "finished_at": str(value.get("finished_at") or "")[:40],
        "last_success_at": str(value.get("last_success_at") or "")[:40],
        "datasets": datasets,
        "message_code": str(value.get("message_code") or "")[:64],
    }


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


class LingxingProbeResultStore:
    def __init__(self, data_root: Path):
        self.path = Path(data_root).resolve() / "lingxing" / "read_only_probe.json"

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return validate_probe_result({"status": "idle", "datasets": [], "message_code": "not_run"})
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ProbeError("probe result cannot be read") from exc
        if not isinstance(data, Mapping):
            raise ProbeError("probe result must be an object")
        return validate_probe_result(data)

    def save(self, value: Mapping[str, Any]) -> dict[str, Any]:
        normalized = validate_probe_result(value)
        _atomic_write(self.path, normalized)
        return normalized


class TlsSdkLingxingProbeProvider:
    """Use the existing fixed-egress transport and return schema summaries only."""

    def run_probe(
        self,
        credentials: LingxingCredentials,
        cached_shops: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        return asyncio.run(self._run_probe(credentials, cached_shops))

    @staticmethod
    def _shop_identity(cached_shops: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        for raw in cached_shops:
            item = dict(raw)
            try:
                sid = int(item.get("sid") or 0)
            except (TypeError, ValueError):
                continue
            seller_id = str(item.get("seller_id") or "").strip()
            marketplace_id = str(item.get("marketplace_id") or "").strip()
            region = str(item.get("region") or "").strip().upper()
            if sid > 0 and seller_id and marketplace_id and region in {"NA", "EU", "FE"}:
                return {"sid": sid, "seller_id": seller_id, "marketplace_id": marketplace_id, "region": region}
        return None

    @staticmethod
    def _matching_profile(response: Any, sid: int) -> int | None:
        for row in _response_rows(response):
            item = _as_mapping(row)
            try:
                if int(item.get("sid") or 0) == sid and int(item.get("profile_id") or 0) > 0:
                    return int(item["profile_id"])
            except (TypeError, ValueError, KeyError):
                continue
        return None

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
        except BaseException as exc:  # noqa: BLE001 - each probe must be isolated
            return classify_probe_error(dataset, exc), None

    async def _run_probe(
        self,
        credentials: LingxingCredentials,
        cached_shops: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        identity = self._shop_identity(cached_shops)
        results: list[dict[str, Any]] = []
        if identity is None:
            for dataset in _PROBE_DATASETS:
                results.append(skipped_summary(dataset, "missing_shop_identity"))
            return {"status": "success", "datasets": results}

        try:
            from aiohttp_socks import ProxyConnector
            from lingxingapi import API
        except ImportError as exc:  # pragma: no cover - covered by frozen runtime gate
            raise ProbeError("Lingxing SDK runtime unavailable") from exc

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
                    lambda: api.warehouse.FbaInventoryDetails(offset=0, length=1),
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
                        options_key="report_options",
                    ),
                    requested_from=traffic_day.isoformat(),
                    requested_to=traffic_day.isoformat(),
                    success_status="task_created",
                )
                results.append(summary)

        return {"status": "success", "datasets": results}


class LingxingReadOnlyProbeService:
    def __init__(
        self,
        connection_store: LingxingLocalStore,
        result_store: LingxingProbeResultStore,
        provider_factory: Callable[[], LingxingProbeProvider] = TlsSdkLingxingProbeProvider,
    ) -> None:
        self.connection_store = connection_store
        self.result_store = result_store
        self.provider_factory = provider_factory
        self._lock = threading.Lock()
        self._running = False
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def trigger(self) -> bool:
        if not self.connection_store.has_credentials():
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop.clear()
            self._worker = threading.Thread(target=self._run, name="lingxing-read-only-probe", daemon=True)
            self._worker.start()
            return True

    def _run(self) -> None:
        previous = self.result_store.load()
        started = utc_now()
        self.result_store.save(
            {
                "status": "running",
                "running": True,
                "started_at": started,
                "last_success_at": previous.get("last_success_at", ""),
                "datasets": previous.get("datasets", []),
                "message_code": "probe_running",
            }
        )
        try:
            credentials = self.connection_store.load_credentials()
            shops = self.connection_store.load_shops()
            raw = self.provider_factory().run_probe(credentials, shops)
            datasets = raw.get("datasets", []) if isinstance(raw, Mapping) else []
            finished = utc_now()
            self.result_store.save(
                {
                    "status": "success",
                    "running": False,
                    "started_at": started,
                    "finished_at": finished,
                    "last_success_at": finished,
                    "datasets": datasets,
                    "message_code": "probe_completed",
                }
            )
        except BaseException:  # noqa: BLE001 - public result must remain generic
            self.result_store.save(
                {
                    "status": "failed",
                    "running": False,
                    "started_at": started,
                    "finished_at": utc_now(),
                    "last_success_at": previous.get("last_success_at", ""),
                    "datasets": previous.get("datasets", []),
                    "message_code": "probe_failed",
                }
            )
        finally:
            with self._lock:
                self._running = False

    def public_status(self) -> dict[str, Any]:
        status = self.result_store.load()
        status["running"] = self.is_running()
        status["configured"] = self.connection_store.has_credentials()
        return status

    def stop(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker:
            worker.join(timeout=5)
