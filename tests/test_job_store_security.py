# -*- coding: utf-8 -*-
"""Synthetic tests for path-confined local job storage."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.job_store import JobStore, safe_filename


@pytest.mark.parametrize(
    "filename",
    ["../escape.csv", "CON.csv", "bad:name.csv", "~$temp.xlsx", "file.exe"],
)
def test_safe_filename_rejects_unsafe_windows_names(filename: str) -> None:
    with pytest.raises(ValueError):
        safe_filename(filename)


def test_job_ids_and_categories_cannot_escape_root(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create_job(label="Synthetic")

    with pytest.raises(ValueError, match="invalid job id"):
        store.load_job("../../outside")
    with pytest.raises(ValueError, match="invalid file category"):
        store.category_dir(job["job_id"], "../../outside")


def test_reserved_upload_path_stays_inside_category(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create_job()
    temp_path, final_path = store.reserve_file_path(
        job["job_id"], "mapping", "mapping.csv"
    )

    category = store.category_dir(job["job_id"], "mapping")
    assert category in temp_path.resolve().parents
    assert category in final_path.resolve().parents
    assert temp_path.name.endswith(".uploading")


def test_manifest_uses_only_relative_local_paths(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create_job(options={"write_excel": True, "write_html": True})

    mapping = store.category_dir(job["job_id"], "mapping") / "mapping.csv"
    business = store.category_dir(job["job_id"], "business") / "business.csv"
    mapping.write_text("seller-sku,asin1\nSKU-1,B000TEST1\n", encoding="utf-8")
    business.write_text("ASIN,Sessions\nB000TEST1,1\n", encoding="utf-8")

    manifest_path = store.build_manifest(job["job_id"])
    manifest_text = manifest_path.read_text(encoding="utf-8")

    assert str(tmp_path.resolve()) not in manifest_text
    assert "input/mapping/mapping.csv" in manifest_text
    assert "input/business/business.csv" in manifest_text


def test_artifacts_are_restricted_to_output_and_review_dirs(tmp_path: Path) -> None:
    store = JobStore(tmp_path)
    job = store.create_job()
    job_dir = store._job_dir(job["job_id"])

    private_input = store.category_dir(job["job_id"], "mapping") / "mapping.csv"
    private_input.write_text("synthetic", encoding="utf-8")
    output = job_dir / "output" / "dashboard.html"
    output.parent.mkdir(parents=True)
    output.write_text("<html>synthetic</html>", encoding="utf-8")

    assert store.resolve_artifact(job["job_id"], "output/dashboard.html") == output
    with pytest.raises(ValueError, match="not allowed"):
        store.resolve_artifact(job["job_id"], "input/mapping/mapping.csv")
    with pytest.raises(ValueError):
        store.resolve_artifact(job["job_id"], "../outside.txt")
