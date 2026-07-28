# -*- coding: utf-8 -*-
"""Credential-free foundations for future Lingxing business-data sync.

Nothing in this module opens a network connection or reads credentials.  The
network-facing callables are injected so pagination, report polling and request
payloads can be verified with synthetic responses before a real account is
used.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar

from agent.lingxing_business_contract import (
    BUSINESS_DATASETS,
    SALES_TRAFFIC_REPORT_OPTIONS,
    SALES_TRAFFIC_REPORT_TYPE,
)

T = TypeVar("T")
_MISSING = object()
_DATASET_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_GENERATION_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{16}$")
_OPTIONS_KEYS = {"report_options", "reportOptions"}
_SENSITIVE_KEYS = {
    "appid",
    "appsecret",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "proxy_url",
    "proxyurl",
    "relay_password",
    "relaypassword",
    "certificate_sha256",
    "certificatesha256",
    "authorization",
    "sign",
    "signature",
    "download_url",
    "downloadurl",
    "x_amz_signature",
    "xamzsignature",
}


class PaginationError(RuntimeError):
    pass


class ReportTaskError(RuntimeError):
    pass


class DatasetStoreError(RuntimeError):
    pass


class SignedSourceApi(Protocol):
    async def _request_with_sign(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        body: dict | None = None,
        headers: dict | None = None,
        extract_data: bool = False,
    ) -> Any: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
    elif hasattr(value, name):
        return getattr(value, name)
    if default is _MISSING:
        raise PaginationError(f"response is missing {name}")
    return default


def _page_items(response: Any) -> list[Any]:
    data = _get_field(response, "data")
    if not isinstance(data, (list, tuple)):
        raise PaginationError("response data must be a list")
    response_count = _get_field(response, "response_count", None)
    if response_count is not None and int(response_count) != len(data):
        raise PaginationError("response_count does not match data length")
    return list(data)


async def collect_offset_pages(
    fetch_page: Callable[[int, int], Awaitable[Any]],
    *,
    page_size: int = 200,
    max_pages: int = 10_000,
) -> list[Any]:
    """Collect an offset/length endpoint with strict no-progress checks."""

    if page_size <= 0 or max_pages <= 0:
        raise ValueError("page_size and max_pages must be positive")
    items: list[Any] = []
    offset = 0
    for _ in range(max_pages):
        response = await fetch_page(offset, page_size)
        page = _page_items(response)
        total_raw = _get_field(response, "total_count", None)
        total = None if total_raw is None else int(total_raw)
        if total is not None and total < 0:
            raise PaginationError("total_count must be non-negative")
        if total is not None and total < len(items) + len(page):
            raise PaginationError("total_count is smaller than accumulated data")
        if not page:
            if total is None or len(items) >= total:
                return items
            raise PaginationError("offset pagination made no progress before total_count")
        items.extend(page)
        offset += len(page)
        if total is not None and len(items) >= total:
            return items
        if total is None and len(page) < page_size:
            return items
    raise PaginationError("offset pagination exceeded max_pages")


async def collect_next_token_pages(
    fetch_page: Callable[[str | None], Awaitable[Any]],
    *,
    max_pages: int = 10_000,
) -> list[Any]:
    """Collect a cursor endpoint and reject repeated or empty cursors."""

    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    items: list[Any] = []
    token: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        response = await fetch_page(token)
        page = _page_items(response)
        next_raw = _get_field(response, "next_token", None)
        next_token = str(next_raw).strip() if next_raw is not None else ""
        items.extend(page)
        if not next_token:
            return items
        if not page:
            raise PaginationError("next-token pagination returned an empty continuation page")
        if next_token in seen or next_token == token:
            raise PaginationError("next-token pagination repeated a cursor")
        seen.add(next_token)
        token = next_token
    raise PaginationError("next-token pagination exceeded max_pages")


def _parse_aware_time(value: str, field_name: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return parsed


@dataclass(frozen=True)
class SalesTrafficReportRequest:
    seller_id: str
    marketplace_ids: tuple[str, ...]
    region: Literal["NA", "EU", "FE"]
    start_time: str
    end_time: str

    def validated(self) -> "SalesTrafficReportRequest":
        seller_id = self.seller_id.strip()
        marketplace_ids = tuple(dict.fromkeys(item.strip() for item in self.marketplace_ids if item.strip()))
        if not seller_id:
            raise ValueError("seller_id is required")
        if not marketplace_ids:
            raise ValueError("at least one marketplace_id is required")
        if self.region not in {"NA", "EU", "FE"}:
            raise ValueError("region must be NA, EU or FE")
        start = _parse_aware_time(self.start_time, "start_time")
        end = _parse_aware_time(self.end_time, "end_time")
        if end < start:
            raise ValueError("end_time must not be before start_time")
        if end - start > timedelta(days=30):
            raise ValueError("sales and traffic report windows must not exceed 30 days")
        return SalesTrafficReportRequest(
            seller_id=seller_id,
            marketplace_ids=marketplace_ids,
            region=self.region,
            start_time=start.isoformat(),
            end_time=end.isoformat(),
        )

    def to_body(self, *, options_key: str) -> dict[str, Any]:
        request = self.validated()
        if options_key not in _OPTIONS_KEYS:
            raise ValueError("options_key must be explicitly report_options or reportOptions")
        return {
            "seller_id": request.seller_id,
            "marketplace_ids": list(request.marketplace_ids),
            "region": request.region,
            "report_type": SALES_TRAFFIC_REPORT_TYPE,
            "start_time": request.start_time,
            "end_time": request.end_time,
            options_key: dict(SALES_TRAFFIC_REPORT_OPTIONS),
        }


async def request_sales_traffic_report(
    source_api: SignedSourceApi,
    request: SalesTrafficReportRequest,
    *,
    options_key: str,
) -> Any:
    """Send the explicitly extended report request through the SDK signer.

    The transport key is deliberately mandatory.  A controlled field probe must
    establish whether the Lingxing endpoint accepts ``report_options`` or
    ``reportOptions``; this helper never silently retries another shape.
    """

    signer = getattr(source_api, "_request_with_sign", None)
    if not callable(signer):
        raise TypeError("source_api does not provide the signed request method")
    body = request.to_body(options_key=options_key)
    return await signer(
        "POST",
        BUSINESS_DATASETS["sales_traffic"].endpoint,
        body=body,
    )


@dataclass(frozen=True)
class ReportDownloadTicket:
    task_id: str
    progress_status: str
    report_document_id: str
    compression_algorithm: str
    download_url: str = field(repr=False)

    def safe_metadata(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "progress_status": self.progress_status,
            "report_document_id": self.report_document_id,
            "compression_algorithm": self.compression_algorithm,
        }


def _report_data(response: Any) -> Mapping[str, Any]:
    data = _get_field(response, "data", response)
    if not isinstance(data, Mapping):
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        elif hasattr(data, "__dict__"):
            data = vars(data)
    if not isinstance(data, Mapping):
        raise ReportTaskError("report task response data must be an object")
    return data


async def poll_report_task(
    fetch_status: Callable[[str], Awaitable[Any]],
    task_id: str,
    *,
    max_attempts: int = 60,
    interval_seconds: float = 2.0,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ReportDownloadTicket:
    """Poll an async report without persisting or logging the signed URL."""

    task_id = str(task_id or "").strip()
    if not task_id:
        raise ValueError("task_id is required")
    if max_attempts <= 0 or interval_seconds < 0:
        raise ValueError("invalid report polling limits")
    pending = {"PENDING", "QUEUED", "IN_QUEUE", "IN_PROGRESS", "PROCESSING", "RUNNING"}
    complete = {"DONE", "SUCCESS", "SUCCEEDED", "COMPLETED", "FINISHED"}
    failed = {"FAILED", "FATAL", "CANCELLED", "CANCELED", "EXPIRED"}

    for attempt in range(max_attempts):
        data = _report_data(await fetch_status(task_id))
        status = str(data.get("progress_status") or data.get("status") or "").strip().upper()
        if not status:
            raise ReportTaskError("report task response is missing progress_status")
        if status in failed:
            raise ReportTaskError(f"report task ended with status {status}")
        if status in complete:
            document_id = str(data.get("report_document_id") or "").strip()
            url = str(data.get("url") or "").strip()
            if not document_id or not url:
                raise ReportTaskError("completed report task is missing document id or download URL")
            return ReportDownloadTicket(
                task_id=task_id,
                progress_status=status,
                report_document_id=document_id,
                compression_algorithm=str(data.get("compression_algorithm") or "").strip(),
                download_url=url,
            )
        if status not in pending:
            raise ReportTaskError(f"unknown report task status {status}")
        if attempt + 1 < max_attempts:
            await sleeper(interval_seconds)
    raise ReportTaskError("report task did not finish before max_attempts")


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", value.lower().replace("-", "_"))


def _reject_sensitive(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = _normalized_key(str(key))
            if name in _SENSITIVE_KEYS:
                raise DatasetStoreError(f"{path} contains forbidden sensitive field {key}")
            _reject_sensitive(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_sensitive(child, f"{path}[{index}]")


def redact_sensitive_text(value: Any, *, limit: int = 1000) -> str:
    """Return a bounded diagnostic string with common secret forms removed."""

    text = str(value or "")
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <redacted>", text)
    text = re.sub(r"(https?://)([^/@\s:]+):([^/@\s]+)@", r"\1<redacted>:<redacted>@", text)
    names = (
        r"app[_-]?secret|access[_-]?token|refresh[_-]?token|proxy[_-]?url|"
        r"relay[_-]?password|certificate[_-]?sha256|x-amz-signature|signature|sign"
    )
    text = re.sub(
        rf"(?i)({names})(\s*[:=]\s*)([\"']?)[^\s,&;\"']+",
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        text,
    )
    text = re.sub(
        rf"(?i)([?&](?:{names})=)[^&#\s]+",
        lambda match: f"{match.group(1)}<redacted>",
        text,
    )
    return text[: max(0, int(limit))]


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
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


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(_canonical_bytes(value).decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise DatasetStoreError("dataset payload must be JSON serializable") from exc


@dataclass(frozen=True)
class DatasetSnapshot:
    dataset: str
    generation: str
    committed_at: str
    rows: tuple[dict[str, Any], ...]
    data_sha256: str
    checkpoint: Mapping[str, Any]


class LingxingDatasetStore:
    """Generation-based local store whose current pointer changes atomically."""

    def __init__(
        self,
        data_root: Path,
        *,
        before_activate: Callable[[Path], None] | None = None,
    ) -> None:
        self.root = Path(data_root).resolve() / "lingxing" / "business_data"
        self.root.mkdir(parents=True, exist_ok=True)
        self._before_activate = before_activate

    def _dataset_dir(self, dataset: str) -> Path:
        name = str(dataset or "").strip()
        if not _DATASET_RE.fullmatch(name) or name not in BUSINESS_DATASETS:
            raise DatasetStoreError("invalid or unknown dataset")
        path = (self.root / name).resolve()
        if self.root not in path.parents:
            raise DatasetStoreError("dataset path escaped business_data root")
        return path

    def _generation_dir(self, dataset: str, generation: str) -> Path:
        if not _GENERATION_RE.fullmatch(str(generation or "")):
            raise DatasetStoreError("invalid generation id")
        dataset_dir = self._dataset_dir(dataset)
        path = (dataset_dir / "generations" / generation).resolve()
        if dataset_dir not in path.parents:
            raise DatasetStoreError("generation path escaped dataset root")
        return path

    @staticmethod
    def _identity(row: Mapping[str, Any], fields: Sequence[str], dataset: str) -> tuple[Any, ...]:
        values: list[Any] = []
        for field_name in fields:
            if field_name not in row or row[field_name] is None or row[field_name] == "":
                raise DatasetStoreError(f"{dataset}: missing identity field {field_name}")
            values.append(row[field_name])
        return tuple(values)

    def _validated_rows(self, dataset: str, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        contract = BUSINESS_DATASETS[dataset]
        output: list[dict[str, Any]] = []
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping):
                raise DatasetStoreError(f"{dataset}: row {index} must be an object")
            row = _json_clone(dict(raw))
            _reject_sensitive(row, f"{dataset}[{index}]")
            missing = [name for name in contract.required_output_fields if name not in row]
            if missing:
                raise DatasetStoreError(f"{dataset}: row {index} is missing required fields: {', '.join(missing)}")
            self._identity(row, contract.identity_fields, dataset)
            output.append(row)
        return output

    def load(self, dataset: str) -> DatasetSnapshot | None:
        dataset_dir = self._dataset_dir(dataset)
        pointer_path = dataset_dir / "current.json"
        if not pointer_path.is_file():
            return None
        try:
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            generation = str(pointer["generation"])
            generation_dir = self._generation_dir(dataset, generation)
            rows = json.loads((generation_dir / "rows.json").read_text(encoding="utf-8"))
            metadata = json.loads((generation_dir / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DatasetStoreError(f"{dataset}: current generation cannot be read") from exc
        if not isinstance(rows, list) or not isinstance(metadata, Mapping):
            raise DatasetStoreError(f"{dataset}: invalid generation files")
        digest = hashlib.sha256(_canonical_bytes(rows)).hexdigest()
        if metadata.get("dataset") != dataset or metadata.get("generation") != generation:
            raise DatasetStoreError(f"{dataset}: generation metadata mismatch")
        if metadata.get("data_sha256") != digest or int(metadata.get("row_count", -1)) != len(rows):
            raise DatasetStoreError(f"{dataset}: generation integrity check failed")
        checkpoint = metadata.get("checkpoint") or {}
        if not isinstance(checkpoint, Mapping):
            raise DatasetStoreError(f"{dataset}: invalid checkpoint")
        return DatasetSnapshot(
            dataset=dataset,
            generation=generation,
            committed_at=str(metadata.get("committed_at") or ""),
            rows=tuple(dict(row) for row in rows),
            data_sha256=digest,
            checkpoint=dict(checkpoint),
        )

    def commit(
        self,
        dataset: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        mode: Literal["replace", "upsert"] = "upsert",
        checkpoint: Mapping[str, Any] | None = None,
        committed_at: str | None = None,
    ) -> DatasetSnapshot:
        if mode not in {"replace", "upsert"}:
            raise ValueError("mode must be replace or upsert")
        dataset_dir = self._dataset_dir(dataset)
        contract = BUSINESS_DATASETS[dataset]
        incoming = self._validated_rows(dataset, rows)
        checkpoint_value = _json_clone(dict(checkpoint or {}))
        _reject_sensitive(checkpoint_value, f"{dataset}.checkpoint")

        merged: dict[tuple[Any, ...], dict[str, Any]] = {}
        if mode == "upsert":
            current = self.load(dataset)
            if current is not None:
                for row in current.rows:
                    merged[self._identity(row, contract.identity_fields, dataset)] = dict(row)
        for row in incoming:
            merged[self._identity(row, contract.identity_fields, dataset)] = row
        final_rows = [
            merged[key]
            for key in sorted(merged, key=lambda item: _canonical_bytes(item))
        ]
        timestamp = committed_at or utc_now()
        _parse_aware_time(timestamp, "committed_at")
        generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:16]
        generation_dir = self._generation_dir(dataset, generation)
        generation_dir.mkdir(parents=True, exist_ok=False)
        digest = hashlib.sha256(_canonical_bytes(final_rows)).hexdigest()
        checkpoint_payload = {
            "status": "success",
            "last_attempt_at": timestamp,
            "last_success_at": timestamp,
            **checkpoint_value,
        }
        metadata = {
            "schema_version": 1,
            "dataset": dataset,
            "generation": generation,
            "committed_at": timestamp,
            "row_count": len(final_rows),
            "data_sha256": digest,
            "identity_fields": list(contract.identity_fields),
            "checkpoint": checkpoint_payload,
        }
        _atomic_json_write(generation_dir / "rows.json", final_rows)
        _atomic_json_write(generation_dir / "metadata.json", metadata)
        if self._before_activate is not None:
            self._before_activate(generation_dir)
        _atomic_json_write(dataset_dir / "current.json", {"generation": generation})
        _atomic_json_write(dataset_dir / "sync_status.json", checkpoint_payload)
        snapshot = self.load(dataset)
        if snapshot is None:
            raise DatasetStoreError(f"{dataset}: committed generation was not activated")
        return snapshot

    def mark_syncing(self, dataset: str, *, attempted_at: str | None = None) -> dict[str, Any]:
        current = self.load(dataset)
        payload = {
            "status": "syncing",
            "last_attempt_at": attempted_at or utc_now(),
            "last_success_at": current.checkpoint.get("last_success_at") if current else None,
            "current_generation": current.generation if current else None,
        }
        _atomic_json_write(self._dataset_dir(dataset) / "sync_status.json", payload)
        return payload

    def record_failure(
        self,
        dataset: str,
        error: Any,
        *,
        error_code: str = "sync_failed",
        attempted_at: str | None = None,
    ) -> dict[str, Any]:
        current = self.load(dataset)
        payload = {
            "status": "failed",
            "last_attempt_at": attempted_at or utc_now(),
            "last_success_at": current.checkpoint.get("last_success_at") if current else None,
            "current_generation": current.generation if current else None,
            "error_code": re.sub(r"[^a-z0-9_]+", "_", str(error_code).lower()).strip("_")[:64] or "sync_failed",
            "message": redact_sensitive_text(error),
        }
        _atomic_json_write(self._dataset_dir(dataset) / "sync_status.json", payload)
        return payload

    def load_status(self, dataset: str) -> dict[str, Any]:
        path = self._dataset_dir(dataset) / "sync_status.json"
        if not path.is_file():
            current = self.load(dataset)
            return {
                "status": "success" if current else "idle",
                "last_success_at": current.checkpoint.get("last_success_at") if current else None,
                "current_generation": current.generation if current else None,
            }
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise DatasetStoreError(f"{dataset}: sync status cannot be read") from exc
        if not isinstance(value, dict):
            raise DatasetStoreError(f"{dataset}: sync status must be an object")
        return value
