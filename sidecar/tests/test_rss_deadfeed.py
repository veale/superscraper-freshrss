"""Regression test for dead RSS feed handling (Tier 1.1).

This test verifies that:
1. When an advertised RSS feed returns 404, it's marked as is_alive=False
2. When a dead RSS exists, needs_browser is True (Phase 2 runs)
3. XPath candidates are still generated even when a dead RSS exists
"""

from __future__ import annotations

import sys
import os

import pytest
import respx
import httpx
from fastapi.testclient import TestClient


from app.main import app
from app.discovery.cascade import run_discovery
from app.models.schemas import DiscoverRequest, ServiceConfig

client = TestClient(app)

# HTML with article elements for XPath generation
_HTML_WITH_ARTICLES = """\
<!DOCTYPE html>
<html>
<head><title>Test Site</title></head>
<body>
    <article class="post">
        <h2><a href="/post-1">Post One</a></h2>
        <time>2024-01-01</time>
    </article>
    <article class="post">
        <h2><a href="/post-2">Post Two</a></h2>
        <time>2024-01-02</time>
    </article>
    <article class="post">
        <h2><a href="/post-3">Post Three</a></h2>
        <time>2024-01-03</time>
    </article>
</body>
</html>"""

# HTML with a link to a dead RSS feed
_HTML_WITH_DEAD_RSS = """\
<!DOCTYPE html>
<html>
<head>
    <title>Test Site</title>
    <link rel="alternate" type="application/rss+xml" href="https://example.com/feed.xml" />
</head>
<body>
    <article class="post">
        <h2><a href="/post-1">Post One</a></h2>
    </article>
</body>
</html>"""


@pytest.mark.asyncio
@respx.mock
async def test_rss_feed_marked_dead_on_404():
    """RSS feed returning 404 should be marked as is_alive=False."""
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, text=_HTML_WITH_DEAD_RSS,
                                    headers={"content-type": "text/html"})
    )
    respx.head("https://example.com/feed.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/feed.xml").mock(return_value=httpx.Response(404))
    respx.route(url__regex=r"https://example\.com/.*").mock(
        return_value=httpx.Response(404, text="not found")
    )

    req = DiscoverRequest(url="https://example.com/", timeout=10, use_browser=False)
    resp = await run_discovery(req)

    rss_feeds = resp.results.rss_feeds
    assert len(rss_feeds) > 0, "Expected at least one RSS feed"
    feed = rss_feeds[0]
    assert feed.url == "https://example.com/feed.xml"
    assert feed.is_alive is False, "RSS feed should be marked as dead"
    assert feed.http_status == 404, "HTTP status should be 404"


@pytest.mark.asyncio
@respx.mock
async def test_dead_rss_triggers_phase2():
    """When RSS is dead, Phase 2 (browser) should run."""
    from unittest.mock import patch

    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, text=_HTML_WITH_DEAD_RSS,
                                    headers={"content-type": "text/html"})
    )
    respx.head("https://example.com/feed.xml").mock(return_value=httpx.Response(404))
    respx.get("https://example.com/feed.xml").mock(return_value=httpx.Response(404))
    respx.route(url__regex=r"https://example\.com/.*").mock(
        return_value=httpx.Response(404, text="not found")
    )

    async def mock_fetch(url, services, **kwargs):
        return _HTML_WITH_ARTICLES, []

    with patch("app.discovery.cascade.fetch_with_capture", mock_fetch):
        req = DiscoverRequest(url="https://example.com/", timeout=10, use_browser=False)
        resp = await run_discovery(req)

    assert resp.results.phase2_used is True, "Phase 2 should run when RSS is dead"
    assert len(resp.results.xpath_candidates) > 0, "XPath candidates should be generated"


@pytest.mark.asyncio
@respx.mock
async def test_live_rss_skips_phase2():
    """When RSS is live, Phase 2 should be skipped."""
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, text=_HTML_WITH_DEAD_RSS,
                                    headers={"content-type": "text/html"})
    )
    respx.head("https://example.com/feed.xml").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/rss+xml"})
    )
    respx.get("https://example.com/feed.xml").mock(
        return_value=httpx.Response(
            200,
            text='<?xml version="1.0"?><rss><channel><item><title>T</title></item></channel></rss>',
            headers={"content-type": "application/rss+xml"},
        )
    )
    respx.route(url__regex=r"https://example\.com/.*").mock(
        return_value=httpx.Response(404, text="not found")
    )

    req = DiscoverRequest(url="https://example.com/", timeout=10, use_browser=False)
    resp = await run_discovery(req)

    rss_feeds = resp.results.rss_feeds
    assert len(rss_feeds) > 0, "Expected at least one RSS feed"
    feed = rss_feeds[0]
    assert feed.is_alive is True, "RSS feed should be marked as live"


class TestDeadRSSFeedHandling:
    """Test handling of dead RSS feeds in discovery (sync/HTTP endpoint tests)."""

    @respx.mock
    def test_force_skip_rss_runs_phase2(self):
        """force_skip_rss should force Phase 2 even with live RSS."""
        # Mock a live RSS feed
        respx.get("https://example.com").mock(
            return_value=httpx.Response(
                200,
                text=_HTML_WITH_DEAD_RSS,
                headers={"content-type": "text/html"},
            )
        )

        respx.head("https://example.com/feed.xml").mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "application/rss+xml"},
            )
        )

        from unittest.mock import patch

        async def mock_fetch(url, services, timeout=30):
            return _HTML_WITH_ARTICLES, []

        with patch("app.services.fetch.fetch_with_capture", mock_fetch):
            resp = client.post(
                "/discover",
                json={
                    "url": "https://example.com",
                    "timeout": 30,
                    "use_browser": False,
                    "force_skip_rss": True,  # Force skip RSS
                    "services": {},
                },
            )

        assert resp.status_code == 200
        data = resp.json()

        # Phase 2 should run because force_skip_rss is True
        assert data["results"]["phase2_used"] is True, (
            "Phase 2 should run when force_skip_rss=True"
        )
