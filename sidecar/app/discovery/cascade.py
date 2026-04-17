"""Discovery cascade — orchestrates all discovery steps."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.discovery.embedded_json import detect_embedded_json
from app.discovery.network_intercept import intercept_network
from app.discovery.rss_autodiscovery import discover_rss
from app.discovery.scoring import score_feed_likeness
from app.discovery.scrapling_selectors import generate_selectors_with_scrapling
from app.discovery.selector_generation import generate_xpath_candidates
from app.discovery.static_js_analysis import extract_api_urls
from app.utils.skeleton import build_skeleton
from app.models.schemas import (
    APIEndpoint,
    DiscoverRequest,
    DiscoverResponse,
    DiscoveryResults,
    PageMeta,
    XPathCandidate,
)

_FRAMEWORK_MARKERS: list[tuple[str, str]] = [
    ("next.js", "__NEXT_DATA__"),
    ("nuxt.js", "__NUXT__"),
    ("gatsby", "___gatsby"),
    ("react", "data-reactroot"),
    ("angular", "ng-version"),
    ("vue.js", "data-v-"),
    ("svelte", "__svelte"),
    ("wordpress", "wp-content"),
]

_ANTI_BOT_MARKERS = [
    "cf-browser-verification",
    "challenge-platform",
    "turnstile",
    "just a moment",
    "checking your browser",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def run_discovery(req: DiscoverRequest) -> DiscoverResponse:
    """Execute the full discovery cascade for *req.url*."""

    errors: list[str] = []
    url = req.url.strip()
    html = ""
    page_meta = PageMeta()

    async with httpx.AsyncClient(
        headers=_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(req.timeout, connect=10),
    ) as client:

        # ── Fetch the page ─────────────────────────────────────────────────
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except httpx.HTTPStatusError as exc:
            errors.append(f"HTTP {exc.response.status_code} fetching {url}")
        except httpx.TimeoutException:
            errors.append(f"Timeout fetching {url}")
        except httpx.HTTPError as exc:
            errors.append(f"Error fetching {url}: {exc}")

        if not html:
            return DiscoverResponse(
                url=url,
                timestamp=datetime.now(timezone.utc),
                results=DiscoveryResults(page_meta=page_meta),
                errors=errors,
            )

        # ── Page metadata ──────────────────────────────────────────────────
        page_meta = _extract_page_meta(html, url)

        # ── Step 1: RSS / Atom autodiscovery ───────────────────────────────
        try:
            rss_feeds = await discover_rss(url, html, client, timeout=8)
        except Exception as exc:
            rss_feeds = []
            errors.append(f"RSS autodiscovery error: {exc}")

        # ── Step 2: Embedded JSON detection ────────────────────────────────
        try:
            embedded_json = detect_embedded_json(html)
        except Exception as exc:
            embedded_json = []
            errors.append(f"Embedded JSON detection error: {exc}")

        # ── Step 3: Static JS API URL extraction + probing ─────────────────
        try:
            api_endpoints = await extract_api_urls(
                url, html, client, timeout=8, max_js_files=5
            )
        except Exception as exc:
            api_endpoints = []
            errors.append(f"Static JS analysis error: {exc}")

        # ── Step 5 (Phase 1): Heuristic XPath candidate generation ─────────
        try:
            xpath_candidates = generate_xpath_candidates(html)
        except Exception as exc:
            xpath_candidates = []
            errors.append(f"XPath generation error: {exc}")

    # ── Phase 2 steps (browser required) ──────────────────────────────────
    # Run when:
    #   • caller forces browser mode, OR
    #   • no RSS found AND (page is JS-rendered OR no API endpoints found), OR
    #   • anti-bot was detected (need stealth browser)
    needs_browser = req.use_browser or (
        not rss_feeds
        and (
            page_meta.has_javascript_content
            or page_meta.anti_bot_detected
            or not api_endpoints
        )
    )

    browser_html = ""
    if needs_browser:
        # ── Step 4: Network interception ───────────────────────────────────
        try:
            browser_html, network_responses = await intercept_network(
                url, timeout=min(req.timeout, 30)
            )
            for resp_data in network_responses:
                sc = score_feed_likeness(resp_data["body"])
                if sc >= 0.15:
                    items = _first_items(resp_data["body"])
                    sample_keys = (
                        sorted({k for item in items[:5] for k in item.keys()})[:15]
                        if items
                        else []
                    )
                    api_endpoints.append(
                        APIEndpoint(
                            url=resp_data["url"],
                            method=resp_data["method"],
                            content_type=resp_data["content_type"],
                            item_count=len(items),
                            sample_keys=sample_keys,
                            feed_score=sc,
                        )
                    )
            # Re-sort by score.
            api_endpoints.sort(key=lambda e: e.feed_score, reverse=True)
            # Deduplicate by URL.
            seen_urls: set[str] = set()
            deduped: list[APIEndpoint] = []
            for ep in api_endpoints:
                if ep.url not in seen_urls:
                    seen_urls.add(ep.url)
                    deduped.append(ep)
            api_endpoints = deduped
        except Exception as exc:
            errors.append(f"Network interception error: {exc}")

        # ── Step 5 (Phase 2): Scrapling selector generation ────────────────
        # Use browser-rendered HTML when available for full DOM analysis.
        analysis_html = browser_html or html
        try:
            scrapling_candidates = generate_selectors_with_scrapling(analysis_html)
            xpath_candidates = _merge_xpath_candidates(
                scrapling_candidates, xpath_candidates
            )
        except Exception as exc:
            errors.append(f"Scrapling selector generation error: {exc}")

    html_skeleton = build_skeleton(browser_html or html) if (browser_html or html) else ""

    return DiscoverResponse(
        url=url,
        timestamp=datetime.now(timezone.utc),
        results=DiscoveryResults(
            rss_feeds=rss_feeds,
            api_endpoints=api_endpoints,
            embedded_json=embedded_json,
            xpath_candidates=xpath_candidates,
            page_meta=page_meta,
            html_skeleton=html_skeleton,
        ),
        errors=errors,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _extract_page_meta(html: str, url: str) -> PageMeta:
    html_lower = html.lower()

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        title = m.group(1).strip()

    canonical = url
    m = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        canonical = m.group(1)

    frameworks: list[str] = []
    for name, marker in _FRAMEWORK_MARKERS:
        if marker.lower() in html_lower:
            frameworks.append(name)

    anti_bot = any(marker in html_lower for marker in _ANTI_BOT_MARKERS)

    body_text_len = len(re.sub(r"<[^>]+>", "", html))
    script_count = html_lower.count("<script")
    has_js_content = script_count > 5 and body_text_len < 2000

    return PageMeta(
        has_javascript_content=has_js_content,
        frameworks_detected=frameworks,
        anti_bot_detected=anti_bot,
        page_title=title[:200],
        canonical_url=canonical,
    )


def _first_items(data: Any) -> list[dict]:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _merge_xpath_candidates(
    scrapling: list[XPathCandidate],
    heuristic: list[XPathCandidate],
) -> list[XPathCandidate]:
    """Merge Scrapling candidates (higher quality) with heuristic ones.

    Scrapling candidates take priority; heuristic ones fill gaps up to 5 total.
    """
    seen: set[str] = {c.item_selector for c in scrapling}
    merged = list(scrapling)
    for c in heuristic:
        if c.item_selector not in seen and len(merged) < 5:
            merged.append(c)
            seen.add(c.item_selector)
    merged.sort(key=lambda c: c.confidence, reverse=True)
    return merged[:5]
