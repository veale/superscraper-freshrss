"""Integration tests against real websites.

These tests require network access.  Run with:
    pytest tests/test_integration.py -v --timeout=60 -m integration

Skip these in CI if no network is available by tagging with @pytest.mark.integration.
"""

from __future__ import annotations

import pytest

from app.discovery.cascade import run_discovery
from app.models.schemas import DiscoverRequest

pytestmark = pytest.mark.asyncio


# ── RSS autodiscovery ─────────────────────────────────────────────────────────


async def test_discover_theverge_has_rss():
    """The Verge publishes an RSS feed via <link rel=alternate>."""
    req = DiscoverRequest(url="https://www.theverge.com", timeout=30)
    resp = await run_discovery(req)
    assert len(resp.results.rss_feeds) >= 1, (
        f"Expected ≥1 RSS feed from The Verge, got {resp.results.rss_feeds}. "
        f"Errors: {resp.errors}"
    )
    feed_urls = [f.url.lower() for f in resp.results.rss_feeds]
    assert any("rss" in u or "feed" in u for u in feed_urls), (
        f"Feed URLs don't look like RSS: {feed_urls}"
    )


async def test_discover_python_blog_has_atom():
    """Python blog uses Atom feed."""
    req = DiscoverRequest(url="https://blog.python.org", timeout=30)
    resp = await run_discovery(req)
    assert len(resp.results.rss_feeds) >= 1, (
        f"Expected ≥1 feed from Python blog, got {resp.results.rss_feeds}. "
        f"Errors: {resp.errors}"
    )


async def test_discover_xkcd_has_rss():
    """XKCD has a well-known /rss.xml path."""
    req = DiscoverRequest(url="https://xkcd.com", timeout=30)
    resp = await run_discovery(req)
    assert len(resp.results.rss_feeds) >= 1, (
        f"Expected ≥1 RSS feed from xkcd, got {resp.results.rss_feeds}. "
        f"Errors: {resp.errors}"
    )
    feed_urls = [f.url for f in resp.results.rss_feeds]
    assert any("rss" in u.lower() for u in feed_urls), feed_urls


# ── WordPress REST API ────────────────────────────────────────────────────────


async def test_discover_wordpress_org_news():
    """WordPress.org news should expose RSS or wp-json REST API."""
    req = DiscoverRequest(url="https://wordpress.org/news/", timeout=30)
    resp = await run_discovery(req)
    has_rss = len(resp.results.rss_feeds) >= 1
    has_api = any("/wp-json" in ep.url for ep in resp.results.api_endpoints)
    assert has_rss or has_api, (
        f"Expected RSS or wp-json from wordpress.org/news. "
        f"Feeds: {resp.results.rss_feeds}. "
        f"APIs: {[ep.url for ep in resp.results.api_endpoints]}. "
        f"Errors: {resp.errors}"
    )


# ── HN — heuristic XPath ──────────────────────────────────────────────────────


async def test_discover_hn_rss_or_xpath():
    """HN has RSS at /rss; heuristic should also find <tr class='athing'>."""
    req = DiscoverRequest(url="https://news.ycombinator.com", timeout=30)
    resp = await run_discovery(req)
    has_rss = len(resp.results.rss_feeds) >= 1
    has_xpath = len(resp.results.xpath_candidates) >= 1
    assert has_rss or has_xpath, (
        f"Expected RSS or XPath candidates from HN. "
        f"Feeds: {resp.results.rss_feeds}. "
        f"XPath: {resp.results.xpath_candidates}. "
        f"Errors: {resp.errors}"
    )


# ── Response schema validation ────────────────────────────────────────────────


async def test_response_structure():
    """DiscoverResponse must conform to schema with all required fields."""
    req = DiscoverRequest(url="https://blog.python.org", timeout=30)
    resp = await run_discovery(req)

    # Required fields are present.
    assert resp.url
    assert resp.timestamp
    assert resp.results is not None
    assert isinstance(resp.errors, list)

    # Results sub-fields are lists.
    assert isinstance(resp.results.rss_feeds, list)
    assert isinstance(resp.results.api_endpoints, list)
    assert isinstance(resp.results.embedded_json, list)
    assert isinstance(resp.results.xpath_candidates, list)
    assert resp.results.page_meta is not None


async def test_invalid_url_returns_errors():
    """A URL that doesn't resolve should return errors, not raise."""
    req = DiscoverRequest(
        url="https://this-domain-does-not-exist-autofeed-test.example.com",
        timeout=10,
    )
    resp = await run_discovery(req)
    assert len(resp.errors) >= 1, "Expected at least one error for unreachable URL"
