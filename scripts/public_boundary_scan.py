from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PATH_PARTS = {"deploy", "lingxing-egress", ".env", "storage", "backups"}
FORBIDDEN_TEXT = [
    r"124\.221\.26\.163",
    r"dba-egress\.ctjfyrdian\.com",
    r"/home/ubuntu/",
    r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY",
    r"amazon-keyword-rank-monitor-dev",
]
ALLOW_SUFFIXES = {".py", ".html", ".md", ".txt", ".lock", ".ps1", ".iss", ".yml", ".yaml"}


def main() -> int:
    failures: list[str] = []
    scanner_path = Path(__file__).resolve()
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.resolve() == scanner_path:
            continue
        relative = path.relative_to(ROOT)
        if any(part in FORBIDDEN_PATH_PARTS for part in relative.parts):
            failures.append(f"forbidden path: {relative}")
        if path.suffix.lower() not in ALLOW_SUFFIXES and path.name not in {"VERSION", ".gitignore", "LICENSE", "NOTICE"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for pattern in FORBIDDEN_TEXT:
            if re.search(pattern, text, flags=re.IGNORECASE):
                failures.append(f"forbidden text in {relative}: {pattern}")
    if failures:
        print("PUBLIC BOUNDARY SCAN FAILED", file=sys.stderr)
        print("\n".join(sorted(set(failures))), file=sys.stderr)
        return 1
    print("PASS: public boundary scan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
