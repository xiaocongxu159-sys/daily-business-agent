from __future__ import annotations

from pathlib import Path


def get_version() -> str:
    candidates = [
        Path(__file__).resolve().parents[1] / "VERSION",
        Path.cwd() / "VERSION",
    ]
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return "0.6.1"


VERSION = get_version()
