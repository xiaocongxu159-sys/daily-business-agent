import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.lingxing_sync_foundation import (
    DatasetStoreError,
    LingxingDatasetStore,
    PaginationError,
    ReportTaskError,
    SalesTrafficReportRequest,
    collect_next_token_pages,
    collect_offset_pages,
    poll_report_task,
    redact_sensitive_text,
    request_sales_traffic_report,
)


def run(awaitable):
    return asyncio.run(awaitable)


def order_row(order_id="ORDER-1", *, amount=10.0):
    return {
        "sid": 101,
        "amazon_order_id": order_id,
        "order_status": "Shipped",
        "asin": "B0SYNTHETIC1",
        "msku": "MSKU-1",
        "lsku": "LSKU-1",
        "order_qty": 1,
        "sales_amt": amount,
        "currency_code": "USD",
        "purchase_time_utc": "2026-07-01T01:00:00+00:00",
        "purchase_date_loc": "2026-06-30",
        "update_time_ts": "2026-07-01T02:00:00+00:00",
    }


def inventory_row(snapshot_date, *, fulfillable):
    return {
        "snapshot_date": snapshot_date,
        "sid": 101,
        "asin": "B0SYNTHETIC1",
        "msku": "MSKU-1",
        "lsku": "LSKU-1",
        "fnsku": "FNSKU-1",
        "afn_fulfillable_qty": fulfillable,
        "afn_unsellable_qty": 2,
        "afn_reserved_fc_processing_qty": 3,
        "afn_reserved_fc_transfers_qty": 4,
        "afn_reserved_customer_order_qty": 5,
        "afn_inbound_working_qty": 6,
        "afn_inbound_shipped_qty": 7,
        "afn_inbound_receiving_qty": 8,
    }


def test_offset_pagination_uses_actual_progress_and_total_count():
    calls = []

    async def fetch(offset, length):
        calls.append((offset, length))
        all_rows = [{"id": value} for value in range(5)]
        page = all_rows[offset : offset + length]
        return SimpleNamespace(data=page, response_count=len(page), total_count=5)

    assert run(collect_offset_pages(fetch, page_size=2)) == [
        {"id": 0},
        {"id": 1},
        {"id": 2},
        {"id": 3},
        {"id": 4},
    ]
    assert calls == [(0, 2), (2, 2), (4, 2)]


def test_offset_pagination_rejects_no_progress_and_bad_counts():
    async def empty_before_total(offset, length):
        return {"data": [], "response_count": 0, "total_count": 1}

    async def bad_response_count(offset, length):
        return {"data": [{"id": 1}], "response_count": 2, "total_count": 1}

    with pytest.raises(PaginationError, match="no progress"):
        run(collect_offset_pages(empty_before_total))
    with pytest.raises(PaginationError, match="response_count"):
        run(collect_offset_pages(bad_response_count))


def test_next_token_pagination_follows_cursor_and_rejects_repetition():
    async def fetch(token):
        if token is None:
            return {"data": [{"id": 1}], "response_count": 1, "next_token": "page-2"}
        return {"data": [{"id": 2}], "response_count": 1, "next_token": ""}

    assert run(collect_next_token_pages(fetch)) == [{"id": 1}, {"id": 2}]

    async def repeated(token):
        return {"data": [{"id": 1}], "response_count": 1, "next_token": "same"}

    with pytest.raises(PaginationError, match="repeated"):
        run(collect_next_token_pages(repeated, max_pages=3))


def test_sales_traffic_adapter_requires_explicit_report_options_shape():
    request = SalesTrafficReportRequest(
        seller_id="SELLER-1",
        marketplace_ids=("ATVPDKIKX0DER",),
        region="NA",
        start_time="2026-07-01T00:00:00+00:00",
        end_time="2026-07-07T23:59:59+00:00",
    )
    body = request.to_body(options_key="report_options")
    assert body["report_type"] == "GET_SALES_AND_TRAFFIC_REPORT"
    assert body["report_options"] == {
        "dateGranularity": "DAY",
        "asinGranularity": "SKU",
    }
    with pytest.raises(ValueError, match="explicitly"):
        request.to_body(options_key="options")

    class FakeSource:
        def __init__(self):
            self.calls = []

        async def _request_with_sign(self, method, url, params=None, body=None, headers=None, extract_data=False):
            self.calls.append({"method": method, "url": url, "params": params, "body": body})
            return {"code": 0, "data": {"task_id": "TASK-1"}}

    source = FakeSource()
    response = run(request_sales_traffic_report(source, request, options_key="reportOptions"))
    assert response["data"]["task_id"] == "TASK-1"
    assert source.calls[0]["url"] == "/basicOpen/report/create/reportExportTask"
    assert source.calls[0]["body"]["reportOptions"]["asinGranularity"] == "SKU"
    assert "report_options" not in source.calls[0]["body"]


def test_sales_traffic_request_rejects_unbounded_or_unzoned_windows():
    with pytest.raises(ValueError, match="UTC offset"):
        SalesTrafficReportRequest(
            "SELLER-1",
            ("SITE-1",),
            "NA",
            "2026-01-01T00:00:00",
            "2026-01-02T00:00:00+00:00",
        ).validated()
    with pytest.raises(ValueError, match="30 days"):
        SalesTrafficReportRequest(
            "SELLER-1",
            ("SITE-1",),
            "NA",
            "2026-01-01T00:00:00+00:00",
            "2026-02-15T00:00:00+00:00",
        ).validated()


