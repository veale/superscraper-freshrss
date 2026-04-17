"""Phase 2 — network interception tests.

These tests require network access and a Playwright browser.
Run with: pytest tests/test_network_intercept.py -v --timeout=60 -m integration
"""

from __future__ import annotations

import time

import pytest

from app.discovery.network_intercept import intercept_network

pytestmark = pytest.mark.asyncio


async def test_intercept_returns_html():
    """Browser-rendered HTML should be non-empty and contain page content."""
    html, _ = await intercept_network("https://news.ycombinator.com", timeout=20)
    assert len(html) > 1000, f"Expected non-trivial HTML, got {len(html)} chars"
    assert "<html" in html.lower()


async def test_intercept_hn_has_content():
    """HN is server-rendered; the browser HTML should contain story rows."""
    html, _ = await intercept_network("https://news.ycombinator.com", timeout=20)
    assert "athing" in html or "storylink" in html or "titleline" in html, (
        "Expected HN story elements in rendered HTML"
    )


async def test_intercept_spa_captures_json():
    """HN Algolia search API returns JSON — should be captured."""
    html, responses = await intercept_network(
        "https://hn.algolia.com/", timeout=25
    )
    assert len(html) > 100, "Expected non-empty HTML"
    if responses:
        urls = [r["url"] for r in responses]
        # Should have captured some API call
        assert any("algolia" in u or "/api/" in u for u in urls), (
            f"Expected algolia API calls, got: {urls[:5]}"
        )


async def test_intercept_filters_tracking_urls():
    """Google Analytics and Facebook tracking requests must be excluded."""
    _, responses = await intercept_network(
        "https://www.theverge.com", timeout=25
    )
    urls = [r["url"] for r in responses]
    assert not any("google-analytics" in u for u in urls), (
        f"GA should be filtered, found: {[u for u in urls if 'google-analytics' in u]}"
    )
    assert not any("facebook.com" in u for u in urls), (
        f"FB should be filtered, found: {[u for u in urls if 'facebook.com' in u]}"
    )


async def test_intercept_respects_timeout():
    """Interception should not hang past the timeout."""
    start = time.monotonic()
    # httpbin delay/60 should be interrupted by our 8s timeout
    try:
        await intercept_network("https://httpbin.org/delay/60", timeout=8)
    except Exception:
        pass
    elapsed = time.monotonic() - start
    assert elapsed < 20, f"Timeout not respected: took {elapsed:.1f}s"


async def test_intercept_all_responses_are_json():
    """Every captured response must have been parseable as JSON."""
    _, responses = await intercept_network(
        "https://news.ycombinator.com", timeout=20
    )
    for r in responses:
        assert isinstance(r["body"], (dict, list)), (
            f"Expected parsed JSON body, got {type(r['body'])} for {r['url']}"
        )
        assert "url" in r
        assert "method" in r
        assert "status" in r
