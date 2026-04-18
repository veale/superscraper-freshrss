"""Step 1 — RSS / Atom / JSON Feed autodiscovery."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin

import httpx

from app.models.schemas import RSSFeed

# Default probe timeout
_PROBE_TIMEOUT = 5.0

# Common feed paths to probe when <link> tags aren't present.
COMMON_FEED_PATHS = [
    "/feed",
    "/feed/",
    "/rss",
    "/rss/",
    "/atom.xml",
    "/feed.xml",
    "/index.xml",
    "/rss.xml",
    "/feed/rss",
    "/feed/atom",
    "/.rss",
    "/blog/feed",
    "/blog/rss",
    "/wp-json/wp/v2/posts",
]

FEED_CONTENT_TYPES = {
    "application/rss+xml",
    "application/atom+xml",
    "application/feed+json",
    "application/json",
    "text/xml",
    "application/xml",
}

def _normalize_content_type(content_type: Optional[str]) -> str:
    return (content_type or "").lower().split(";")[0].strip()


def _feed_type_from_content_type(content_type: str) -> Optional[str]:
    if not content_type:
        return None
    if "json" in content_type:
        return "json_feed"
    if "atom" in content_type:
        return "atom"
    if "rss" in content_type or content_type in {"application/xml", "text/xml"}:
        return "rss"
    return None


async def _probe_feed(
    client: httpx.AsyncClient,
    url: str,
    timeout: float = _PROBE_TIMEOUT,
) -> tuple[bool, int | None, str, Optional[str]]:
    """Return (is_alive, http_status, parse_error, detected_type)."""

    status: int | None = None
    parse_error = ""
    detected_type: Optional[str] = None

    try:
        resp = await client.head(url, timeout=timeout, follow_redirects=True)
        status = resp.status_code
        content_type = _normalize_content_type(resp.headers.get("content-type"))
        detected_type = _feed_type_from_content_type(content_type)
        if 200 <= status < 300 and detected_type:
            return True, status, "", detected_type
        parse_error = f"HEAD {status} {content_type or 'unknown'}"
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        parse_error = f"HEAD error: {exc}"

    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        status = resp.status_code
        content_type = _normalize_content_type(resp.headers.get("content-type"))
        detected_type = detected_type or _feed_type_from_content_type(content_type)
        if 200 <= status < 300 and detected_type:
            return True, status, "", detected_type
        parse_error = f"GET {status} {content_type or 'unknown'}"
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        parse_error = f"GET error: {exc}"
        return False, status, parse_error, detected_type

    return False, status, parse_error, detected_type


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

    parser = _LinkParser(url)
    try:
        parser.feed(html)
    except Exception:
        pass

    async def _add_feed(candidate_url: str, title: str, type_hint: Optional[str]) -> None:
        if candidate_url in seen_urls:
            return
        is_alive, status, parse_error, detected_type = await _probe_feed(
            client, candidate_url, timeout=_PROBE_TIMEOUT
        )
        feed_type = type_hint or detected_type or "rss"
        feeds.append(
            RSSFeed(
                url=candidate_url,
                title=title,
                feed_type=feed_type,
                is_alive=is_alive,
                http_status=status,
                parse_error=parse_error,
            )
        )
        seen_urls.add(candidate_url)

    for candidate in parser.feeds:
        await _add_feed(candidate.url, candidate.title or "", candidate.feed_type)

    for path in COMMON_FEED_PATHS:
        candidate = urljoin(url, path)
        if candidate in seen_urls:
            continue
        await _add_feed(candidate, "", None)

    return feeds


async def _probe_single_feed(url: str) -> dict:
    """Probe a single feed URL and return its liveness status.
    
    Returns a dict with keys: is_alive, http_status, parse_error.
    """
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
        is_alive, http_status, parse_error, _ = await _probe_feed(client, url)
        return {
            "is_alive": is_alive,
            "http_status": http_status,
            "parse_error": parse_error,
        }
