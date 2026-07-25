# -*- coding: utf-8 -*-
"""Persistent local token management. Tokens never leave the local data root."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

STATE_FILENAME = "agent_state.json"


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_or_create_agent_token(data_root: Path) -> tuple[str, Path]:
    root = Path(data_root).resolve()
    state_path = root / STATE_FILENAME
    if state_path.is_file():
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        token = str(payload.get("agent_token", "")).strip()
        if len(token) < 32:
            raise ValueError("stored agent token is invalid")
        return token, state_path

    token = secrets.token_urlsafe(32)
    _atomic_json_write(
        state_path,
        {
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent_token": token,
        },
    )
    return token, state_path
