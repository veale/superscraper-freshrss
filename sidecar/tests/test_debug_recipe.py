"""Tests for Round 3 LLM recipe-debug primitives."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.llm.prompts import render_debug_recipe_prompt
from app.llm.analyzer import debug_recipe
from app.models.schemas import LLMConfig
from app.scraping.config_store import save_config, load_config, update_config


_ENDPOINT = "http://localhost:11434"
_COMPLETIONS_URL = f"{_ENDPOINT}/chat/completions"
_LLM = LLMConfig(endpoint=_ENDPOINT, api_key="sk-test", model="llama3", timeout=10)


def _llm_response(content: dict | str, tokens: int = 50) -> dict:
    if isinstance(content, dict):
        content = json.dumps(content)
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": tokens},
    }


# ── Prompt rendering ────────────────────────────────────────────────────────


def test_render_debug_recipe_xpath_prompt_mentions_selectors():
    system, user = render_debug_recipe_prompt(
        strategy="xpath",
        url="https://example.com",
        recipe={"item_selector": "//div[@class='old']"},
        item_count=0,
        errors=["no items"],
        warnings=[],
        sample_items=[],
        source_sample="<html><body><article><h2>Post</h2></article></body></html>",
    )
    assert "failing feed recipe" in system.lower()
    assert "xpath" in user.lower()
    assert "//div[@class='old']" in user
    assert "items_returned: 0" in user


def test_render_debug_recipe_truncates_large_source_sample():
    big = "x" * 50000
    _, user = render_debug_recipe_prompt(
        strategy="json_api", url="https://e.com", recipe={}, item_count=0,
        errors=[], warnings=[], sample_items=[], source_sample=big,
    )
    # Rough clamp — we set 12_000 chars in render_debug_recipe_prompt.
    assert len(user) < 20_000
    assert "xxxx" in user


def test_render_debug_recipe_includes_sample_items():
    _, user = render_debug_recipe_prompt(
        strategy="json_api", url="https://e.com", recipe={"item_path": "data"},
        item_count=2, errors=[], warnings=[],
        sample_items=[{"title": "ok", "link": "/a", "content": "c" * 500}],
        source_sample="{}",
    )
    # content cap (200 chars via _truncate_values) should apply
    assert "ok" in user
    assert "ccccc" in user
    assert "c" * 500 not in user


# ── Analyzer ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_debug_recipe_happy_path(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response({
            "diff": {
                "item_title": "headline",
                "item_link": "canonicalUrl",
            },
            "reasoning": "Sample shows `headline` is the display title.",
            "caveats": ["link may need origin prepended"],
        }))
    )

    out = await debug_recipe(
        strategy="json_api",
        url="https://api.example.com/search",
        recipe={"item_path": "results", "item_title": "title", "item_link": "url"},
        item_count=0,
        errors=["title field missing"],
        warnings=[],
        sample_items=[],
        source_sample='{"results":[{"headline":"X","canonicalUrl":"/x"}]}',
        llm=_LLM,
    )
    assert out["error"] is None
    assert out["diff"] == {"item_title": "headline", "item_link": "canonicalUrl"}
    assert "headline" in out["reasoning"]
    assert out["caveats"] == ["link may need origin prepended"]


@pytest.mark.asyncio
async def test_debug_recipe_drops_non_scalar_diff_fields(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response({
            "diff": {
                "item_path": "data.items",
                "field_mapping": {"not a real field": True},  # should be dropped
                "request_headers": {"X-Test": "1"},  # dict preserved
            },
            "reasoning": "…",
        }))
    )
    out = await debug_recipe(
        strategy="json_api", url="https://e.com", recipe={},
        item_count=0, errors=[], warnings=[], sample_items=[],
        source_sample="{}", llm=_LLM,
    )
    assert "field_mapping" not in out["diff"]
    assert out["diff"]["item_path"] == "data.items"
    assert out["diff"]["request_headers"] == {"X-Test": "1"}


@pytest.mark.asyncio
async def test_debug_recipe_llm_auth_error(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(401, json={"error": "bad key"})
    )
    out = await debug_recipe(
        strategy="xpath", url="https://e.com", recipe={},
        item_count=0, errors=[], warnings=[], sample_items=[],
        source_sample="<html/>", llm=_LLM,
    )
    assert out["error"] and "auth" in out["error"].lower()
    assert out["diff"] == {}


# ── Config store update ─────────────────────────────────────────────────────


def test_update_config_writes_changes():
    cid = save_config("scrape", {"url": "https://a.com", "strategy": "xpath"})
    assert update_config("scrape", cid, {"url": "https://b.com", "strategy": "xpath"})
    cfg = load_config("scrape", cid)
    assert cfg["url"] == "https://b.com"
    # cache_key should always be set to the id
    assert cfg["cache_key"] == cid


def test_update_config_rejects_missing_id():
    assert not update_config("scrape", "does-not-exist", {"foo": "bar"})


def test_update_config_rejects_unsafe_id():
    assert not update_config("scrape", "../etc/passwd", {"foo": "bar"})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