def test_report_polling_returns_transient_url_but_safe_metadata_excludes_it():
    responses = iter(
        [
            {"data": {"progress_status": "PROCESSING"}},
            {
                "data": {
                    "progress_status": "DONE",
                    "report_document_id": "DOC-1",
                    "compression_algorithm": "GZIP",
                    "url": "https://download.example.invalid/file?signature=SYNTHETIC-SECRET",
                }
            },
        ]
    )
    sleeps = []

    async def fetch(task_id):
        assert task_id == "TASK-1"
        return next(responses)

    async def sleep(delay):
        sleeps.append(delay)

    ticket = run(poll_report_task(fetch, "TASK-1", interval_seconds=0.25, sleeper=sleep))
    assert ticket.download_url.startswith("https://")
    assert "SYNTHETIC-SECRET" not in repr(ticket)
    assert "url" not in ticket.safe_metadata()
    assert sleeps == [0.25]


def test_report_polling_fails_closed_on_unknown_failure_and_timeout():
    async def failed(task_id):
        return {"data": {"progress_status": "FAILED"}}

    async def unknown(task_id):
        return {"data": {"progress_status": "MYSTERY"}}

    async def pending(task_id):
        return {"data": {"progress_status": "PROCESSING"}}

    async def no_sleep(delay):
        return None

    with pytest.raises(ReportTaskError, match="FAILED"):
        run(poll_report_task(failed, "TASK-1"))
    with pytest.raises(ReportTaskError, match="unknown"):
        run(poll_report_task(unknown, "TASK-1"))
    with pytest.raises(ReportTaskError, match="max_attempts"):
        run(poll_report_task(pending, "TASK-1", max_attempts=2, interval_seconds=0, sleeper=no_sleep))


def test_dataset_store_upsert_is_idempotent_and_late_corrections_replace_rows(tmp_path):
    store = LingxingDatasetStore(tmp_path)
    first = store.commit(
        "orders",
        [order_row("ORDER-1", amount=10), order_row("ORDER-2", amount=20)],
        checkpoint={"date_from": "2026-06-30", "date_to": "2026-07-01"},
    )
    assert len(first.rows) == 2

    same = store.commit("orders", [order_row("ORDER-1", amount=10)])
    assert len(same.rows) == 2
    assert {row["amazon_order_id"] for row in same.rows} == {"ORDER-1", "ORDER-2"}

    corrected = store.commit("orders", [order_row("ORDER-1", amount=12.5)])
    assert len(corrected.rows) == 2
    values = {row["amazon_order_id"]: row["sales_amt"] for row in corrected.rows}
    assert values == {"ORDER-1": 12.5, "ORDER-2": 20}


def test_dataset_activation_is_atomic_and_preserves_previous_success(tmp_path):
    stable = LingxingDatasetStore(tmp_path)
    old = stable.commit("orders", [order_row(amount=10)])

    def fail_before_activate(generation_dir: Path):
        assert (generation_dir / "rows.json").is_file()
        assert (generation_dir / "metadata.json").is_file()
        raise RuntimeError("simulated power loss")

    interrupted = LingxingDatasetStore(tmp_path, before_activate=fail_before_activate)
    with pytest.raises(RuntimeError, match="power loss"):
        interrupted.commit("orders", [order_row(amount=99)])

    current = stable.load("orders")
    assert current is not None
    assert current.generation == old.generation
    assert current.rows[0]["sales_amt"] == 10


def test_dataset_validation_rejects_missing_fields_sensitive_values_and_path_escape(tmp_path):
    store = LingxingDatasetStore(tmp_path)
    incomplete = order_row()
    incomplete.pop("currency_code")
    with pytest.raises(DatasetStoreError, match="currency_code"):
        store.commit("orders", [incomplete])

    sensitive = order_row()
    sensitive["app_secret"] = "SYNTHETIC-DO-NOT-STORE"
    with pytest.raises(DatasetStoreError, match="sensitive"):
        store.commit("orders", [sensitive])

    with pytest.raises(DatasetStoreError, match="invalid"):
        store.load("../orders")
    assert store.load("orders") is None


def test_inventory_snapshots_remain_separate_by_date_without_summing(tmp_path):
    store = LingxingDatasetStore(tmp_path)
    store.commit("fba_inventory_snapshot", [inventory_row("2026-07-01", fulfillable=100)])
    snapshot = store.commit("fba_inventory_snapshot", [inventory_row("2026-07-02", fulfillable=80)])
    assert len(snapshot.rows) == 2
    assert {
        row["snapshot_date"]: row["afn_fulfillable_qty"]
        for row in snapshot.rows
    } == {"2026-07-01": 100, "2026-07-02": 80}


def test_failure_status_is_redacted_and_keeps_current_generation(tmp_path):
    store = LingxingDatasetStore(tmp_path)
    current = store.commit("orders", [order_row()])
    error = (
        "app_secret=SYNTHETIC-APP-SECRET "
        "access_token=SYNTHETIC-TOKEN "
        "https://user:password@example.invalid/file?x-amz-signature=SYNTHETIC-SIGNATURE"
    )
    status = store.record_failure("orders", error, error_code="network timeout")
    serialized = str(status)
    assert "SYNTHETIC" not in serialized
    assert "password" not in serialized
    assert status["current_generation"] == current.generation
    assert store.load("orders").generation == current.generation
    assert store.load_status("orders")["status"] == "failed"


def test_redaction_is_bounded_and_removes_bearer_and_query_secrets():
    raw = "Bearer SECRET access_token=TOKEN https://u:p@example.invalid/?signature=SIG" + "x" * 2000
    safe = redact_sensitive_text(raw, limit=200)
    assert len(safe) <= 200
    assert "SECRET" not in safe
    assert "TOKEN" not in safe
    assert "SIG" not in safe
    assert "u:p@" not in safe
