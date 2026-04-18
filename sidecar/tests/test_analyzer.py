"""Unit tests for recommend_strategy — LLM mocked via respx."""
from __future__ import annotations

import json
import sys
import os

import httpx
import pytest
import respx


from app.llm.analyzer import recommend_strategy, _sanity_check_php
from app.models.schemas import (
    AnalyzeRequest,
    APIEndpoint,
    DiscoveryResults,
    EmbeddedJSON,
    LLMConfig,
    PageMeta,
    RSSFeed,
    XPathCandidate,
)

_ENDPOINT = "http://llm.internal"
_COMPLETIONS_URL = f"{_ENDPOINT}/chat/completions"

_LLM_CFG = LLMConfig(endpoint=_ENDPOINT, api_key="sk-x", model="gpt-4o-mini", timeout=30)


def _llm_resp(content: dict, tokens: int = 50) -> httpx.Response:
    body = {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"total_tokens": tokens},
    }
    return httpx.Response(200, json=body)


def _req(results: DiscoveryResults | None = None) -> AnalyzeRequest:
    return AnalyzeRequest(
        url="https://example.com",
        results=results or DiscoveryResults(),
        html_skeleton="<html><body><p>[text:5]</p></body></html>",
        llm=_LLM_CFG,
    )


@pytest.mark.asyncio
async def test_happy_path_rss_strategy(respx_mock):
    llm_json = {
        "strategy": "rss",
        "confidence": 0.95,
        "selected_candidate_ref": "rss[0]",
        "field_overrides": {},
        "reasoning": "Official RSS feed found.",
        "caveats": [],
    }
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=_llm_resp(llm_json, tokens=120))

    results = DiscoveryResults(
        rss_feeds=[RSSFeed(url="https://example.com/feed.rss", title="Example Feed")]
    )
    resp = await recommend_strategy(_req(results))

    assert resp.recommendation is not None
    assert resp.recommendation.strategy.value == "rss"
    assert resp.recommendation.confidence == 0.95
    assert resp.recommendation.selected_candidate_ref == "rss[0]"
    assert resp.recommendation.reasoning == "Official RSS feed found."
    assert resp.tokens_used == 120
    assert resp.errors == []


@pytest.mark.asyncio
async def test_happy_path_xpath_strategy(respx_mock):
    llm_json = {
        "strategy": "xpath",
        "confidence": 0.72,
        "selected_candidate_ref": "xpath[0]",
        "field_overrides": {"itemTitle": ".//h2/text()", "itemUri": ".//a/@href"},
        "reasoning": "Stable XPath pattern detected.",
        "caveats": ["May break on layout changes"],
    }
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=_llm_resp(llm_json))

    results = DiscoveryResults(
        xpath_candidates=[
            XPathCandidate(item_selector="//article", confidence=0.72, item_count=10)
        ]
    )
    resp = await recommend_strategy(_req(results))

    assert resp.recommendation.strategy.value == "xpath"
    assert resp.recommendation.field_overrides == {
        "itemTitle": ".//h2/text()",
        "itemUri": ".//a/@href",
    }
    assert len(resp.recommendation.caveats) == 1


@pytest.mark.asyncio
async def test_unknown_strategy_returns_error(respx_mock):
    llm_json = {"strategy": "telepathy", "confidence": 0.99}
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=_llm_resp(llm_json))

    resp = await recommend_strategy(_req())

    assert resp.recommendation is None
    assert any("telepathy" in e for e in resp.errors)
    assert resp.llm_raw == llm_json


@pytest.mark.asyncio
async def test_missing_optional_fields_default_safely(respx_mock):
    # Only strategy and confidence — everything else absent
    llm_json = {"strategy": "embedded_json", "confidence": 0.6}
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=_llm_resp(llm_json))

    resp = await recommend_strategy(_req())

    rec = resp.recommendation
    assert rec is not None
    assert rec.strategy.value == "embedded_json"
    assert rec.reasoning == ""
    assert rec.selected_candidate_ref is None
    assert rec.field_overrides == {}
    assert rec.caveats == []


