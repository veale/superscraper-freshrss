"""Regression test for bridge name contract (Tier 0.1).

This test verifies that:
1. LLM returns bridge_name WITH the Bridge suffix (e.g., "FooBridge")
2. deploy_bridge accepts it (matches ^[A-Z][A-Za-z0-9]*Bridge$)
3. _sanity_check_php validates the class name correctly (expects "class FooBridge")
4. No sanity warnings are returned for valid PHP
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient


from app.main import app
from app.bridge.deploy import deploy_bridge
from app.llm.analyzer import _sanity_check_php

client = TestClient(app)

_LLM_ENDPOINT = "http://llm.test"
_COMPLETIONS_URL = f"{_LLM_ENDPOINT}/chat/completions"

_LLM_CONFIG = {
    "endpoint": _LLM_ENDPOINT,
    "api_key": "sk-test",
    "model": "gpt-4o-mini",
    "timeout": 30,
}

# Valid PHP with Bridge suffix in class name
_VALID_PHP_WITH_SUFFIX = """\
<?php
class FooBridge extends BridgeAbstract {
    const NAME = 'Foo';
    const URI = 'https://foo.example';
    const DESCRIPTION = 'Test bridge';
    const MAINTAINER = 'AutoFeed-LLM';
    const PARAMETERS = [];

    public function collectData() {
        $this->items[] = ['title' => 'x', 'uri' => 'https://example.com'];
    }
}"""

# Valid PHP WITHOUT Bridge suffix (should fail sanity check with current code)
_VALID_PHP_WITHOUT_SUFFIX = """\
<?php
class Foo extends BridgeAbstract {
    const NAME = 'Foo';
    const URI = 'https://foo.example';
    const DESCRIPTION = 'Test bridge';
    const MAINTAINER = 'AutoFeed-LLM';
    const PARAMETERS = [];

    public function collectData() {
        $this->items[] = ['title' => 'x', 'uri' => 'https://example.com'];
    }
}"""


def _llm_resp(content: dict) -> httpx.Response:
    body = {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"total_tokens": 300},
    }
    return httpx.Response(200, json=body)


class TestBridgeNameContract:
    """Test the bridge name contract across all components."""

    def test_deploy_accepts_bridge_suffix(self, tmp_path):
        """deploy_bridge should accept names matching ^[A-Z][A-Za-z0-9]*Bridge$"""
        bridges_dir = tmp_path / "bridges"
        bridges_dir.mkdir()

        result = deploy_bridge(
            name="FooBridge",
            code=_VALID_PHP_WITH_SUFFIX,
            bridges_dir=str(bridges_dir),
        )

        assert result.deployed, f"Deploy failed: {result.errors}"
        assert (bridges_dir / "FooBridge.php").exists()

    def test_deploy_rejects_missing_suffix(self, tmp_path):
        """deploy_bridge should reject names without Bridge suffix"""
        bridges_dir = tmp_path / "bridges"
        bridges_dir.mkdir()

        result = deploy_bridge(
            name="Foo",
            code=_VALID_PHP_WITHOUT_SUFFIX,
            bridges_dir=str(bridges_dir),
        )

        assert not result.deployed
        assert "Invalid bridge name" in result.errors[0]

    def test_sanity_check_passes_with_suffix(self):
        """_sanity_check_php should pass for valid PHP with Bridge suffix"""
        warnings, soft_warnings = _sanity_check_php("FooBridge", _VALID_PHP_WITH_SUFFIX)

        # Should have no warnings for valid PHP
        assert warnings == [], f"Unexpected warnings: {warnings}"
        assert soft_warnings == [], f"Unexpected soft_warnings: {soft_warnings}"

    def test_sanity_check_fails_without_suffix(self):
        """After Tier 0.1: bridge_name must match the class name exactly.

        If the LLM returns bridge_name='FooBridge' but writes class Foo,
        the sanity check should flag the mismatch.
        """
        warnings, _soft = _sanity_check_php("FooBridge", _VALID_PHP_WITHOUT_SUFFIX)
        assert any("Expected class 'FooBridge' not found" in w for w in warnings), (
            f"Expected class-mismatch warning. Got: {warnings}"
        )


@pytest.mark.asyncio
async def test_bridge_generate_to_deploy_flow(tmp_path, respx_mock, monkeypatch):
    """Full flow: /bridge/generate -> /bridge/deploy with Bridge suffix."""
    monkeypatch.setenv("AUTOFEED_BRIDGES_DIR", str(tmp_path))

    # Mock LLM to return name WITH Bridge suffix
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=_llm_resp({
            "bridge_name": "FooBridge",
            "php_code": _VALID_PHP_WITH_SUFFIX
        })
    )

    # Generate the bridge
    resp = client.post(
        "/bridge/generate",
        json={
            "url": "https://foo.example",
            "results": {
                "rss_feeds": [],
                "api_endpoints": [],
                "embedded_json": [],
                "xpath_candidates": [],
                "page_meta": {"page_title": "Foo"},
                "html_skeleton": "<html><body></body></html>",
            },
            "html_skeleton": "<html><body></body></html>",
            "llm": _LLM_CONFIG,
        },
    )

    assert resp.status_code == 200
    data = resp.json()

    # Verify the response includes the Bridge suffix
    assert data["bridge_name"] == "FooBridge"
    assert data["filename"] == "FooBridge.php"

    # Verify no sanity warnings for valid PHP
    assert data["sanity_warnings"] == [], f"Expected no warnings, got: {data['sanity_warnings']}"

    # Now deploy it
    deploy_resp = client.post(
        "/bridge/deploy",
        json={
            "bridge_name": data["bridge_name"],
            "php_code": data["php_code"],
        },
    )

    assert deploy_resp.status_code == 200
    deploy_data = deploy_resp.json()
    assert deploy_data["deployed"]

    # Verify the file exists with correct content
    bridge_file = tmp_path / "FooBridge.php"
    assert bridge_file.exists()
    content = bridge_file.read_text()
    assert "class FooBridge" in content
    assert "extends BridgeAbstract" in content