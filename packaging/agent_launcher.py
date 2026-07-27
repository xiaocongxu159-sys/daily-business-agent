# -*- coding: utf-8 -*-
"""Packaged Windows entry point for Daily Business Agent."""
from __future__ import annotations

import multiprocessing
import os
import sys
import traceback
from pathlib import Path

from agent.run_agent import main


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


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _ensure_standard_streams()
    try:
        main()
    except BaseException:
        _write_crash_log()
        raise
