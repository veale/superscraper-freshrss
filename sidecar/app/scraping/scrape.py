"""Phase 4 — routine scraping with adaptive selectors.

Scrapling version note (0.4.6):
  • `adaptive=True` on Selector() init enables the SQLite storage backend.
  • `sel.xpath(expr, auto_save=True)` saves a structural fingerprint when the
    selector finds elements.
  • `sel.xpath(expr, adaptive=True, auto_save=True)` attempts adaptive
    relocation when the exact selector matches nothing.
  • Persistence is the SQLite file itself — no JSON serialisation needed.
  • Drift is detected when the exact selector returns nothing but adaptive
    relocation succeeds.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from scrapling import Selector

from app.models.schemas import (
    FeedStrategy,
    ScrapeItem,
    ScrapeRequest,
    ScrapeResponse,
    ScrapeSelectors,
)
from app.services.config import ServiceConfig
from app.services.fetch import fetch_with_capture

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_CACHE_DIR = Path(os.getenv("AUTOFEED_CACHE_DIR", "/app/data/scrape-cache"))
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

_JS_RENDERED_HINTS = re.compile(
    r"(next\.js|nuxt|gatsby|react|angular|vue|svelte|__NEXT_DATA__|___gatsby)",
    re.IGNORECASE,
)


# ── Public entrypoint ─────────────────────────────────────────────────────────


async def run_scrape(req: ScrapeRequest) -> ScrapeResponse:
    """Strategy dispatcher. Each branch returns a list[ScrapeItem]."""
    services = req.services.normalised()
    started = datetime.now(timezone.utc)
    items: list[ScrapeItem] = []
    warnings: list[str] = []
    errors: list[str] = []
    drift = False
    cache_hit = False
    backend_used = "none"

    try:
        if req.strategy == FeedStrategy.RSS:
            items, warnings = await _scrape_rss(req.url, req.timeout)
            backend_used = "httpx"

        elif req.strategy in (FeedStrategy.JSON_API, FeedStrategy.JSON_DOT_NOTATION):
            items, warnings = await _scrape_json_api(req, services)
            backend_used = "httpx"

        elif req.strategy == FeedStrategy.XPATH:
            items, warnings, drift, cache_hit, backend_used = await _scrape_xpath(
                req, services
            )

        elif req.strategy == FeedStrategy.EMBEDDED_JSON:
            items, warnings = await _scrape_embedded_json(req, services)
            backend_used = "httpx"

        elif req.strategy == FeedStrategy.GRAPHQL:
            items, warnings = await _scrape_graphql(req, services)
            backend_used = "httpx"

        else:
            errors.append(f"Strategy {req.strategy} is not supported by /scrape")

    except Exception as exc:
        errors.append(f"Scrape failed: {exc}")

    return ScrapeResponse(
        url=req.url,
        timestamp=started,
        strategy=req.strategy,
        items=items,
        item_count=len(items),
        drift_detected=drift,
        cache_hit=cache_hit,
        fetch_backend_used=backend_used,
        errors=errors,
        warnings=warnings,
    )


# ── RSS ───────────────────────────────────────────────────────────────────────


async def _scrape_rss(url: str, timeout: int) -> tuple[list[ScrapeItem], list[str]]:
    import feedparser  # local import — optional dep

    warnings: list[str] = []
    async with httpx.AsyncClient(
        headers=_HEADERS, follow_redirects=True, timeout=timeout
    ) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            return [], [f"RSS fetch error: {exc}"]

    feed = feedparser.parse(resp.text)
    items: list[ScrapeItem] = []
    for entry in feed.entries[:100]:
        content = (
            entry.get("content", [{}])[0].get("value", "")
            or entry.get("summary", "")
            or entry.get("description", "")
        )
        items.append(
            ScrapeItem(
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                content=content,
                timestamp=entry.get("published", "") or entry.get("updated", ""),
                author=entry.get("author", ""),
            )
        )
    if not items:
        warnings.append("feedparser found no entries")
    return items, warnings


# ── JSON API / dot-notation ───────────────────────────────────────────────────


def _dot_get(obj: Any, path: str) -> Any:
    """Walk *path* ('a.b.0.c') through *obj*. Returns None on miss."""
    if not path:
        return obj
    for segment in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(segment)
        elif isinstance(obj, list):
            try:
                obj = obj[int(segment)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return obj


def _map_json_item(raw: dict, sel: ScrapeSelectors) -> ScrapeItem:
    def _field(path: str) -> str:
        if not path:
            return ""
        v = _dot_get(raw, path)
        if v is None:
            return ""
        return str(v)

    return ScrapeItem(
        title=_field(sel.item_title),
        link=_field(sel.item_link),
        content=_field(sel.item_content),
        timestamp=_field(sel.item_timestamp),
        thumbnail=_field(sel.item_thumbnail),
        author=_field(sel.item_author),
        raw=raw,
    )


async def _scrape_json_api(
    req: ScrapeRequest, services: ServiceConfig
) -> tuple[list[ScrapeItem], list[str]]:
    warnings: list[str] = []
    async with httpx.AsyncClient(
        headers={**_HEADERS, "Accept": "application/json"},
        follow_redirects=True,
        timeout=req.timeout,
    ) as client:
        try:
            resp = await client.get(req.url)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [], [f"JSON API fetch error: {exc}"]

    arr = _dot_get(data, req.selectors.item)
    if not isinstance(arr, list):
        warnings.append(
            f"item path '{req.selectors.item}' did not resolve to a list"
        )
        return [], warnings

    items = [_map_json_item(it, req.selectors) for it in arr[:100] if isinstance(it, dict)]
    return items, warnings


async def _scrape_embedded_json(
    req: ScrapeRequest, services: ServiceConfig
) -> tuple[list[ScrapeItem], list[str]]:
    """Fetch the page HTML (stealth if backend configured), extract embedded JSON.

    Scrapling-serve / browser backends give better JS-rendered output; plain
    httpx is acceptable because embedded JSON is server-rendered (it's in the
    initial HTML payload). Only use the browser path when the user explicitly
    chose a non-bundled backend.
    """
    warnings: list[str] = []
    services = services.normalised()
    backend = services.chosen_backend()

    if backend != "bundled":
        try:
            html, _ = await fetch_with_capture(
                req.url, services, timeout=req.timeout, capture_responses=False,
                stealth=req.stealth, solve_cloudflare=req.solve_cloudflare,
            )
        except Exception as exc:
            return [], [f"Browser fetch error: {exc}"]
    else:
        async with httpx.AsyncClient(
            headers=_HEADERS, follow_redirects=True, timeout=req.timeout
        ) as client:
            try:
                resp = await client.get(req.url)
                html = resp.text
            except httpx.HTTPError as exc:
                return [], [f"Fetch error: {exc}"]

    # Walk the JSON path from the page's inline scripts.
    import json as _json
    import re as _re

    # Extract all <script> text blocks and look for the path.
    script_pattern = _re.compile(
        r"<script[^>]*>(.*?)</script>", _re.DOTALL | _re.IGNORECASE
    )
    raw_obj: Any = None
    for m in script_pattern.finditer(html):
        text = m.group(1).strip()
        if not text or not text.startswith("{") and not text.startswith("["):
            # Try JSON assignment patterns: var x = {...}
            assign = _re.search(r"=\s*(\{.*\}|\[.*\])\s*;?\s*$", text, _re.DOTALL)
            if assign:
                text = assign.group(1)
            else:
                continue
        try:
            raw_obj = _json.loads(text)
            break
        except (ValueError, _json.JSONDecodeError):
            continue

    if raw_obj is None:
        warnings.append("No parseable JSON found in page scripts")
        return [], warnings

    arr = _dot_get(raw_obj, req.selectors.item)
    if not isinstance(arr, list):
        warnings.append(
            f"item path '{req.selectors.item}' did not resolve to a list in embedded JSON"
        )
        return [], warnings

    items = [_map_json_item(it, req.selectors) for it in arr[:100] if isinstance(it, dict)]
    return items, warnings


# ── XPath (adaptive) ─────────────────────────────────────────────────────────


def _serialise_scrapling_element(el: Any) -> str:
    """Return the outer HTML of a Scrapling element as a string."""
    html_content = getattr(el, "html_content", None)
    if html_content:
        return html_content
    body = getattr(el, "body", None)
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        return body
    raw = getattr(el, "_element", None) or (el if hasattr(el, "tag") else None)
    if raw is not None:
        try:
            from lxml import etree
            return etree.tostring(raw, encoding="unicode")
        except Exception:
            pass
    return ""


def _is_safe_key(key: str) -> bool:
    return bool(_SAFE_KEY.fullmatch(key))


async def _scrape_xpath(
    req: ScrapeRequest, services: ServiceConfig
) -> tuple[list[ScrapeItem], list[str], bool, bool, str]:
    """HTML+XPath scrape with optional Scrapling adaptive selector tracking.

    Returns (items, warnings, drift_detected, cache_hit, backend_used).

    Scrapling 0.4.6 adaptive API:
      - Selector(html, adaptive=True, storage_args={...}) enables SQLite storage.
      - sel.xpath(expr, auto_save=True) saves fingerprint when elements found.
      - sel.xpath(expr, adaptive=True, auto_save=True) relocates when exact fails.
    """
    warnings: list[str] = []
    services = services.normalised()
    backend = services.chosen_backend()

    # 1. Fetch HTML.
    if backend == "bundled" and not _is_likely_js_rendered(req.url):
        async with httpx.AsyncClient(
            headers=_HEADERS, follow_redirects=True, timeout=req.timeout
        ) as client:
            try:
                resp = await client.get(req.url)
                html = resp.text
            except httpx.HTTPError as exc:
                return [], [f"Fetch error: {exc}"], False, False, "httpx"
        backend_used = "httpx"
    else:
        try:
            html, _ = await fetch_with_capture(
                req.url, services, timeout=req.timeout, capture_responses=False,
                stealth=req.stealth, solve_cloudflare=req.solve_cloudflare,
            )
        except Exception as exc:
            return [], [f"Browser fetch error: {exc}"], False, False, str(backend)
        backend_used = str(backend)

    # 2. Build Scrapling Selector with adaptive storage if requested.
    cache_enabled = req.adaptive and _is_safe_key(req.cache_key) if req.cache_key else False
    cache_hit = False
    db_path: str | None = None

    if cache_enabled:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        db_path = str(_CACHE_DIR / f"{req.cache_key}.db")
        cache_hit = os.path.exists(db_path)
        sel = Selector(
            html,
            adaptive=True,
            storage_args={"storage_file": db_path, "url": req.url},
        )
    else:
        sel = Selector(html)

    # 3. Run the item selector.
    drift = False
    if not req.selectors.item:
        warnings.append("No item selector provided")
        return [], warnings, False, cache_hit, backend_used

    try:
        # Try exact match first; auto_save records fingerprint when successful.
        elements = sel.xpath(req.selectors.item, auto_save=cache_enabled)
        if not elements and cache_hit:
            # Exact failed but we have a stored fingerprint — attempt adaptive relocation.
            elements = sel.xpath(
                req.selectors.item, adaptive=True, auto_save=cache_enabled
            )
            drift = bool(elements)
    except Exception as exc:
        warnings.append(f"XPath evaluation error: {exc}")
        return [], warnings, False, cache_hit, backend_used

    if not elements and req.selectors.example_text:
        # Adaptive relocation failed or not available — try AutoScraper-style recovery.
        from app.scraping.rule_builder import recover_selector
        try:
            stack = recover_selector(html, req.selectors.example_text)
            if stack is not None and stack.sibling_count >= 3:
                recovered_sel = Selector(html)
                try:
                    elements = recovered_sel.xpath(stack.xpath)
                except Exception:
                    elements = []
                if elements:
                    drift = True
                    warnings.append(
                        f"Original selector matched 0; recovered via rule builder: {stack.xpath}"
                    )
        except Exception as exc:
            warnings.append(f"Rule builder recovery error: {exc}")

    if not elements:
        warnings.append(
            f"Item selector matched 0 elements; adaptive={'on' if req.adaptive else 'off'}"
        )
        return [], warnings, False, cache_hit, backend_used

    # 4. Attempt per-field selector recovery for fields with examples that return blanks.
    sel_updated = req.selectors
    _FIELD_EXAMPLES = [
        ("item_title",     "title_examples",     "title"),
        ("item_link",      "link_examples",      "link"),
        ("item_content",   "content_examples",   "content"),
        ("item_timestamp", "timestamp_examples", "timestamp"),
        ("item_author",    "author_examples",    "author"),
        ("item_thumbnail", "thumbnail_examples", "thumbnail"),
    ]
    for field_attr, examples_attr, label in _FIELD_EXAMPLES:
        # Get plural examples list, fall back to singular for migration
        examples_list = getattr(req.selectors, examples_attr, [])
        if not examples_list:
            # Check legacy singular field for migration
            singular_attr = examples_attr.replace("_examples", "_example")
            singular_val = getattr(req.selectors, singular_attr, "")
            if singular_val:
                examples_list = [singular_val]

        if not examples_list:
            continue

        test_items = [_map_element(el, req.selectors, base_url=req.url) for el in elements[:3]]
        non_empty = sum(1 for it in test_items if getattr(it, label, ""))
        if non_empty >= len(test_items) // 2 + 1:
            continue  # already working well enough

        # Try to recover a better selector by scanning up to 5 items.
        try:
            from app.scraping.rule_builder import recover_field_selectors
            item_html = ""
            # Find an item HTML that contains any of the examples
            for el in elements[:5]:
                frag = _serialise_scrapling_element(el)
                for ex in examples_list:
                    if ex.lower() in frag.lower():
                        item_html = frag
                        break
                if item_html:
                    break
                if len(frag) > len(item_html):
                    item_html = frag

            if item_html:
                recovered_xpaths = recover_field_selectors(
                    item_html, examples_list, html, req.selectors.item
                )
                if len(recovered_xpaths) >= 2:
                    # Multiple distinct XPaths - create union selector
                    merged = " | ".join(f"({xp})" for xp in recovered_xpaths)
                    sel_updated = sel_updated.model_copy(update={field_attr: merged})
                    warnings.append(f"Recovered {field_attr} via union: {merged}")
                elif recovered_xpaths:
                    sel_updated = sel_updated.model_copy(update={field_attr: recovered_xpaths[0]})
                    warnings.append(f"Recovered {field_attr} via example: {recovered_xpaths[0]}")
        except Exception as exc:
            warnings.append(f"Field recovery error for {field_attr}: {exc}")

    # 5. Map each element through per-field sub-selectors.
    items: list[ScrapeItem] = []
    for el in elements[:100]:
        items.append(_map_element(el, sel_updated, base_url=req.url))

    return items, warnings, drift, cache_hit, backend_used


async def _scrape_graphql(
    req: ScrapeRequest, services: ServiceConfig
) -> tuple[list[ScrapeItem], list[str]]:
    """Replay a saved GraphQL operation and map items."""
    warnings: list[str] = []
    op = req.graphql
    if op is None:
        return [], ["No GraphQL operation provided in request"]

    payload: dict[str, Any] = {"query": op.query}
    if op.variables:
        payload["variables"] = op.variables
    if op.operation_name:
        payload["operationName"] = op.operation_name

    headers = {
        **_HEADERS,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if services.auth_token:
        headers["Authorization"] = f"Bearer {services.auth_token}"

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=req.timeout
    ) as client:
        try:
            resp = await client.post(op.endpoint, json=payload)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return [], [f"GraphQL fetch error: {exc}"]

    # Navigate to the items array via response_path
    arr = _dot_get(data, op.response_path) if op.response_path else data
    if isinstance(arr, dict):
        # Try data.<first key> heuristic when path leads to the data envelope
        inner = arr.get("data") or arr
        if isinstance(inner, dict) and len(inner) == 1:
            arr = next(iter(inner.values()))
    if not isinstance(arr, list):
        warnings.append(
            f"GraphQL response_path '{op.response_path}' did not resolve to a list"
        )
        return [], warnings

    items = [_map_json_item(it, req.selectors) for it in arr[:100] if isinstance(it, dict)]
    if not items:
        warnings.append("GraphQL operation returned 0 mappable items")
    return items, warnings


def _is_likely_js_rendered(url: str) -> bool:
    """Rough heuristic — force browser for known JS-heavy domains."""
    return False  # Let the caller decide via services.chosen_backend()


def _map_element(el: Any, selectors: ScrapeSelectors, base_url: str) -> ScrapeItem:
    """Apply per-field XPath selectors relative to *el*."""

    def _first_text(xp: str) -> str:
        if not xp:
            return ""
        try:
            r = el.xpath(xp)
            if not r:
                return ""
            v = r[0]
            return v.text if hasattr(v, "text") else str(v)
        except Exception:
            return ""

    link = _first_text(selectors.item_link)
    if link and not link.startswith(("http://", "https://")):
        link = urljoin(base_url, link)

    return ScrapeItem(
        title=_first_text(selectors.item_title).strip(),
        link=link,
        content=_first_text(selectors.item_content).strip(),
        timestamp=_first_text(selectors.item_timestamp).strip(),
        thumbnail=_first_text(selectors.item_thumbnail).strip(),
        author=_first_text(selectors.item_author).strip(),
    )
