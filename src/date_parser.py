# -*- coding: utf-8 -*-
"""Date extraction and normalization helpers for local report filenames."""
import re
from datetime import datetime
from pathlib import Path


def extract_date_from_filename(filename):
    """Extract a date from a filename and return YYYY-MM-DD when possible."""
    name = Path(filename).stem
    date_formats = [
        "%Y-%m-%d", "%Y_%m_%d", "%Y.%m.%d",
        "%m-%d-%Y", "%m_%d_%Y", "%m.%d.%Y",
        "%d-%m-%Y", "%d_%m_%Y", "%d.%m.%Y",
        "%Y%m%d",
    ]
    for format_string in date_formats:
        try:
            return datetime.strptime(name, format_string).strftime("%Y-%m-%d")
        except ValueError:
            pass

    match = re.search(r"(\d{1,2})[-_.](\d{1,2})[-_.](\d{2})$", name)
    if match:
        day, month, year = map(int, match.groups())
        try:
            return datetime(year + 2000, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    match = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", name)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass

    match = re.search(r"(\d{2})[-_.](\d{2})[-_.](\d{4})", name)
    if match:
        month, day, year = map(int, match.groups())
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def validate_date(date_str):
    if not date_str:
        return False
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def normalize_date(date_str):
    if not date_str:
        return None
    if validate_date(date_str):
        return date_str
    for format_string in [
        "%Y/%m/%d", "%Y.%m.%d", "%Y_%m_%d",
        "%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y",
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%Y%m%d", "%m/%d/%y", "%m-%d-%y",
    ]:
        try:
            return datetime.strptime(str(date_str), format_string).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return date_str
