# -*- coding: utf-8 -*-
"""Packaged Windows entry point for Daily Business Agent."""
from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import traceback
from pathlib import Path


def _ensure_standard_streams() -> None:
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _write_crash_log() -> None:
    target = os.environ.get("DAILY_BUSINESS_AGENT_CRASH_LOG", "").strip()
    if not target:
        return
    try:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def _flag_workspace(argv: list[str], flag: str) -> Path | None:
    if flag not in argv:
        return None
    index = argv.index(flag)
    if index + 1 >= len(argv):
        raise RuntimeError(f"{flag} requires a workspace path")
    return Path(argv[index + 1]).expanduser().resolve()


def _verification_progress(workspace: Path) -> str:
    path = workspace / "verification-progress.txt"
    try:
        return path.read_text(encoding="utf-8").strip() or "no-progress"
    except OSError:
        return "no-progress"


def _run_verification_parent(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "--verify-local-analysis-runtime-child",
        str(workspace),
    ]
    try:
        completed = subprocess.run(command, timeout=180, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "frozen local analysis exceeded 180 seconds; "
            f"last_progress={_verification_progress(workspace)}"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "frozen local analysis child failed; "
            f"exit_code={completed.returncode}; "
            f"last_progress={_verification_progress(workspace)}"
        )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _ensure_standard_streams()
    argv = sys.argv[1:]
    parent_flag = "--verify-local-analysis-runtime"
    child_flag = "--verify-local-analysis-runtime-child"
    verification_mode = parent_flag in argv or child_flag in argv
    try:
        from agent.frozen_runtime import (
            install_frozen_runtime_patches,
            verify_local_analysis_runtime,
        )

        parent_workspace = _flag_workspace(argv, parent_flag)
        child_workspace = _flag_workspace(argv, child_flag)
        if parent_workspace is not None:
            _run_verification_parent(parent_workspace)
            os._exit(0)
        if child_workspace is not None:
            verify_local_analysis_runtime(child_workspace)
            os._exit(0)

        install_frozen_runtime_patches()
        from agent.run_agent import main

        main()
    except BaseException:
        _write_crash_log()
        if verification_mode:
            os._exit(1)
        raise
