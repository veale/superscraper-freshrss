"""Pytest configuration for AutoFeed sidecar tests."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TEST_DATA_ROOT = Path(tempfile.gettempdir()) / "autofeed-test-data"
_TEST_DATA_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("AUTOFEED_DATA_DIR", str(_TEST_DATA_ROOT))
os.environ.setdefault(
    "AUTOFEED_DISCOVERY_CACHE_DIR", str(_TEST_DATA_ROOT / "discover-cache")
)
os.environ.setdefault("AUTOFEED_BRIDGES_DIR", str(_TEST_DATA_ROOT / "bridges"))
