# -*- coding: utf-8 -*-
"""Packaged Windows entry point for Daily Business Agent."""
from __future__ import annotations

import multiprocessing
import os
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


def _runtime_verification_workspace(argv: list[str]) -> Path | None:
    flag = "--verify-local-analysis-runtime"
    if flag not in argv:
        return None
    index = argv.index(flag)
    if index + 1 >= len(argv):
        raise RuntimeError(f"{flag} requires a workspace path")
    return Path(argv[index + 1])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _ensure_standard_streams()
    try:
        from agent.frozen_runtime import (
            install_frozen_runtime_patches,
            verify_local_analysis_runtime,
        )

        verification_workspace = _runtime_verification_workspace(sys.argv[1:])
        if verification_workspace is not None:
            verify_local_analysis_runtime(verification_workspace)
        else:
            install_frozen_runtime_patches()
            from agent.run_agent import main

            main()
    except BaseException:
        _write_crash_log()
        raise
