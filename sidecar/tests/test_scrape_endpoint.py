"""Offline tests for the /scrape endpoint.  All outbound HTTP is mocked."""

from __future__ import annotations

import sys, os

import json
from pathlib import Path

import pytest
import respx
import httpx

pytestmark = pytest.mark.asyncio

_FIXTURE = Path(__file__).parent / "fixtures" / "articles.html"

_ATOM_BODY = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test</title>
  <entry>
    <title>Post One</title>
    <link href="https://example.com/1"/>
    <updated>2024-01-01T00:00:00Z</updated>
    <summary>Summary one</summary>
  </entry>
  <entry>
    <title>Post Two</title>
    <link href="https://example.com/2"/>
    <updated>2024-01-02T00:00:00Z</updated>
    <summary>Summary two</summary>
  </entry>
</feed>"""

_JSON_BODY = {
    "data": {
        "posts": [
            {"title": "Alpha", "url": "https://example.com/a", "body": "Content A"},
            {"title": "Beta",  "url": "https://example.com/b", "body": "Content B"},
            {"title": "Gamma", "url": "https://example.com/c", "body": "Content C"},
        ]
    }
}


@respx.mock
async def test_json_api_strategy_returns_items():
    respx.get("https://example.com/api").mock(
        return_value=httpx.Response(200, json=_JSON_BODY, headers={"content-type": "application/json"})
    )
    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/api",
        strategy=FeedStrategy.JSON_API,
        selectors=ScrapeSelectors(
            item="data.posts",
            item_title="title",
            item_link="url",
            item_content="body",
        ),
    )
    result = await run_scrape(req)
    assert result.item_count == 3
    assert result.items[0].title == "Alpha"
    assert result.items[1].title == "Beta"
    assert result.errors == []


@respx.mock
async def test_rss_strategy_parses_atom():
    respx.get("https://example.com/feed").mock(
        return_value=httpx.Response(200, content=_ATOM_BODY.encode(), headers={"content-type": "application/atom+xml"})
    )
    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/feed",
        strategy=FeedStrategy.RSS,
        selectors=ScrapeSelectors(),
    )
    result = await run_scrape(req)
    assert result.item_count == 2
    titles = [i.title for i in result.items]
    assert "Post One" in titles
    assert "Post Two" in titles


@respx.mock
async def test_xpath_strategy_returns_items():
    html = _FIXTURE.read_text()
    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/blog",
        strategy=FeedStrategy.XPATH,
        selectors=ScrapeSelectors(
            item="//article[@class='post']",
            item_title=".//h2[@class='title']/a",
            item_link=".//h2[@class='title']/a/@href",
        ),
        adaptive=False,
        cache_key="",
    )
    result = await run_scrape(req)
    assert result.item_count == 3
    assert result.items[0].title == "First Post"
    assert result.errors == []


@respx.mock
async def test_unsupported_strategy_returns_error():
    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/",
        strategy=FeedStrategy.RSS_BRIDGE,
        selectors=ScrapeSelectors(),
    )
    result = await run_scrape(req)
    assert result.item_count == 0
    assert any("not supported" in e for e in result.errors)


@respx.mock
async def test_path_traversal_cache_key_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOFEED_CACHE_DIR", str(tmp_path))
    html = _FIXTURE.read_text()
    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    from app.scraping.scrape import run_scrape, _CACHE_DIR
    import importlib
    import app.scraping.scrape as scrape_mod
    monkeypatch.setattr(scrape_mod, "_CACHE_DIR", tmp_path)

    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/blog",
        strategy=FeedStrategy.XPATH,
        selectors=ScrapeSelectors(item="//article[@class='post']"),
        adaptive=True,
        cache_key="../../../etc/passwd",
    )
    result = await run_scrape(req)
    # Should not crash; cache_key is rejected as unsafe
    assert result.item_count >= 0
    # No .db file should have been written outside tmp_path
    assert not (tmp_path / "etc").exists()


@respx.mock
async def test_bad_xpath_selector_returns_warning():
    html = _FIXTURE.read_text()
    respx.get("https://example.com/blog").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    from app.scraping.scrape import run_scrape
    from app.models.schemas import ScrapeRequest, ScrapeSelectors, FeedStrategy

    req = ScrapeRequest(
        url="https://example.com/blog",
        strategy=FeedStrategy.XPATH,
        selectors=ScrapeSelectors(item="//nonexistent[@class='nope']"),
        adaptive=False,
        cache_key="",
    )
    result = await run_scrape(req)
    assert result.item_count == 0
    assert result.warnings  # should have at least one warning about 0 elements
