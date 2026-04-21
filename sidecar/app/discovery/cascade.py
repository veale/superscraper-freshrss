"""Discovery cascade — orchestrates all discovery steps."""


import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.discovery.date_anchor import anchor_via_dates
from app.discovery.embedded_json import detect_embedded_json
from app.discovery.rss_autodiscovery import discover_rss
from app.discovery.scoring import score_feed_likeness
from app.discovery.scrapling_selectors import generate_selectors_with_scrapling
from app.discovery.selector_generation import generate_xpath_candidates
from app.discovery.static_js_analysis import extract_api_urls
from app.services.fetch import fetch_with_capture
from app.utils.skeleton import build_skeleton
from app.utils.tree_pruning import build_pruned_html
from app.discovery.field_mapper import auto_map_fields
from app.discovery.graphql_detect import detect_graphql_in_capture
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


async def run_discovery(
    req: DiscoverRequest,
    trace: dict | None = None,
) -> DiscoverResponse:
    """Execute the full discovery cascade for *req.url*.

    When *trace* is provided it is populated with intermediate artifacts
    (raw_html, pruned_html, skeleton) and per-step provenance so the UI
    transparency panels can render what was actually fed into each stage.
    """

    def _t(path: str, value):
        if trace is None:
            return
        cur = trace
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value

    errors: list[str] = []
    url = req.url.strip()
    html = ""
    page_meta = PageMeta()
    _t("fetch.url", url)
    _t("fetch.method", "httpx (Chrome UA, redirects)")
    _t("fetch.headers", dict(_HEADERS))
    _t("fetch.timeout", req.timeout)

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
            _t("fetch.status", resp.status_code)
            _t("fetch.final_url", str(resp.url))
            _t("fetch.response_headers", dict(resp.headers))
            _t("artifacts.raw_html", html)
        except httpx.HTTPStatusError as exc:
            errors.append(f"HTTP {exc.response.status_code} fetching {url}")
            _t("fetch.status", exc.response.status_code)
        except httpx.TimeoutException:
            errors.append(f"Timeout fetching {url}")
            _t("fetch.error", "timeout")
        except httpx.HTTPError as exc:
            errors.append(f"Error fetching {url}: {exc}")
            _t("fetch.error", str(exc))

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
        _t("steps.rss", {
            "method": "<link rel=alternate> + <a> probes + well-known paths (feed.xml, rss, atom.xml)",
            "count": len(rss_feeds),
            "urls": [f.url for f in rss_feeds],
        })

        # ── Step 2: Embedded JSON detection ────────────────────────────────
        try:
            embedded_json = detect_embedded_json(html)
            for ej in embedded_json:
                if ej.sample_keys and not ej.field_mapping:
                    ej.field_mapping = auto_map_fields(ej.sample_keys)
        except Exception as exc:
            embedded_json = []
            errors.append(f"Embedded JSON detection error: {exc}")
        _t("steps.embedded_json", {
            "method": "script[type=application/ld+json] + __NEXT_DATA__ + other inline JSON blobs",
            "count": len(embedded_json),
            "paths": [e.path for e in embedded_json],
        })

        # ── Step 3: Static JS API URL extraction + probing ─────────────────
        try:
            api_endpoints = await extract_api_urls(
                url, html, client, timeout=8, max_js_files=5
            )
        except Exception as exc:
            api_endpoints = []
            errors.append(f"Static JS analysis error: {exc}")
        _t("steps.api_static", {
            "method": "regex-scan for /api/, .json, fetch('...') URLs inside inline + external JS files (max 5)",
            "count": len(api_endpoints),
            "urls": [a.url for a in api_endpoints],
        })

        # ── Tree pruning pre-pass ──────────────────────────────────────────
        # Only XPath/selector generators and the skeleton builder use pruned HTML.
        # RSS autodiscovery, embedded-JSON, and static-JS-analysis need the raw
        # HTML because they look inside <script> blocks which prune_tree removes.
        # listing_mode=True preserves article-card metadata nodes (timestamp,
        # author, meta wrappers) so XPath candidate generation can see them.
        try:
            pruned_html = build_pruned_html(html, listing_mode=True)
        except Exception as exc:
            pruned_html = html
            errors.append(f"Tree pruning error: {exc}")
        _t("artifacts.pruned_html", pruned_html)
        _t("steps.prune", {
            "method": "tree_pruning.build_pruned_html (listing_mode=True — keeps article-card meta nodes)",
            "input_bytes": len(html),
            "output_bytes": len(pruned_html),
        })

        # ── Step 5 (Phase 1): Heuristic XPath candidate generation ─────────
        try:
            xpath_candidates = generate_xpath_candidates(pruned_html)
        except Exception as exc:
            xpath_candidates = []
            errors.append(f"XPath generation error: {exc}")
        _t("steps.xpath_heuristic", {
            "method": "generate_xpath_candidates (frequency + co-occurrence heuristic on pruned HTML)",
            "count": len(xpath_candidates),
            "item_selectors": [c.item_selector for c in xpath_candidates],
        })

    # ── Phase 2 steps (browser required) ──────────────────────────────────
    # Run when:
    #   • caller forces browser mode, OR
    #   • no RSS found AND (page is JS-rendered OR no API endpoints found), OR
    #   • anti-bot was detected (need stealth browser)
    any_live_rss = any(feed.is_alive for feed in rss_feeds)
    if req.force_skip_rss:
        any_live_rss = False

    needs_browser = req.use_browser or (
        not any_live_rss
        and (
            page_meta.has_javascript_content
            or page_meta.anti_bot_detected
            or not api_endpoints
        )
    )
    _t("decision.needs_browser", needs_browser)
    _t("decision.any_live_rss", any_live_rss)
    _t("decision.force_skip_rss", req.force_skip_rss)

    browser_html = ""
    graphql_operations = []
    stealth_used = False
    if needs_browser:
        # ── Step 4: Network interception ───────────────────────────────────
        # Auto-promote to stealth when anti-bot markers are detected,
        # or when user explicitly requests it via force_stealth.
        use_stealth = page_meta.anti_bot_detected or req.force_stealth
        if use_stealth:
            stealth_used = True
        network_responses: list[dict] = []
        try:
            browser_html, network_responses = await fetch_with_capture(
                url, req.services, timeout=min(req.timeout, 30),
                stealth=use_stealth,
            )
            _t("artifacts.browser_html", browser_html)
            _t("steps.browser_fetch", {
                "method": f"fetch_with_capture (backend={req.services.fetch_backend}, stealth={use_stealth})",
                "html_bytes": len(browser_html),
                "network_response_count": len(network_responses),
                "network_urls": [r.get("url", "") for r in network_responses[:20]],
            })
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
                            field_mapping=auto_map_fields(sample_keys),
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

        # ── GraphQL detection (inline) ──────────────────────────────────────
        try:
            graphql_operations = await detect_graphql_in_capture(network_responses)
        except Exception as exc:
            graphql_operations = []
            errors.append(f"GraphQL detection error: {exc}")
        _t("steps.graphql", {
            "method": "detect_graphql_in_capture (inspects intercepted POSTs for 'query'/'variables' payloads)",
            "count": len(graphql_operations),
            "operations": [g.operation_name or "" for g in graphql_operations],
        })

        # ── Step 5 (Phase 2): Scrapling selector generation ────────────────
        # Use browser-rendered HTML when available; prune before passing to
        # selector generator so noise subtrees don't pollute candidate scoring.
        analysis_html = browser_html or html
        try:
            pruned_analysis_html = build_pruned_html(analysis_html, listing_mode=True)
        except Exception:
            pruned_analysis_html = analysis_html
        try:
            scrapling_candidates = generate_selectors_with_scrapling(pruned_analysis_html)
            xpath_candidates = _merge_xpath_candidates(
                scrapling_candidates, xpath_candidates
            )
            _t("steps.xpath_scrapling", {
                "method": "scrapling auto-selector generation on browser-rendered pruned HTML",
                "count": len(scrapling_candidates),
                "item_selectors": [c.item_selector for c in scrapling_candidates],
            })
        except Exception as exc:
            errors.append(f"Scrapling selector generation error: {exc}")
            _t("steps.xpath_scrapling", {"method": "scrapling auto-selector generation", "error": str(exc)})

    # ── Step 5.4: Date-anchor heuristic ───────────────────────────────────────
    # Dates are the least ambiguous item cue (titles/links recur in nav, ads,
    # related-posts; dates rarely do). Scan the page for date text + <time>
    # elements, cluster by lowest repeating ancestor, validate sibling shape.
    try:
        date_anchor_html = browser_html or pruned_html or html
        date_candidate = anchor_via_dates(date_anchor_html)
    except Exception as exc:
        date_candidate = None
        errors.append(f"Date-anchor error: {exc}")
    _t("steps.date_anchor", {
        "method": "date_anchor.anchor_via_dates (walk-up from dated nodes, validate sibling shape)",
        "outcome": {
            "item_selector": date_candidate.item_selector if date_candidate else None,
            "item_count": date_candidate.item_count if date_candidate else 0,
            "confidence": date_candidate.confidence if date_candidate else None,
        } if date_candidate else None,
    })
    if date_candidate is not None:
        xpath_candidates = _merge_xpath_candidates([date_candidate], xpath_candidates)

    # ── Step 5.5: Initial-examples anchor (LCA-based, cross-family union) ─────
    if req.initial_examples:
        from app.discovery.multi_field_anchor import find_items_from_rows
        anchor_html = browser_html or html
        try:
            outcome = find_items_from_rows(anchor_html, req.initial_examples)
        except Exception as exc:
            outcome = None
            errors.append(f"Initial-examples anchor error: {exc}")
        _t("steps.initial_examples", {
            "method": "multi_field_anchor.find_items_from_rows (LCA over user-supplied example rows)",
            "rows": req.initial_examples,
            "outcome": {
                "item_selector": outcome.item_selector if outcome else None,
                "confidence": outcome.confidence if outcome else None,
                "item_count": outcome.item_count if outcome else None,
            } if outcome else None,
        })
        if outcome is not None:
            from app.models.schemas import XPathCandidate
            anchored = XPathCandidate(
                item_selector=outcome.item_selector,
                title_selector=outcome.field_selectors.get("title", ""),
                link_selector=outcome.field_selectors.get("link", ""),
                content_selector=outcome.field_selectors.get("content", ""),
                timestamp_selector=outcome.field_selectors.get("timestamp", ""),
                author_selector=outcome.field_selectors.get("author", ""),
                thumbnail_selector=outcome.field_selectors.get("thumbnail", ""),
                confidence=outcome.confidence,
                item_count=outcome.item_count,
                item_selector_union=" | " in outcome.item_selector,
            )
            # Prepend so it shows first; let _merge deduplicate.
            xpath_candidates = _merge_xpath_candidates([anchored], xpath_candidates)

    html_skeleton = build_skeleton(browser_html or html) if (browser_html or html) else ""
    _t("artifacts.html_skeleton", html_skeleton)
    _t("steps.skeleton", {
        "method": "utils.skeleton.build_skeleton (collapses text to [text:N] markers, keeps structure)",
        "source": "browser_html" if browser_html else "raw_html",
        "output_bytes": len(html_skeleton),
    })

    backend_used = req.services.fetch_backend if needs_browser else "http"
    _t("decision.backend_used", backend_used)
    _t("decision.stealth_used", stealth_used)

    return DiscoverResponse(
        url=url,
        timestamp=datetime.now(timezone.utc),
        results=DiscoveryResults(
            rss_feeds=rss_feeds,
            api_endpoints=api_endpoints,
            embedded_json=embedded_json,
            xpath_candidates=xpath_candidates,
            graphql_operations=graphql_operations,
            page_meta=page_meta,
            html_skeleton=html_skeleton,
            phase2_used=needs_browser,
            stealth_used=stealth_used,
            force_skip_rss=req.force_skip_rss,
            backend_used=backend_used,
        ),
        errors=errors,
        browser_html=browser_html if needs_browser else "",
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
