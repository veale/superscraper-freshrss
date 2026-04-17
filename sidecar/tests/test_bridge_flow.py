"""Integration tests for /bridge/generate → /bridge/deploy flow."""
from __future__ import annotations

import json
import os
import shutil
import sys

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app

client = TestClient(app)

_LLM_ENDPOINT = "http://llm.test"
_COMPLETIONS_URL = f"{_LLM_ENDPOINT}/chat/completions"

_LLM_CONFIG = {
    "endpoint": _LLM_ENDPOINT,
    "api_key": "sk-test",
    "model": "gpt-4o-mini",
    "timeout": 30,
}

_SAMPLE_PHP = """\
<?php
class ExampleSiteBridge extends BridgeAbstract {
    const NAME = 'ExampleSite Bridge';
    const URI = 'https://example.com';
    const DESCRIPTION = 'Scrapes example.com';
    const CACHE_TIMEOUT = 3600;

    public function collectData() {
        $html = getSimpleHTMLDOM($this->getURI());
        foreach ($html->find('article') as $el) {
            $this->items[] = [
                'title'   => $el->find('h2', 0)->plaintext,
                'uri'     => $el->find('a', 0)->href,
                'content' => $el->innertext,
            ];
        }
    }
}"""

_DISCOVER_RESULTS = {
    "rss_feeds": [],
    "api_endpoints": [],
    "embedded_json": [],
    "xpath_candidates": [],
    "page_meta": {"page_title": "Example Site"},
    "html_skeleton": "<html><body><article><h2>Post</h2></article></body></html>",
}


def _llm_resp(content: dict) -> httpx.Response:
    body = {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"total_tokens": 300},
    }
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_bridge_generate_returns_php(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=_llm_resp(
            {"bridge_name": "ExampleSiteBridge", "php_code": _SAMPLE_PHP}
        )
    )

    resp = client.post(
        "/bridge/generate",
        json={
            "url": "https://example.com",
            "results": _DISCOVER_RESULTS,
            "html_skeleton": _DISCOVER_RESULTS["html_skeleton"],
            "llm": _LLM_CONFIG,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["bridge_name"] == "ExampleSiteBridge"
    assert data["filename"] == "ExampleSiteBridge.php"
    assert "<?php" in data["php_code"]
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_bridge_generate_sanity_warnings_on_bad_php(respx_mock):
    bad_php = "not php at all"
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=_llm_resp({"bridge_name": "TestBridge", "php_code": bad_php})
    )

    resp = client.post(
        "/bridge/generate",
        json={
            "url": "https://example.com",
            "results": _DISCOVER_RESULTS,
            "llm": _LLM_CONFIG,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["sanity_warnings"]
    assert any("<?php" in w for w in data["sanity_warnings"])


@pytest.mark.asyncio
async def test_bridge_generate_llm_missing_fields(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=_llm_resp({"oops": "no bridge here"})
    )

    resp = client.post(
        "/bridge/generate",
        json={
            "url": "https://example.com",
            "results": _DISCOVER_RESULTS,
            "llm": _LLM_CONFIG,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["errors"]


def test_bridge_deploy_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFEED_BRIDGES_DIR", str(tmp_path))

    import importlib
    import app.main as main_module
    importlib.reload(main_module)

    test_client = TestClient(main_module.app)
    resp = test_client.post(
        "/bridge/deploy",
        json={"bridge_name": "ExampleSiteBridge", "php_code": _SAMPLE_PHP},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["deployed"]
    assert data["errors"] == []
    assert (tmp_path / "ExampleSiteBridge.php").exists()


def test_bridge_deploy_invalid_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFEED_BRIDGES_DIR", str(tmp_path))

    import importlib
    import app.main as main_module
    importlib.reload(main_module)

    test_client = TestClient(main_module.app)
    resp = test_client.post(
        "/bridge/deploy",
        json={"bridge_name": "invalid-name", "php_code": _SAMPLE_PHP},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert not data["deployed"]
    assert data["errors"]


@pytest.mark.skipif(shutil.which("php") is None, reason="php not on PATH")
@pytest.mark.asyncio
async def test_bridge_generate_and_php_lint(tmp_path, respx_mock):
    """End-to-end: generate → deploy to tmp → php -l passes."""
    import subprocess

    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=_llm_resp(
            {"bridge_name": "ExampleSiteBridge", "php_code": _SAMPLE_PHP}
        )
    )

    resp = client.post(
        "/bridge/generate",
        json={
            "url": "https://example.com",
            "results": _DISCOVER_RESULTS,
            "llm": _LLM_CONFIG,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    php_code = data["php_code"]
    bridge_name = data["bridge_name"]

    from app.bridge.deploy import deploy_bridge
    result = deploy_bridge(bridge_name, php_code, bridges_dir=str(tmp_path))
    assert result.deployed

    lint = subprocess.run(
        ["php", "-l", result.path], capture_output=True, text=True
    )
    assert lint.returncode == 0, f"php -l failed:\n{lint.stdout}\n{lint.stderr}"
