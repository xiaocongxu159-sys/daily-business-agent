import asyncio
import json

import pytest

from agent.lingxing_probe import ProbeError
from agent.lingxing_probe_diagnostics import (
    DiagnosticLingxingProbeResultStore,
    DiagnosticTlsSdkLingxingProbeProvider,
    classify_diagnostic_probe_error,
    validate_diagnostic_result,
)


class UnknownRequestError(RuntimeError):
    pass


class ResponseDataError(RuntimeError):
    pass


def test_unknown_lingxing_error_keeps_only_fixed_category_and_numeric_code():
    value = classify_diagnostic_probe_error(
        "sales_traffic",
        UnknownRequestError(
            "未知的 api 错误\n"
            "数据信息: {'seller_id': 'SELLER-SECRET', 'sales': 98765.43}\n"
            "错误代码: 4201234\n"
            "app_secret=SYNTHETIC-SECRET"
        ),
    )
    encoded = json.dumps(value, ensure_ascii=False)
    assert value["status"] == "failed"
    assert value["error_code"] == "request_rejected"
    assert value["diagnostic_code"] == "lingxing_unknown_request"
    assert value["remote_error_code"] == 4201234
    for forbidden in (
        "SELLER-SECRET",
        "98765.43",
        "SYNTHETIC-SECRET",
        "seller_id",
        "app_secret",
    ):
        assert forbidden not in encoded


def test_response_validation_error_is_separate_from_request_rejection():
    value = classify_diagnostic_probe_error(
        "fba_inventory_shared_detail",
        ResponseDataError("response carried ASIN-BUSINESS-SECRET"),
    )
    assert value["status"] == "failed"
    assert value["error_code"] == "invalid_response"
    assert value["diagnostic_code"] == "lingxing_response_error"
    assert value["remote_error_code"] is None
    assert "ASIN-BUSINESS-SECRET" not in json.dumps(value)


def test_diagnostic_store_reads_legacy_schema_and_writes_only_safe_fields(tmp_path):
    store = DiagnosticLingxingProbeResultStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "read_only_schema_probe",
                "status": "success",
                "running": False,
                "datasets": [
                    {
                        "dataset": "orders",
                        "status": "no_data",
                        "fields": [],
                        "sampled_rows": 0,
                        "response_count": 0,
                        "total_count": 0,
                        "date_from": "2026-07-21",
                        "date_to": "2026-07-27",
                        "error_code": "",
                    }
                ],
                "message_code": "probe_completed",
            }
        ),
        encoding="utf-8",
    )
    legacy = store.load()
    assert legacy["schema_version"] == 2
    assert legacy["datasets"][0]["diagnostic_code"] == ""
    assert legacy["datasets"][0]["remote_error_code"] is None

    saved = store.save(
        {
            "status": "success",
            "datasets": [
                {
                    "dataset": "sales_traffic",
                    "status": "failed",
                    "fields": [],
                    "sampled_rows": 0,
                    "response_count": 0,
                    "total_count": None,
                    "date_from": None,
                    "date_to": None,
                    "error_code": "request_rejected",
                    "diagnostic_code": "lingxing_unknown_request",
                    "remote_error_code": 4201234,
                    "raw_error": "ORDER-SECRET app_secret=SECRET",
                }
            ],
            "message_code": "probe_completed",
            "credentials": "DO-NOT-PERSIST",
        }
    )
    assert saved["schema_version"] == 2
    text = store.path.read_text(encoding="utf-8")
    assert "lingxing_unknown_request" in text
    assert "4201234" in text
    for forbidden in (
        "ORDER-SECRET",
        "app_secret",
        "DO-NOT-PERSIST",
        "raw_error",
        "credentials",
    ):
        assert forbidden not in text


def test_diagnostic_validation_rejects_arbitrary_text_and_large_remote_codes():
    base = {
        "status": "success",
        "datasets": [
            {
                "dataset": "sales_traffic",
                "status": "failed",
                "fields": [],
                "sampled_rows": 0,
                "response_count": 0,
                "total_count": None,
                "error_code": "request_rejected",
                "diagnostic_code": "lingxing_unknown_request",
                "remote_error_code": 4201234,
            }
        ],
    }
    assert validate_diagnostic_result(base)["datasets"][0]["remote_error_code"] == 4201234

    bad_text = json.loads(json.dumps(base))
    bad_text["datasets"][0]["diagnostic_code"] = "secret=DO-NOT-STORE"
    with pytest.raises(ProbeError):
        validate_diagnostic_result(bad_text)

    bad_number = json.loads(json.dumps(base))
    bad_number["datasets"][0]["remote_error_code"] = 10**12
    with pytest.raises(ProbeError):
        validate_diagnostic_result(bad_number)


def test_provider_capture_uses_diagnostic_classifier_without_leaking_exception():
    provider = DiagnosticTlsSdkLingxingProbeProvider()

    async def fail():
        raise UnknownRequestError(
            "错误代码: 555001\nrequest={'seller_id':'SELLER-SECRET','amount':12345.67}"
        )

    summary, response = asyncio.run(provider._capture("sales_traffic", fail))
    assert response is None
    assert summary["diagnostic_code"] == "lingxing_unknown_request"
    assert summary["remote_error_code"] == 555001
    encoded = json.dumps(summary)
    assert "SELLER-SECRET" not in encoded
    assert "12345.67" not in encoded