@pytest.mark.asyncio
async def test_confidence_clamped_to_0_1(respx_mock):
    llm_json = {"strategy": "rss", "confidence": 1.5}  # out of range
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=_llm_resp(llm_json))

    resp = await recommend_strategy(_req())
    assert resp.recommendation.confidence == 1.0


@pytest.mark.asyncio
async def test_llm_timeout_returns_error(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

    resp = await recommend_strategy(_req())

    assert resp.recommendation is None
    assert any("timeout" in e.lower() for e in resp.errors)


@pytest.mark.asyncio
async def test_llm_auth_error_returns_error(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    resp = await recommend_strategy(_req())

    assert resp.recommendation is None
    assert any("auth" in e.lower() for e in resp.errors)


@pytest.mark.asyncio
async def test_llm_malformed_response_returns_error(respx_mock):
    body = {
        "choices": [{"message": {"content": "not json at all, just text"}}],
        "usage": {"total_tokens": 5},
    }
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=httpx.Response(200, json=body))

    resp = await recommend_strategy(_req())

    assert resp.recommendation is None
    assert resp.errors


@pytest.mark.asyncio
async def test_rss_bridge_strategy_allowed(respx_mock):
    llm_json = {
        "strategy": "rss_bridge",
        "confidence": 0.5,
        "selected_candidate_ref": None,
        "field_overrides": {},
        "reasoning": "No other clean source available.",
        "caveats": ["Requires RSS-Bridge deployment"],
    }
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=_llm_resp(llm_json))

    resp = await recommend_strategy(_req())

    assert resp.recommendation.strategy.value == "rss_bridge"
    assert resp.recommendation.selected_candidate_ref is None


@pytest.mark.asyncio
async def test_url_preserved_in_response(respx_mock):
    llm_json = {"strategy": "rss", "confidence": 0.9}
    respx_mock.post(_COMPLETIONS_URL).mock(return_value=_llm_resp(llm_json))

    req = AnalyzeRequest(
        url="https://specific-site.example.org/blog",
        results=DiscoveryResults(),
        llm=_LLM_CFG,
    )
    resp = await recommend_strategy(req)
    assert resp.url == "https://specific-site.example.org/blog"


def test_sanity_check_missing_constants_warns():
    code = """<?php
    class ExampleBridgeBridge extends BridgeAbstract {
        public function collectData() {}
    }
    """
    warnings, _soft = _sanity_check_php("ExampleBridgeBridge", code)
    assert "Missing const NAME constant" in warnings
    assert "Missing const URI constant" in warnings
    assert "Missing const DESCRIPTION constant" in warnings
    assert "const MAINTAINER should equal 'AutoFeed-LLM'" in warnings
    assert "const PARAMETERS is required even if empty" in warnings


def test_sanity_check_detects_dangerous_calls():
    code = """<?php
    class SafeBridge extends BridgeAbstract {
        const NAME = 'Safe';
        const URI = 'https://example.com';
        const DESCRIPTION = 'desc';
        const MAINTAINER = 'AutoFeed-LLM';
        const PARAMETERS = [];

        public function collectData() {
            shell_exec('rm -rf /tmp');
            file_get_contents('/secret');
            fopen('/etc/passwd', 'r');
            curl_init('https://example.com');
            base64_decode('c2VjcmV0');
        }
    }
    """
    warnings, soft_warnings = _sanity_check_php("SafeBridge", code)
    assert any("shell_exec" in w for w in warnings), warnings
    assert any("file_get_contents" in w for w in soft_warnings), soft_warnings
    assert any("fopen(" in w for w in soft_warnings), soft_warnings
    assert any("curl_" in w for w in soft_warnings), soft_warnings
    assert any("base64_decode" in w for w in soft_warnings), soft_warnings
