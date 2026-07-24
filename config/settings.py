# -*- coding: utf-8 -*-
"""Public-safe global defaults.

Store and marketplace identity must come from user-provided data or explicit
local configuration. The public repository never ships a real store identity
or a historical production output filename.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
SRC_DIR = BASE_DIR / "src"
CONFIG_DIR = BASE_DIR / "config"

DEFAULT_SHOP_ID = ""
DEFAULT_SHOP_NAME = ""
DEFAULT_MARKETPLACE = ""
INVENTORY_MIGRATION_BASELINE_FILE = ""
