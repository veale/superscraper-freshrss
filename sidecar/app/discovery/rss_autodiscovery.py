"""Step 1 — RSS / Atom / JSON Feed autodiscovery."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin

import httpx

from app.models.schemas import RSSFeed

# Common feed paths to probe when <link> tags aren't present.
COMMON_FEED_PATHS = [
    "/feed", "/feed/", "/rss", "/rss/", "/atom.xml",
    "/feed.xml", "/index.xml", "/rss.xml", "/feed/rss",
    "/feed/atom", "/.rss", "/blog/feed", "/blog/rss",
    "/wp-json/wp/v2/posts",  # WordPress REST (JSON)
]

FEED_CONTENT_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/json",
    "text/xml",
    "application/xml",
}


class _LinkParser(HTMLParser):
    """Minimal HTML parser that extracts <link rel="alternate"> feed URLs."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.feeds: list[RSSFeed] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "link":
            return
        attr = dict(attrs)
        rel = (attr.get("rel") or "").lower()
        link_type = (attr.get("type") or "").lower()
        href = attr.get("href")

        if not href:
            return

        if rel == "alternate" and link_type in FEED_CONTENT_TYPES:
            absolute = urljoin(self.base_url, href)
            title = attr.get("title") or ""
            ft = "atom" if "atom" in link_type else "rss"
            if "json" in link_type:
                ft = "json_feed"
            self.feeds.append(RSSFeed(url=absolute, title=title, feed_type=ft))


async def discover_rss(
    url: str,
    html: str,
    client: httpx.AsyncClient,
    timeout: float = 10.0,
) -> list[RSSFeed]:
    """Return feed URLs found via ``<link>`` tags and common path probing."""

    feeds: list[RSSFeed] = []
    seen_urls: set[str] = set()

    # ── 1. Parse <link rel="alternate"> from the HTML ────────────────────
    parser = _LinkParser(url)
    try:
        parser.feed(html)
    except Exception:
        pass

    for f in parser.feeds:
        if f.url not in seen_urls:
            feeds.append(f)
            seen_urls.add(f.url)

    # ── 2. Probe common feed paths ───────────────────────────────────────
    for path in COMMON_FEED_PATHS:
        candidate = urljoin(url, path)
        if candidate in seen_urls:
            continue
        try:
            resp = await client.head(candidate, timeout=timeout, follow_redirects=True)
            ct = (resp.headers.get("content-type") or "").lower().split(";")[0].strip()

            if resp.status_code == 200 and ct in FEED_CONTENT_TYPES:
                ft = "rss"
                if "atom" in ct:
                    ft = "atom"
                elif "json" in ct:
                    ft = "json_feed"
                feeds.append(RSSFeed(url=candidate, title="", feed_type=ft))
                seen_urls.add(candidate)
        except (httpx.HTTPError, httpx.TimeoutException):
            continue

    return feeds
