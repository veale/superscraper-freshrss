"""Offline tests for the adaptive selector cache (Scrapling 0.4.6 SQLite backend)."""

from __future__ import annotations

import sys, os

from pathlib import Path
import pytest
import respx
import httpx

pytestmark = pytest.mark.asyncio

_FIXTURE = Path(__file__).parent / "fixtures" / "articles.html"
_HTML = _FIXTURE.read_text()


@respx.mock
async def test_first_run_with_adaptive_writes_db(tmp_path, monkeypatch):
    """First run with a valid cache_key should write a .db fingerprint file."""
    import app.scraping.scrape as scrape_mod
    monkeypatch.setattr(scrape_mod, "_CACHE_DIR", tmp_path)

    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )

    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/blog",
        strategy=FeedStrategy.XPATH,
        selectors=ScrapeSelectors(item="//article[@class='post']"),
        adaptive=True,
        cache_key="test-key-abc",
    )
    result = await run_scrape(req)
    assert result.item_count == 3
    assert (tmp_path / "test-key-abc.db").exists()


@respx.mock
async def test_second_run_reports_cache_hit(tmp_path, monkeypatch):
    """Second run with the same cache_key should report cache_hit=True."""
    import app.scraping.scrape as scrape_mod
    monkeypatch.setattr(scrape_mod, "_CACHE_DIR", tmp_path)

    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )

    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/blog",
        strategy=FeedStrategy.XPATH,
        selectors=ScrapeSelectors(item="//article[@class='post']"),
        adaptive=True,
        cache_key="test-key-hit",
    )
    # Run 1 — no cache yet
    r1 = await run_scrape(req)
    assert r1.cache_hit is False

    # Run 2 — db file now exists
    r2 = await run_scrape(req)
    assert r2.cache_hit is True


@respx.mock
async def test_unsafe_cache_key_does_not_crash(tmp_path, monkeypatch):
    """An unsafe cache_key must be silently ignored (no file written, no crash)."""
    import app.scraping.scrape as scrape_mod
    monkeypatch.setattr(scrape_mod, "_CACHE_DIR", tmp_path)

    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(200, text=_HTML, headers={"content-type": "text/html"})
    )

    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/blog",
        strategy=FeedStrategy.XPATH,
        selectors=ScrapeSelectors(item="//article[@class='post']"),
        adaptive=True,
        cache_key="../evil",
    )
    result = await run_scrape(req)
    assert result.cache_hit is False
    # No file should escape the tmp_path dir
    assert list(tmp_path.iterdir()) == []
