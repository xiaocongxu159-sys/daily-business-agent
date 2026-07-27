# -*- coding: utf-8 -*-
"""Final local-dashboard validation and atomic write helper."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

REMOTE_RESOURCE = re.compile(r"(?:src|href)=[\"']https?://", re.IGNORECASE)


def patch_dashboard_html_file(path):
    target = Path(path).resolve()
    text = target.read_text(encoding="utf-8")
    if REMOTE_RESOURCE.search(text):
        raise ValueError("dashboard contains a remote script or stylesheet")
    if "每日经营看板" not in text:
        raise ValueError("dashboard title is missing")
    if "data-local-dashboard-checked" not in text:
        text = text.replace("<html ", '<html data-local-dashboard-checked="true" ', 1)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return target
