"""Snapshot-style tests for prompt rendering — catch template drift."""
from __future__ import annotations

import sys
import os

import pytest


from app.llm.prompts import render_bridge_prompt, render_strategy_prompt
from app.models.schemas import (
    AnalyzeRequest,
    APIEndpoint,
    BridgeGenerateRequest,
    DiscoveryResults,
    EmbeddedJSON,
    GraphQLOperation,
    LLMConfig,
    PageMeta,
    RSSFeed,
    XPathCandidate,
)

_LLM = LLMConfig(endpoint="http://llm.test", model="test-model", timeout=30)

_FULL_RESULTS = DiscoveryResults(
    rss_feeds=[
        RSSFeed(url="https://example.com/feed.rss", title="Example RSS"),
        RSSFeed(url="https://example.com/atom.xml", title="Example Atom"),
    ],
    api_endpoints=[
        APIEndpoint(
            url="https://api.example.com/posts",
            feed_score=0.82,
            sample_keys=["id", "title", "url", "date"],
        )
    ],
    embedded_json=[
        EmbeddedJSON(
            source="script#__NEXT_DATA__",
            path="props.pageProps.articles",
            sample_keys=["slug", "title", "excerpt"],
        )
    ],
    graphql_operations=[
        GraphQLOperation(
            endpoint="https://api.example.com/graphql",
            operation_name="GetPosts",
            operation_type="query",
            query="query GetPosts { posts { title url date } }",
            response_path="posts",
            item_count=10,
            sample_keys=["title", "url", "date"],
            feed_score=0.78,
            detected_via="network_capture",
        )
    ],
    xpath_candidates=[
        XPathCandidate(
            item_selector="//article",
            confidence=0.75,
            item_count=12,
        )
    ],
    page_meta=PageMeta(
        page_title="Example Blog",
        frameworks_detected=["next.js"],
        anti_bot_detected=False,
    ),
    html_skeleton="<html><body><main><article>[text:10]</article></main></body></html>",
)


def _analyze_req(results=None, skeleton="") -> AnalyzeRequest:
    return AnalyzeRequest(
        url="https://example.com",
        results=results or _FULL_RESULTS,
        html_skeleton=skeleton,
        llm=_LLM,
    )


# ── Strategy prompt tests ─────────────────────────────────────────────────────

def test_strategy_system_prompt_is_nonempty():
    system, _ = render_strategy_prompt(_analyze_req())
    assert len(system) > 50
    assert "rss_bridge" in system.lower()


def test_strategy_user_prompt_contains_url():
    _, user = render_strategy_prompt(_analyze_req())
    assert "https://example.com" in user


def test_strategy_user_prompt_contains_page_title():
    _, user = render_strategy_prompt(_analyze_req())
    assert "Example Blog" in user


def test_strategy_user_prompt_contains_candidate_counts():
    _, user = render_strategy_prompt(_analyze_req())
    assert "rss_feeds     (2)" in user
    assert "api_endpoints (1)" in user
    assert "embedded_json (1)" in user
    assert "xpath         (1)" in user


def test_strategy_user_prompt_contains_rss_url():
    _, user = render_strategy_prompt(_analyze_req())
    assert "https://example.com/feed.rss" in user


def test_strategy_user_prompt_contains_api_score():
    _, user = render_strategy_prompt(_analyze_req())
    assert "score=0.82" in user


def test_strategy_user_prompt_contains_embedded_json_source():
    _, user = render_strategy_prompt(_analyze_req())
    assert "__NEXT_DATA__" in user


def test_strategy_user_prompt_contains_xpath_selector():
    _, user = render_strategy_prompt(_analyze_req())
    assert "//article" in user


def test_strategy_user_prompt_contains_framework():
    _, user = render_strategy_prompt(_analyze_req())
    assert "next.js" in user


def test_strategy_user_prompt_uses_request_skeleton_over_results():
    req = _analyze_req(skeleton="<CUSTOM_SKELETON/>")
    _, user = render_strategy_prompt(req)
    assert "<CUSTOM_SKELETON/>" in user


def test_strategy_user_prompt_falls_back_to_results_skeleton():
    req = _analyze_req(skeleton="")  # no override
    _, user = render_strategy_prompt(req)
    assert "<article>" in user  # from _FULL_RESULTS.html_skeleton


def test_strategy_user_prompt_no_candidates():
    req = _analyze_req(results=DiscoveryResults(), skeleton="")
    _, user = render_strategy_prompt(req)
    assert "rss_feeds     (0)" in user
    assert "none" in user


def test_strategy_skeleton_truncated_to_8000_chars():
    big_skeleton = "x" * 20_000
    req = _analyze_req(skeleton=big_skeleton)
    _, user = render_strategy_prompt(req)
    # The skeleton section should not contain more than 8000 x's
    assert "x" * 8001 not in user


# ── Bridge prompt tests ───────────────────────────────────────────────────────

def _bridge_req(hint="") -> BridgeGenerateRequest:
    return BridgeGenerateRequest(
        url="https://example.com",
        results=_FULL_RESULTS,
        html_skeleton="",
        llm=_LLM,
        hint=hint,
    )


def test_bridge_system_prompt_mentions_bridge_abstract():
    system, _ = render_bridge_prompt(_bridge_req())
    assert "BridgeAbstract" in system


def test_bridge_system_prompt_mentions_rules():
    system, _ = render_bridge_prompt(_bridge_req())
    assert "<?php" in system
    assert "CACHE_TIMEOUT" in system


def test_bridge_user_prompt_contains_url():
    _, user = render_bridge_prompt(_bridge_req())
    assert "https://example.com" in user


def test_bridge_user_prompt_contains_hint():
    _, user = render_bridge_prompt(_bridge_req(hint="Focus on the news section"))
    assert "Focus on the news section" in user


def test_bridge_user_prompt_no_hint_shows_none():
    _, user = render_bridge_prompt(_bridge_req(hint=""))
    assert "HINT: none" in user


def test_bridge_user_prompt_contains_candidate_counts():
    _, user = render_bridge_prompt(_bridge_req())
    assert "(2)" in user  # rss_feeds count
    assert "(1)" in user  # api/ej/xp counts


def test_bridge_user_prompt_includes_one_shot():
    _, user = render_bridge_prompt(_bridge_req())
    assert "Reference example of a minimal valid bridge" in user


def test_bridge_user_prompt_targets_url_after_example():
    _, user = render_bridge_prompt(_bridge_req())
    lines = user.strip().splitlines()
    assert lines[0].startswith("Reference example of a minimal valid bridge")
    assert any(line.startswith("TARGET URL:") for line in lines)


# ── GraphQL prompt tests ──────────────────────────────────────────────────────

def test_strategy_user_prompt_contains_graphql_operation():
    _, user = render_strategy_prompt(_analyze_req())
    assert "https://api.example.com/graphql" in user


def test_strategy_user_prompt_contains_graphql_count():
    _, user = render_strategy_prompt(_analyze_req())
    assert "graphql       (1)" in user


def test_strategy_user_prompt_no_graphql():
    req = _analyze_req(results=DiscoveryResults(), skeleton="")
    _, user = render_strategy_prompt(req)
    assert "graphql       (0)" in user
    assert "none" in user
