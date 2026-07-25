# -*- coding: utf-8 -*-
"""Loopback-only settings for the Windows local Agent."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def default_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        # Keep the historical path so upgrades retain DPAPI credentials and cache.
        return Path(local_app_data) / "CTJFyrdian" / "DailyBusinessAgent"
    return Path.home() / ".ctjfyrdian" / "daily_business_agent"


@dataclass(frozen=True)
class AgentSettings:
    data_root: Path = field(default_factory=default_data_root)
    host: str = "127.0.0.1"
    port: int = 8766
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8766",
        "http://localhost:8766",
    )

    def validated(self) -> "AgentSettings":
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError("local agent must bind to a loopback host")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return self
