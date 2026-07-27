# -*- coding: utf-8 -*-
"""Atomic, path-confined job persistence for the Windows local agent."""
from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

CATEGORY_DIRS = {
    "mapping": "mapping",
    "erp": "erp",
    "business": "business",
    "advertising": "advertising",
    "inventory": "inventory",
    "plan": "plan",
}
ALLOWED_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xls"}
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
WINDOWS_INVALID_CHARS = set('<>:"/\\|?*')
JOB_ID_RE = re.compile(r"^\d{8}_[0-9a-f]{8}$")
MUTABLE_STATUSES = {"draft", "failed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(filename: str) -> str:
    raw_name = str(filename or "")
    name = raw_name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("filename is empty or invalid")
    if name != raw_name or name.endswith((" ", ".")):
        raise ValueError("filename must not start/end with spaces or end with a dot")
    if any(ord(char) < 32 or char in WINDOWS_INVALID_CHARS for char in name):
        raise ValueError("filename contains characters not allowed on Windows")
    if name.startswith("~$"):
        raise ValueError("temporary Office files are not accepted")
    if Path(name).stem.upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError("filename uses a Windows reserved device name")
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported file suffix: {Path(name).suffix}")
    return name


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


class JobStore:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root).resolve()
        self.jobs_root = self.data_root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _job_dir(self, job_id: str) -> Path:
        if not JOB_ID_RE.fullmatch(str(job_id)):
            raise ValueError("invalid job id")
        path = (self.jobs_root / job_id).resolve()
        if self.jobs_root != path and self.jobs_root not in path.parents:
            raise ValueError("job path escaped jobs root")
        return path

    def _job_file(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def create_job(self, label: str = "", options: dict | None = None) -> dict:
        with self._lock:
            while True:
                job_id = f"{datetime.now().strftime('%Y%m%d')}_{secrets.token_hex(4)}"
                job_dir = self._job_dir(job_id)
                if not job_dir.exists():
                    break
            for category_dir in CATEGORY_DIRS.values():
                (job_dir / "input" / category_dir).mkdir(parents=True, exist_ok=True)
            payload = {
                "job_id": job_id,
                "label": str(label or "")[:100],
                "status": "draft",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "options": options or {"write_excel": True, "write_html": True},
                "files": {category: [] for category in CATEGORY_DIRS},
                "result": None,
                "error": None,
            }
            _atomic_json_write(self._job_file(job_id), payload)
            return payload

    def load_job(self, job_id: str) -> dict:
        path = self._job_file(job_id)
        if not path.is_file():
            raise FileNotFoundError("job not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def save_job(self, job: dict) -> dict:
        with self._lock:
            job = dict(job)
            job["updated_at"] = utc_now()
            _atomic_json_write(self._job_file(job["job_id"]), job)
            return job

    def list_jobs(self) -> list[dict]:
        jobs = []
        for path in sorted(self.jobs_root.glob("*/job.json"), reverse=True):
            try:
                jobs.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return jobs

    def category_dir(self, job_id: str, category: str) -> Path:
        if category not in CATEGORY_DIRS:
            raise ValueError("invalid file category")
        job_dir = self._job_dir(job_id)
        path = (job_dir / "input" / CATEGORY_DIRS[category]).resolve()
        if job_dir not in path.parents:
            raise ValueError("category path escaped job")
        return path

    def ensure_mutable(self, job: dict) -> None:
        if job.get("status") not in MUTABLE_STATUSES:
            raise ValueError(f"job cannot be modified while status={job.get('status')}")

    def existing_input_bytes(self, job_id: str) -> int:
        input_root = self._job_dir(job_id) / "input"
        return sum(path.stat().st_size for path in input_root.rglob("*") if path.is_file())

    def reserve_file_path(self, job_id: str, category: str, filename: str) -> tuple[Path, Path]:
        name = safe_filename(filename)
        folder = self.category_dir(job_id, category)
        folder.mkdir(parents=True, exist_ok=True)
        candidate = folder / name
        counter = 2
        while candidate.exists():
            candidate = folder / f"{Path(name).stem} ({counter}){Path(name).suffix}"
            counter += 1
        temp = folder / f".{candidate.name}.{secrets.token_hex(4)}.uploading"
        return temp, candidate

    def refresh_files(self, job: dict) -> dict:
        files = {}
        job_id = job["job_id"]
        for category in CATEGORY_DIRS:
            folder = self.category_dir(job_id, category)
            files[category] = [
                {
                    "name": path.name,
                    "relative_path": path.relative_to(self._job_dir(job_id)).as_posix(),
                    "size_bytes": path.stat().st_size,
                }
                for path in sorted(folder.iterdir())
                if path.is_file() and not path.name.endswith(".uploading")
            ]
        job["files"] = files
        return self.save_job(job)

    def delete_file(self, job_id: str, category: str, filename: str) -> dict:
        with self._lock:
            job = self.load_job(job_id)
            self.ensure_mutable(job)
            name = safe_filename(filename)
            folder = self.category_dir(job_id, category)
            target = (folder / name).resolve()
            if folder not in target.parents:
                raise ValueError("file path escaped category")
            if not target.is_file():
                raise FileNotFoundError("file not found")
            target.unlink()
            return self.refresh_files(job)

    def build_manifest(self, job_id: str) -> Path:
        with self._lock:
            job = self.refresh_files(self.load_job(job_id))
            files = job["files"]
            if len(files["mapping"]) != 1:
                raise ValueError("exactly one mapping file is required")
            if files["erp"] and (files["business"] or files["advertising"]):
                raise ValueError("ERP files cannot be mixed with business/advertising files")
            if not files["erp"] and not files["business"] and not files["advertising"]:
                raise ValueError("provide ERP files or at least one business/advertising file")

            def paths(category: str) -> list[str]:
                return [item["relative_path"] for item in files[category]]

            options = job.get("options") or {}
            manifest = {
                "workspace": ".",
                "mapping_file": paths("mapping")[0],
                "erp_files": paths("erp"),
                "business_files": paths("business"),
                "ad_files": paths("advertising"),
                "inventory_files": paths("inventory"),
                "plan_files": paths("plan"),
                "include_history": False,
                "write_excel": bool(options.get("write_excel", True)),
                "write_html": bool(options.get("write_html", True)),
            }
            manifest_path = self._job_dir(job_id) / "job_manifest.json"
            _atomic_json_write(manifest_path, manifest)
            return manifest_path

    def update_status(self, job_id: str, status: str, *, result=None, error=None) -> dict:
        with self._lock:
            job = self.load_job(job_id)
            job["status"] = status
            job["result"] = result
            job["error"] = error
            return self.save_job(job)

    def artifact_paths(self, job_id: str) -> list[dict]:
        job_dir = self._job_dir(job_id)
        allowed = [job_dir / "output", job_dir / "data" / "manual_review"]
        artifacts = []
        for root in allowed:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    artifacts.append({
                        "relative_path": path.relative_to(job_dir).as_posix(),
                        "size_bytes": path.stat().st_size,
                    })
        result_path = job_dir / "job_result.json"
        if result_path.is_file():
            artifacts.append({
                "relative_path": "job_result.json",
                "size_bytes": result_path.stat().st_size,
            })
        return artifacts

    def resolve_artifact(self, job_id: str, relative_path: str) -> Path:
        job_dir = self._job_dir(job_id)
        relative = str(relative_path or "").replace("\\", "/")
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("invalid artifact path")
        target = (job_dir / relative).resolve()
        allowed_roots = [
            (job_dir / "output").resolve(),
            (job_dir / "data" / "manual_review").resolve(),
        ]
        allowed = relative == "job_result.json" or any(
            root == target or root in target.parents for root in allowed_roots
        )
        if not allowed:
            raise ValueError("artifact path is not allowed")
        if not target.is_file():
            raise FileNotFoundError("artifact not found")
        return target
