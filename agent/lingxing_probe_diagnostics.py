# -*- coding: utf-8 -*-
"""Privacy-safe diagnostics for the Lingxing read-only probe.

Only fixed diagnostic categories and an optional numeric Lingxing error code are
persisted. Exception messages, request bodies, identifiers, credentials and
business values are never stored or returned.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.lingxing_probe import (
    LingxingProbeResultStore,
    ProbeError,
    TlsSdkLingxingProbeProvider,
    classify_probe_error,
    summarize_response,
    validate_probe_result,
    validate_probe_summary,
)

_DIAGNOSTIC_CODES = {
    "lingxing_authorization",
    "lingxing_rate_limit",
    "lingxing_timeout",
    "lingxing_parameter_rejected",
    "lingxing_unknown_request",
    "lingxing_server_error",
    "lingxing_response_error",
    "sdk_response_validation",
    "sdk_contract_mismatch",
    "transport_error",
    "unsupported_operation",
    "message_classified",
    "unexpected_exception",
}
_REMOTE_CODE_RE = re.compile(
    r"(?:错误代码|error[_ ]?code|err[_ ]?code)\s*[:=]\s*['\"]?(-?\d{1,9})",
    re.IGNORECASE,
)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 6:
        chain.append(current)
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return chain


def _numeric_remote_code(chain: Sequence[BaseException]) -> int | None:
    for item in chain:
        try:
            text = str(item)[:8000]
        except BaseException:  # pragma: no cover - defensive only
            continue
        match = _REMOTE_CODE_RE.search(text)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        if -999_999_999 <= value <= 999_999_999:
            return value
    return None


def validate_diagnostic_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_probe_summary(value)
    diagnostic_code = str(value.get("diagnostic_code") or "")
    if diagnostic_code and diagnostic_code not in _DIAGNOSTIC_CODES:
        raise ProbeError("invalid probe diagnostic code")

    remote_raw = value.get("remote_error_code")
    remote_error_code: int | None = None
    if remote_raw is not None and remote_raw != "":
        try:
            remote_error_code = int(remote_raw)
        except (TypeError, ValueError) as exc:
            raise ProbeError("invalid remote error code") from exc
        if not -999_999_999 <= remote_error_code <= 999_999_999:
            raise ProbeError("remote error code is out of range")

    if not normalized["error_code"]:
        diagnostic_code = ""
        remote_error_code = None

    normalized["diagnostic_code"] = diagnostic_code
    normalized["remote_error_code"] = remote_error_code
    return normalized


def validate_diagnostic_result(value: Mapping[str, Any]) -> dict[str, Any]:
    datasets_raw = value.get("datasets") or []
    if not isinstance(datasets_raw, list):
        raise ProbeError("probe datasets must be a list")
    diagnostic_datasets = [validate_diagnostic_summary(item) for item in datasets_raw]
    if len({item["dataset"] for item in diagnostic_datasets}) != len(diagnostic_datasets):
        raise ProbeError("duplicate probe dataset")

    legacy_value = dict(value)
    legacy_value["datasets"] = [validate_probe_summary(item) for item in datasets_raw]
    normalized = validate_probe_result(legacy_value)
    normalized["schema_version"] = 2
    normalized["datasets"] = diagnostic_datasets
    return normalized


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


class DiagnosticLingxingProbeResultStore(LingxingProbeResultStore):
    """Backward-compatible store that preserves only safe diagnostics."""

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return validate_diagnostic_result(
                {"status": "idle", "datasets": [], "message_code": "not_run"}
            )
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ProbeError("probe result cannot be read") from exc
        if not isinstance(data, Mapping):
            raise ProbeError("probe result must be an object")
        return validate_diagnostic_result(data)

    def save(self, value: Mapping[str, Any]) -> dict[str, Any]:
        normalized = validate_diagnostic_result(value)
        _atomic_write(self.path, normalized)
        return normalized


def classify_diagnostic_probe_error(dataset: str, exc: BaseException) -> dict[str, Any]:
    chain = _exception_chain(exc)
    names = {type(item).__name__ for item in chain}
    remote_error_code = _numeric_remote_code(chain)

    if names & {
        "AuthorizationError",
        "UnauthorizedApiError",
        "UnauthorizedRequestIpError",
        "AppIdOrSecretError",
        "TokenError",
        "TokenExpiredError",
        "AccessTokenExpiredError",
        "RefreshTokenExpiredError",
        "InvalidTokenError",
        "InvalidAccessTokenError",
        "InvalidRefreshTokenError",
        "SignatureError",
        "SignatureExpiredError",
        "InvalidSignatureError",
    }:
        status, error_code, diagnostic_code = (
            "unauthorized",
            "unauthorized",
            "lingxing_authorization",
        )
    elif names & {"ApiLimitError", "TooManyRequestsError"}:
        status, error_code, diagnostic_code = (
            "rate_limited",
            "rate_limited",
            "lingxing_rate_limit",
        )
    elif names & {"ApiTimeoutError", "TimeoutError"}:
        status, error_code, diagnostic_code = "timeout", "timeout", "lingxing_timeout"
    elif names & {"InvalidParametersError", "InvalidApiUrlError", "ParametersError"}:
        status, error_code, diagnostic_code = (
            "failed",
            "request_rejected",
            "lingxing_parameter_rejected",
        )
    elif "UnknownRequestError" in names:
        status, error_code, diagnostic_code = (
            "failed",
            "request_rejected",
            "lingxing_unknown_request",
        )
    elif names & {"InternalServerError", "ServerError"}:
        status, error_code, diagnostic_code = (
            "failed",
            "probe_failed",
            "lingxing_server_error",
        )
    elif names & {"ResponseDataError", "ReponseError"}:
        status, error_code, diagnostic_code = (
            "failed",
            "invalid_response",
            "lingxing_response_error",
        )
    elif "ValidationError" in names:
        status, error_code, diagnostic_code = (
            "failed",
            "invalid_response",
            "sdk_response_validation",
        )
    elif names & {"InternetConnectionError", "ClientConnectionError", "ClientPayloadError"}:
        status, error_code, diagnostic_code = "timeout", "timeout", "transport_error"
    elif names & {"AttributeError", "NotImplementedError"}:
        status, error_code, diagnostic_code = (
            "unsupported",
            "unsupported",
            "unsupported_operation",
        )
    elif "TypeError" in names:
        status, error_code, diagnostic_code = (
            "unsupported",
            "unsupported",
            "sdk_contract_mismatch",
        )
    else:
        legacy = classify_probe_error(dataset, exc)
        status = legacy["status"]
        error_code = legacy["error_code"]
        diagnostic_code = (
            "message_classified" if error_code != "probe_failed" else "unexpected_exception"
        )

    return validate_diagnostic_summary(
        {
            "dataset": dataset,
            "status": status,
            "fields": [],
            "sampled_rows": 0,
            "response_count": 0,
            "total_count": None,
            "date_from": None,
            "date_to": None,
            "error_code": error_code,
            "diagnostic_code": diagnostic_code,
            "remote_error_code": remote_error_code,
        }
    )


class DiagnosticTlsSdkLingxingProbeProvider(TlsSdkLingxingProbeProvider):
    """Use the normal probe calls but preserve safe exception categories."""

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
            return classify_diagnostic_probe_error(dataset, exc), None
