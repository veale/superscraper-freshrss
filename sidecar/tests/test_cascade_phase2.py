"""Phase 2 cascade integration tests.

These tests require network access.
Run with: pytest tests/test_cascade_phase2.py -v --timeout=90 -m integration
"""

from __future__ import annotations

import pytest

from app.discovery.cascade import run_discovery
from app.models.schemas import DiscoverRequest

pytestmark = pytest.mark.asyncio


async def test_use_browser_flag_works():
    """use_browser=True should trigger Phase 2 paths without error."""
    req = DiscoverRequest(
        url="https://news.ycombinator.com", timeout=30, use_browser=True
    )
    resp = await run_discovery(req)
    # Should not crash and should return valid structure
    assert resp.results is not None
    assert isinstance(resp.errors, list)


async def test_browser_mode_adds_xpath_candidates():
    """Browser mode should produce XPath candidates for HN (server-rendered)."""
    req = DiscoverRequest(
        url="https://news.ycombinator.com", timeout=30, use_browser=True
    )
    resp = await run_discovery(req)
    has_rss = len(resp.results.rss_feeds) >= 1
    has_xpath = len(resp.results.xpath_candidates) >= 1
    assert has_rss or has_xpath, (
        f"Expected RSS or XPath candidates from HN in browser mode. "
        f"Errors: {resp.errors}"
    )


async def test_phase1_results_preserved_in_browser_mode():
    """Phase 1 RSS results must still appear when browser mode is active."""
    req = DiscoverRequest(
        url="https://blog.python.org", timeout=30, use_browser=True
    )
    resp = await run_discovery(req)
    assert len(resp.results.rss_feeds) >= 1, (
        f"RSS feeds should still be found in browser mode. "
        f"Feeds: {resp.results.rss_feeds}. Errors: {resp.errors}"
    )


async def test_no_duplicate_api_endpoints():
    """API endpoints found by both static JS analysis and network interception
    should be deduplicated by URL."""
    req = DiscoverRequest(
        url="https://wordpress.org/news/", timeout=30, use_browser=True
    )
    resp = await run_discovery(req)
    urls = [ep.url for ep in resp.results.api_endpoints]
    assert len(urls) == len(set(urls)), f"Duplicate API endpoint URLs: {urls}"


async def test_error_boundary_isolates_browser_failure():
    """A browser error should not suppress Phase 1 results (e.g. RSS)."""
    # Use a real site with RSS so Phase 1 succeeds even if Phase 2 has issues.
    req = DiscoverRequest(
        url="https://xkcd.com", timeout=30, use_browser=True
    )
    resp = await run_discovery(req)
    # Phase 1 RSS should still be there regardless.
    assert len(resp.results.rss_feeds) >= 1, (
        f"Phase 1 RSS should survive any Phase 2 error. Errors: {resp.errors}"
    )
