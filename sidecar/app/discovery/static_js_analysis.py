"""Step 3 — Scan HTML and linked JS for API endpoint URLs."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx

from app.models.schemas import APIEndpoint
from app.discovery.scoring import score_feed_likeness

# ── Patterns that suggest an API endpoint ─────────────────────────────────

_API_URL_PATTERNS = [
    r'/api/',
    r'/v[1-9]/',
    r'/graphql',
    r'/wp-json/',
    r'/_next/data/',
    r'/rest/',
    r'/query',
]

_INTERESTING_KEYWORDS = re.compile(
    r'(?:posts|articles|events|entries|items|listings|news|feed|blog|stories|updates)',
    re.IGNORECASE,
)

# URLs we never want to probe.
_EXCLUDE_PATTERNS = re.compile(
    r'(?:'
    r'analytics|tracking|pixel|beacon|/log(?:s)?(?:/|$)'
    r'|google-analytics|facebook\.com|doubleclick'
    r'|/ads/|sentry\.io|hotjar\.com|cloudflare'
    r'|/auth/|/login|/logout|/oauth|/token'
    r'|\.(?:css|js|png|jpg|jpeg|gif|svg|woff2?|ttf|ico)(?:\?|$)'
    r')',
    re.IGNORECASE,
)

# Regex to extract URL-like strings from JS source.
_URL_IN_JS = re.compile(
    r'''(?:["'`])'''          # opening quote
    r'(https?://[^"\'`\s]{8,300})'  # URL
    r'''(?:["'`])''',        # closing quote
    re.IGNORECASE,
)

# Regex to extract relative API paths from JS source.
_RELATIVE_API_PATH = re.compile(
    r'''(?:["'`])'''
    r'(/(?:api|v[1-9]|wp-json|rest|graphql)[^"\'`\s]{2,200})'
    r'''(?:["'`])''',
    re.IGNORECASE,
)

# Find <script src="..."> tags.
_SCRIPT_SRC_RE = re.compile(
    r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE
)


def _is_api_like(url: str) -> bool:
    lower = url.lower()
    if _EXCLUDE_PATTERNS.search(lower):
        return False
    for pat in _API_URL_PATTERNS:
        if re.search(pat, lower):
            return True
    if _INTERESTING_KEYWORDS.search(lower):
        return True
    return False


async def extract_api_urls(
    url: str,
    html: str,
    client: httpx.AsyncClient,
    timeout: float = 10.0,
    max_js_files: int = 5,
) -> list[APIEndpoint]:
    """Extract candidate API URLs from inline JS and linked scripts."""

    candidates: set[str] = set()
    base_domain = urlparse(url).netloc

    # ── Inline sources ────────────────────────────────────────────────────
    _scan_source(html, url, candidates)

    # ── Linked JS files (only same-origin, first N by appearance) ────────
    script_srcs = _SCRIPT_SRC_RE.findall(html)
    fetched = 0
    for src in script_srcs:
        if fetched >= max_js_files:
            break
        abs_src = urljoin(url, src)
        # Only fetch same-origin scripts.
        if urlparse(abs_src).netloc != base_domain:
            continue
        try:
            resp = await client.get(abs_src, timeout=timeout)
            if resp.status_code == 200 and len(resp.text) < 2_000_000:
                _scan_source(resp.text, url, candidates)
                fetched += 1
        except (httpx.HTTPError, httpx.TimeoutException):
            continue

    # ── Probe candidates ──────────────────────────────────────────────────
    endpoints: list[APIEndpoint] = []
    for candidate_url in sorted(candidates):
        ep = await _probe_endpoint(candidate_url, client, timeout)
        if ep is not None:
            endpoints.append(ep)

    endpoints.sort(key=lambda e: e.feed_score, reverse=True)
    return endpoints


def _scan_source(source: str, base_url: str, acc: set[str]) -> None:
    """Find URL strings in *source* and add API-like ones to *acc*."""
    # Absolute URLs
    for m in _URL_IN_JS.finditer(source):
        u = m.group(1).split("'")[0].split('"')[0].split('`')[0]
        if _is_api_like(u):
            acc.add(u)

    # Relative API paths
    for m in _RELATIVE_API_PATH.finditer(source):
        path = m.group(1)
        absolute = urljoin(base_url, path)
        if _is_api_like(absolute):
            acc.add(absolute)


async def _probe_endpoint(
    candidate_url: str,
    client: httpx.AsyncClient,
    timeout: float,
) -> APIEndpoint | None:
    """GET the URL and see if it returns a feed-like JSON response."""
    try:
        resp = await client.get(
            candidate_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
    except (httpx.HTTPError, httpx.TimeoutException):
        return None

    ct = (resp.headers.get("content-type") or "").lower()
    if "json" not in ct:
        return None
    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    sc = score_feed_likeness(data)
    if sc < 0.15:
        return None

    # Extract sample info.
    items = _first_items(data)
    sample_keys = sorted({k for item in items[:5] for k in item.keys()})[:15] if items else []
    sample_item = _sanitise_sample(items[0]) if items else None

    return APIEndpoint(
        url=candidate_url,
        method="GET",
        content_type=ct.split(";")[0].strip(),
        item_count=len(items),
        sample_keys=sample_keys,
        sample_item=sample_item,
        feed_score=sc,
    )


def _first_items(data) -> list[dict]:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _sanitise_sample(item: dict) -> dict:
    """Return a trimmed version of *item* suitable for display."""
    out: dict = {}
    for k, v in list(item.items())[:10]:
        if isinstance(v, str) and len(v) > 200:
            v = v[:200] + "…"
        elif isinstance(v, (list, dict)):
            v = f"<{type(v).__name__} len={len(v)}>"
        out[k] = v
    return out
