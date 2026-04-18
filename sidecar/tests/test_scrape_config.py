"""Offline tests for /scrape/config CRUD and the Atom feed endpoint."""

from __future__ import annotations

import sys, os

import json
import re

import pytest
import respx
import httpx
from fastapi.testclient import TestClient

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
    "cache_key": "",
    "timeout": 10,
    "max_pages": 1,
}

_FIXTURE_HTML = (
    "<html><body>"
    "<article><h2><a href='/1'>Post One</a></h2></article>"
    "<article><h2><a href='/2'>Post Two</a></h2></article>"
    "</body></html>"
)


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFEED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOFEED_CACHE_DIR", str(tmp_path / "cache"))

    import importlib
    import app.scraping.config_store as cs_mod
    monkeypatch.setattr(cs_mod, "_DATA_DIR", tmp_path)

    from app.main import app
    return TestClient(app)


def test_post_get_roundtrip(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    resp = client.post("/scrape/config", json=_BASE_REQ)
    assert resp.status_code == 200
    data = resp.json()
    assert "config_id" in data
    assert "feed_url" in data
    config_id = data["config_id"]

    # GET should return the saved config
    get_resp = client.get(f"/scrape/config/{config_id}")
    assert get_resp.status_code == 200
    cfg = get_resp.json()
    assert cfg["url"] == _BASE_REQ["url"]
    assert cfg["strategy"] == _BASE_REQ["strategy"]


def test_delete_removes_config(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    resp = client.post("/scrape/config", json=_BASE_REQ)
    config_id = resp.json()["config_id"]

    del_resp = client.delete(f"/scrape/config/{config_id}")
    assert del_resp.status_code == 204

    get_resp = client.get(f"/scrape/config/{config_id}")
    assert get_resp.status_code == 404


def test_get_missing_returns_404(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.get("/scrape/config/doesnotexist123")
    assert resp.status_code == 404


def test_config_id_is_url_safe(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)
    resp = client.post("/scrape/config", json=_BASE_REQ)
    config_id = resp.json()["config_id"]
    # token_urlsafe(12) must not contain +, /, or =
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", config_id), f"Unsafe config_id: {config_id}"


def test_cache_key_equals_config_id_and_no_tmp(tmp_path, monkeypatch):
    client = _make_client(tmp_path, monkeypatch)

    resp = client.post("/scrape/config", json=_BASE_REQ)
    assert resp.status_code == 200
    config_id = resp.json()["config_id"]

    cfg_resp = client.get(f"/scrape/config/{config_id}")
    assert cfg_resp.status_code == 200
    cfg = cfg_resp.json()
    assert cfg["cache_key"] == config_id

    config_dir = tmp_path / "scrape-configs"
    assert config_dir.exists()
    tmp_files = list(config_dir.glob("*.tmp"))
    assert tmp_files == []


@respx.mock
def test_atom_feed_returns_valid_xml(tmp_path, monkeypatch):
    """GET /scrape/feed?id=... should return parseable Atom XML."""
    from lxml import etree

    client = _make_client(tmp_path, monkeypatch)

    # Create config
    resp = client.post("/scrape/config", json={**_BASE_REQ, "adaptive": False})
    config_id = resp.json()["config_id"]

    # Mock the HTTP fetch that run_scrape will make
    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(
            200,
            text=_FIXTURE_HTML,
            headers={"content-type": "text/html"},
        )
    )

    feed_resp = client.get(f"/scrape/feed?id={config_id}")
    assert feed_resp.status_code == 200
    assert "xml" in feed_resp.headers["content-type"]

    # Must parse as valid XML
    root = etree.fromstring(feed_resp.content)
    assert root is not None

    # Should contain both titles
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    titles = [e.text for e in root.findall(".//atom:title", ns)]
    assert any("Post One" in (t or "") for t in titles)
    assert any("Post Two" in (t or "") for t in titles)
