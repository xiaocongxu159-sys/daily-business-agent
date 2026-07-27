# -*- coding: utf-8 -*-
"""Synthetic request-boundary tests for the local report engine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.local_engine import LocalEngineRequest, run_local_engine_from_manifest, validate_request


def write(path: Path, content: str = "synthetic") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def valid_request(workspace: Path, **overrides) -> LocalEngineRequest:
    values = {
        "workspace": workspace,
        "mapping_file": write(workspace / "input" / "mapping" / "mapping.csv"),
        "business_files": (
            write(workspace / "input" / "business" / "business.csv"),
        ),
        "write_excel": True,
        "write_html": True,
    }
    values.update(overrides)
    return LocalEngineRequest(**values)


def test_request_accepts_files_staged_inside_workspace(tmp_path: Path) -> None:
    request = validate_request(valid_request(tmp_path))
    assert request.workspace == tmp_path.resolve()
    assert all(tmp_path.resolve() in path.parents for path in (request.mapping_file, *request.business_files))


def test_request_rejects_file_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    workspace.mkdir()
    outside = write(tmp_path / "outside.csv")
    request = valid_request(workspace, mapping_file=outside)

    with pytest.raises(ValueError, match="inside workspace"):
        validate_request(request)


def test_request_rejects_unsupported_and_temporary_files(tmp_path: Path) -> None:
    unsupported = valid_request(
        tmp_path,
        mapping_file=write(tmp_path / "input" / "mapping" / "mapping.exe"),
    )
    with pytest.raises(ValueError, match="unsupported suffix"):
        validate_request(unsupported)

    temporary = valid_request(
        tmp_path,
        mapping_file=write(tmp_path / "input" / "mapping" / "~$mapping.xlsx"),
    )
    with pytest.raises(ValueError, match="temporary Office"):
        validate_request(temporary)


def test_erp_and_separate_reports_cannot_be_mixed(tmp_path: Path) -> None:
    request = valid_request(
        tmp_path,
        erp_files=(write(tmp_path / "input" / "erp" / "erp.xlsx"),),
    )
    with pytest.raises(ValueError, match="cannot be combined"):
        validate_request(request)


def test_html_output_requires_excel_output(tmp_path: Path) -> None:
    request = valid_request(tmp_path, write_excel=False, write_html=True)
    with pytest.raises(ValueError, match="requires write_excel"):
        validate_request(request)


def test_manifest_relative_paths_are_resolved_inside_manifest_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "job"
    mapping = write(workspace / "input" / "mapping" / "mapping.csv")
    business = write(workspace / "input" / "business" / "business.csv")
    manifest = workspace / "job_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "workspace": ".",
                "mapping_file": mapping.relative_to(workspace).as_posix(),
                "business_files": [business.relative_to(workspace).as_posix()],
                "write_excel": False,
                "write_html": False,
            }
        ),
        encoding="utf-8",
    )

    # Processing modules are deliberately not imported during manifest parsing.
    request = LocalEngineRequest.from_dict(
        json.loads(manifest.read_text(encoding="utf-8")),
        base_dir=manifest.parent,
    )
    validated = validate_request(request)
    assert validated.mapping_file == mapping.resolve()


def test_missing_manifest_fails_before_processing_modules_load(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="manifest not found"):
        run_local_engine_from_manifest(tmp_path / "missing.json")
