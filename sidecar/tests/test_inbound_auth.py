"""Regression tests for the inbound shared-secret guard."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _reload_app_with_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("AUTOFEED_INBOUND_TOKEN", "secret")
    monkeypatch.setenv("AUTOFEED_BRIDGES_DIR", str(tmp_path))
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import app.main as main_module
    importlib.reload(main_module)
    return TestClient(main_module.app)


_SAMPLE_PHP = """<?php
class ExampleSiteBridge extends BridgeAbstract {
    public function collectData() {
        $this->items[] = ['title' => 'x', 'uri' => 'https://example.com', 'content' => 'x', 'timestamp' => 'now'];
    }
}
"""


def test_health_open_even_with_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _reload_app_with_env(monkeypatch, tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200


def test_bridge_deploy_needs_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _reload_app_with_env(monkeypatch, tmp_path)

    resp = client.post(
        "/bridge/deploy",
        json={"bridge_name": "ExampleSiteBridge", "php_code": _SAMPLE_PHP},
    )
    assert resp.status_code == 401

    resp = client.post(
        "/bridge/deploy",
        headers={"Authorization": "Bearer secret"},
        json={"bridge_name": "ExampleSiteBridge", "php_code": _SAMPLE_PHP},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["deployed"]
