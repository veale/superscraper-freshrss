"""Regression test for scrape config cache_key injection (Tier 0.3).

This test verifies that:
1. POST /scrape/config creates a config with cache_key == config_id
2. No .tmp files are left behind after config creation
3. The config can be retrieved and contains the correct cache_key
"""

from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient



def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFEED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOFEED_CACHE_DIR", str(tmp_path / "cache"))

    import app.scraping.config_store as cs_mod
    monkeypatch.setattr(cs_mod, "_DATA_DIR", tmp_path, raising=False)

    from app.main import app as _app
    return TestClient(_app)


_BASE_REQ = {
    "url": "https://example.com/blog",
    "strategy": "xpath",
    "selectors": {
        "item": "//article",
        "item_title": ".//h2/a",
        "item_link": ".//a/@href",
        "item_content": "",
        "item_timestamp": "",
        "item_thumbnail": "",
        "item_author": "",
    },
    "adaptive": False,
    "cache_key": "",  # Will be set by the server
    "timeout": 10,
    "max_pages": 1,
}


def test_scrape_config_cache_key_equals_config_id(tmp_path, monkeypatch):
    """Test that cache_key is set to config_id in a single write (no race)."""
    client = _make_client(tmp_path, monkeypatch)

    # Create a config
    resp = client.post("/scrape/config", json=_BASE_REQ)
    assert resp.status_code == 200

    data = resp.json()
    config_id = data["config_id"]
    feed_url = data["feed_url"]

    # Verify config_id is present
    assert config_id, "config_id should not be empty"

    # Retrieve the config
    get_resp = client.get(f"/scrape/config/{config_id}")
    assert get_resp.status_code == 200

    cfg = get_resp.json()

    # The key assertion: cache_key should equal config_id
    assert cfg.get("cache_key") == config_id, (
        f"cache_key ({cfg.get('cache_key')}) should equal config_id ({config_id})"
    )


def test_no_tmp_files_left_behind(tmp_path, monkeypatch):
    """Test that no .tmp files are left in the data directory."""
    client = _make_client(tmp_path, monkeypatch)

    # Create a config
    resp = client.post("/scrape/config", json=_BASE_REQ)
    assert resp.status_code == 200

    # Check for any .tmp files
    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert tmp_files == [], f"Found leftover .tmp files: {tmp_files}"


def test_multiple_configs_have_unique_cache_keys(tmp_path, monkeypatch):
    """Test that multiple configs get unique cache_keys."""
    client = _make_client(tmp_path, monkeypatch)

    # Create multiple configs
    config_ids = []
    cache_keys = []

    for i in range(3):
        req = {**_BASE_REQ, "url": f"https://example.com/blog{i}"}
        resp = client.post("/scrape/config", json=req)
        assert resp.status_code == 200

        data = resp.json()
        config_ids.append(data["config_id"])

        # Get the config and check cache_key
        get_resp = client.get(f"/scrape/config/{data['config_id']}")
        cfg = get_resp.json()
        cache_keys.append(cfg.get("cache_key"))

    # All config_ids should be unique
    assert len(set(config_ids)) == 3, "config_ids should be unique"

    # All cache_keys should equal their respective config_ids
    for config_id, cache_key in zip(config_ids, cache_keys):
        assert cache_key == config_id, (
            f"cache_key ({cache_key}) should equal config_id ({config_id})"
        )


def test_cache_key_not_overwritten_on_subsequent_reads(tmp_path, monkeypatch):
    """Test that cache_key remains stable across multiple reads."""
    client = _make_client(tmp_path, monkeypatch)

    # Create a config
    resp = client.post("/scrape/config", json=_BASE_REQ)
    assert resp.status_code == 200

    config_id = resp.json()["config_id"]

    # Read the config multiple times
    for _ in range(3):
        get_resp = client.get(f"/scrape/config/{config_id}")
        assert get_resp.status_code == 200
        cfg = get_resp.json()
        assert cfg.get("cache_key") == config_id