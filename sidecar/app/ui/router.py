"""AutoFeed web UI — HTML routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.services import trace_store

_templates_dir = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_templates_dir)

router = APIRouter(include_in_schema=False)


def _ctx(request: Request, title: str = "AutoFeed", **extra: object) -> dict:
    flash = request.session.pop("flash", None)
    return {"request": request, "title": title, "flash": flash, **extra}


def _placeholder(request: Request, heading: str, note: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "placeholder.html",
        _ctx(request, heading, heading=heading, note=note),
    )


def _service_config():
    from app.services.config import ServiceConfig
    s = _store().get()
    return ServiceConfig(
        fetch_backend=s.get("fetch_backend", "bundled"),  # type: ignore[arg-type]
        playwright_server_url=s.get("playwright_server_url", ""),
        browserless_url=s.get("browserless_url", ""),
        scrapling_serve_url=s.get("scrapling_serve_url", ""),
        rss_bridge_url=s.get("rss_bridge_url", ""),
        auth_token=s.get("services_auth_token", ""),
    )


def _store():
    from app.ui.settings_store import get_store
    return get_store()


def _llm_config():
    from app.models.schemas import LLMConfig
    s = _store().get()
    if not s.get("llm_endpoint"):
        return None
    return LLMConfig(
        endpoint=s["llm_endpoint"],
        api_key=s.get("llm_api_key", ""),
        model=s.get("llm_model", "gpt-4o-mini"),
    )


def _bridges_dir() -> str:
    return os.getenv("AUTOFEED_BRIDGES_DIR", "/app/bridges")


def _entries(discover_id: str, candidates: list, type_key: str) -> list[dict]:
    # Sort by whichever score field this candidate type uses
    def score(c):
        d = c.model_dump()
        return d.get("confidence") or d.get("feed_score") or 0

    sorted_candidates = sorted(candidates, key=score, reverse=True)

    auto_indices = set()
    if sorted_candidates:
        auto_indices.add(0)  # always top 1
        for i, c in enumerate(sorted_candidates[1:], start=1):  # anything above 70%, max 3 total
            if score(c) >= 0.70 and len(auto_indices) < 3:
                auto_indices.add(i)

    return [
        {
            "c": c.model_dump(),
            "auto_preview": i in auto_indices,
            "preview_url": (
                f"/preview-fragment?discover_id={discover_id}"
                f"&type={type_key}&index={i}"
            ),
            "index": i,
        }
        for i, c in enumerate(sorted_candidates)
    ]


# ── Home ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    from app.ui.feeds_store import get_feeds_store
    recent = get_feeds_store().all()[:3]
    prefill_url = request.query_params.get("url", "")
    services = _service_config()
    backends_available = {
        "bundled": True,
        "stealthy": True,
        "playwright_server": bool(services.playwright_server_url),
        "browserless": bool(services.browserless_url),
        "scrapling_serve": bool(services.scrapling_serve_url),
    }
    return templates.TemplateResponse(
        request,
        "home.html",
        _ctx(
            request,
            "AutoFeed — Discover Feeds",
            recent_feeds=recent,
            prefill_url=prefill_url,
            backends_available=backends_available,
        ),
    )


# ── Discovery results ─────────────────────────────────────────────────────────

@router.get("/d/{discover_id}", response_class=HTMLResponse)
async def discover_results(request: Request, discover_id: str) -> HTMLResponse:
    from app.services.discovery_cache import load_discovery
    from app.models.schemas import DiscoverResponse

    stored = load_discovery(discover_id)
    if stored is None:
        return templates.TemplateResponse(
            request,
            "discover_not_found.html",
            _ctx(request, "Result not found", discover_id=discover_id),
            status_code=404,
        )

    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    res = result.results
    s = _store().get()
    has_llm = bool(s.get("llm_endpoint"))

    # Filter dead/empty candidates (Tier B.1)
    # Only keep RSS feeds that pass the liveness probe
    live_rss = [f for f in res.rss_feeds if f.is_alive]

    # Only keep APIs with at least 3 items or score >= 0.3
    useful_api = [
        a for a in res.api_endpoints
        if a.item_count >= 3 or a.feed_score >= 0.3
    ]

    # Only keep embedded JSON with >= 3 items
    useful_embedded = [
        e for e in res.embedded_json
        if e.item_count >= 3
    ]

    # GraphQL: keep all — already filtered by detector
    useful_graphql = list(res.graphql_operations)

    # XPath: keep all — user may refine low-confidence ones
    all_xpath = list(res.xpath_candidates)

    # Compute has_results from filtered lists
    has_results = bool(
        live_rss or useful_api or useful_embedded
        or all_xpath or useful_graphql
    )

    return templates.TemplateResponse(
        request,
        "discover_results.html",
        _ctx(
            request,
            f"Discovery — {result.url}",
            target_url=result.url,
            discover_id=discover_id,
            results=res,
            meta=res.page_meta.model_dump(),
            errors=stored.get("errors", []),
            has_llm=has_llm,
            has_results=has_results,
            rss_feeds=_entries(discover_id, live_rss, "rss"),
            api_endpoints=_entries(discover_id, useful_api, "api"),
            embedded_json=_entries(discover_id, useful_embedded, "embedded"),
            xpath_candidates=_entries(discover_id, all_xpath, "xpath"),
            graphql_operations=_entries(discover_id, useful_graphql, "graphql"),
        ),
    )


# ── Preview fragment (called async by discover results page) ──────────────────

@router.get("/preview-fragment", response_class=HTMLResponse)
async def preview_fragment(
    request: Request,
    discover_id: str,
    type: str,
    index: int = 0,
) -> HTMLResponse:
    from app.services.discovery_cache import load_discovery
    from app.models.schemas import DiscoverResponse, FeedStrategy, ScrapeRequest, ScrapeSelectors
    from app.scraping.scrape import run_scrape

    def _err(msg: str) -> HTMLResponse:
        return HTMLResponse(
            f'<div class="preview-error">{msg}</div>'
        )

    stored = load_discovery(discover_id)
    if stored is None:
        return _err("Discovery result expired or not found.")

    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    res = result.results
    source_url = result.url
    services = _service_config()

    try:
        if type == "rss":
            if index >= len(res.rss_feeds):
                return _err("Index out of range.")
            c = res.rss_feeds[index]
            req = ScrapeRequest(
                url=c.url, strategy=FeedStrategy.RSS,
                services=services, adaptive=False,
            )
        elif type == "xpath":
            if index >= len(res.xpath_candidates):
                return _err("Index out of range.")
            c = res.xpath_candidates[index]
            req = ScrapeRequest(
                url=source_url, strategy=FeedStrategy.XPATH,
                selectors=ScrapeSelectors(
                    item=c.item_selector,
                    item_title=c.title_selector,
                    item_link=c.link_selector,
                    item_content=c.content_selector,
                    item_timestamp=c.timestamp_selector,
                    item_thumbnail=c.thumbnail_selector,
                ),
                services=services, adaptive=False,
            )
        elif type == "api":
            if index >= len(res.api_endpoints):
                return _err("Index out of range.")
            c = res.api_endpoints[index]
            fm = c.field_mapping or {}
            req = ScrapeRequest(
                url=c.url, strategy=FeedStrategy.JSON_API,
                selectors=ScrapeSelectors(
                    item=c.item_path,
                    item_title=fm.get("title", ""),
                    item_link=fm.get("link", ""),
                    item_content=fm.get("content", ""),
                    item_timestamp=fm.get("timestamp", ""),
                    item_author=fm.get("author", ""),
                    item_thumbnail=fm.get("thumbnail", ""),
                ),
                method=c.method or "GET",
                request_body=c.request_body or "",
                request_headers=dict(c.request_headers or {}),
                pagination=c.pagination,
                max_pages=1,
                services=services, adaptive=False,
            )
        elif type == "embedded":
            if index >= len(res.embedded_json):
                return _err("Index out of range.")
            c = res.embedded_json[index]
            req = ScrapeRequest(
                url=source_url, strategy=FeedStrategy.EMBEDDED_JSON,
                selectors=ScrapeSelectors(item=c.path),
                services=services, adaptive=False,
            )
        elif type == "graphql":
            if index >= len(res.graphql_operations):
                return _err("Index out of range.")
            gql_op = res.graphql_operations[index]
            # Auto-map fields from sample_keys
            from app.discovery.field_mapper import auto_map_fields
            field_map = auto_map_fields(gql_op.sample_keys)
            req = ScrapeRequest(
                url=gql_op.endpoint,
                strategy=FeedStrategy.GRAPHQL,
                graphql=gql_op,
                selectors=ScrapeSelectors(
                    item_title=field_map.get("title", ""),
                    item_link=field_map.get("link", ""),
                    item_content=field_map.get("content", ""),
                    item_timestamp=field_map.get("timestamp", ""),
                    item_author=field_map.get("author", ""),
                ),
                services=services, adaptive=False,
            )
        else:
            return _err(f"Unknown type: {type}")

        scrape = await run_scrape(req)
        items = scrape.items[:10]
        total = len(items)
        fc = {
            "title":   sum(1 for it in items if it.title),
            "link":    sum(1 for it in items if it.link),
            "date":    sum(1 for it in items if it.timestamp),
            "content": sum(1 for it in items if it.content),
        }
        trace_store.add_action(discover_id, {
            "kind": "preview",
            "panel": f"{type}:{index}",
            "inputs": {
                "scrape_request": req.model_dump(mode="json"),
            },
            "outputs": {
                "item_count": total,
                "field_counts": fc,
                "errors": list(scrape.errors),
                "first_items": [it.model_dump() for it in items[:3]],
            },
            "provenance": {
                "method": f"run_scrape (strategy={req.strategy.value})",
            },
        })
        return templates.TemplateResponse(
            request,
            "partials/preview_table.html",
            {
                "request": request,
                "items": [it.model_dump() for it in items],
                "total": total,
                "field_counts": fc,
                "errors": scrape.errors,
            },
        )
    except Exception as exc:
        trace_store.add_action(discover_id, {
            "kind": "preview",
            "panel": f"{type}:{index}",
            "error": str(exc)[:500],
        })
        return _err(f"Preview failed: {str(exc)[:300]}")


# ── Preview-refine (re-run with per-field example injection) ──────────────────

@router.post("/preview-refine", response_class=HTMLResponse)
async def preview_refine(request: Request) -> HTMLResponse:
    """Re-run a preview with per-field example values injected into ScrapeSelectors."""
    from app.services.discovery_cache import load_discovery
    from app.models.schemas import DiscoverResponse, FeedStrategy, ScrapeRequest, ScrapeSelectors
    from app.scraping.scrape import run_scrape

    def _err(msg: str) -> HTMLResponse:
        return HTMLResponse(f'<div class="preview-error">{msg}</div>')

    form = await request.form()
    discover_id = str(form.get("discover_id", "")).strip()
    index = int(form.get("index", 0))
    services = _service_config()

    stored = load_discovery(discover_id)
    if stored is None:
        return _err("Discovery result expired.")
    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    res = result.results
    if index >= len(res.xpath_candidates):
        return _err("Index out of range.")
    c = res.xpath_candidates[index]

    def f(k: str) -> str:
        return str(form.get(k, "")).strip()

    def fl(k: str) -> list[str]:
        """Get list of values for a multi-value form field."""
        return [v.strip() for v in form.getlist(k) if v.strip()]

    req = ScrapeRequest(
        url=result.url,
        strategy=FeedStrategy.XPATH,
        selectors=ScrapeSelectors(
            item=c.item_selector,
            item_title=c.title_selector,
            item_link=c.link_selector,
            item_content=c.content_selector,
            item_timestamp=c.timestamp_selector,
            item_thumbnail=c.thumbnail_selector,
            # Support both singular (legacy) and plural (new) form fields
            title_examples=fl("title_examples") or [f("title_example")] if f("title_example") else [],
            link_examples=fl("link_examples") or [f("link_example")] if f("link_example") else [],
            content_examples=fl("content_examples") or [f("content_example")] if f("content_example") else [],
            timestamp_examples=fl("timestamp_examples") or [f("timestamp_example")] if f("timestamp_example") else [],
            author_examples=fl("author_examples") or [f("author_example")] if f("author_example") else [],
            thumbnail_examples=fl("thumbnail_examples") or [f("thumbnail_example")] if f("thumbnail_example") else [],
        ),
        services=services,
        adaptive=False,
    )
    try:
        scrape = await run_scrape(req)
        items = scrape.items[:10]
        total = len(items)
        fc = {
            "title":   sum(1 for it in items if it.title),
            "link":    sum(1 for it in items if it.link),
            "date":    sum(1 for it in items if it.timestamp),
            "content": sum(1 for it in items if it.content),
        }
        return templates.TemplateResponse(
            request,
            "partials/preview_table.html",
            {
                "request": request,
                "items": [it.model_dump() for it in items],
                "total": total,
                "field_counts": fc,
                "errors": scrape.errors,
                "warnings": scrape.warnings,
                "refine_url": None,
            },
        )
    except Exception as exc:
        return _err(f"Refine failed: {str(exc)[:300]}")


@router.post("/preview-fragment-refined")
async def preview_fragment_refined(request: Request):
    """Re-run every candidate's preview with the global refine examples injected.

    Returns a dict {type: {index: html}} that the client applies to existing
    .preview-target nodes.
    """
    import asyncio
    from app.services.discovery_cache import load_discovery, update_discovery, load_browser_html
    from app.models.schemas import DiscoverResponse, FeedStrategy, ScrapeRequest, ScrapeSelectors, XPathCandidate
    from app.scraping.scrape import run_scrape, fetch_and_parse, _scrape_xpath_from_selector
    from app.discovery.multi_field_anchor import decode_example_rows, find_items_from_rows

    form = await request.form()
    discover_id = str(form.get("discover_id", "")).strip()
    services = _service_config()

    stored = load_discovery(discover_id)
    if stored is None:
        return JSONResponse({"error": "Discovery result expired."}, status_code=400)

    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    res = result.results

    refine_examples: dict[str, list[str]] = {}
    for role in ["title", "link", "content", "timestamp", "author", "thumbnail"]:
        examples = [v.strip() for v in form.getlist(f"{role}_examples") if v.strip()]
        if examples:
            refine_examples[role] = examples

    # ── Fix 1: anchor a fresh XPath candidate from the user's examples ────────
    # When the user provides examples, run the deterministic LCA anchor up front
    # and prepend the result as a top-ranked candidate. Existing heuristic
    # candidates may be wrong (e.g. matched a taxonomy widget); example-anchored
    # containers are usually correct, so they should lead the list.
    anchor_result = None
    anchor_error: str | None = None
    if refine_examples:
        rows = decode_example_rows(form)
        if rows:
            anchor_html = load_browser_html(discover_id) or ""
            if not anchor_html:
                # Prefer the stored raw_html artifact over a fresh fetch —
                # it's the exact bytes discovery saw, so user examples that
                # appear in the transparency panels will match here.
                artifact = trace_store.get_artifact(discover_id, "raw_html")
                if artifact and artifact.get("content"):
                    anchor_html = artifact["content"]
            if not anchor_html:
                try:
                    anchor_html, _, _ = await fetch_and_parse(
                        result.url, services, timeout=30
                    )
                except RuntimeError as exc:
                    anchor_error = f"Fetch failed during anchoring: {str(exc)[:200]}"
            if anchor_html:
                try:
                    anchor_result = find_items_from_rows(anchor_html, rows)
                except Exception as exc:
                    anchor_error = f"Anchor error: {str(exc)[:200]}"

    if anchor_result is not None:
        anchored = XPathCandidate(
            item_selector=anchor_result.item_selector,
            title_selector=anchor_result.field_selectors.get("title", ""),
            link_selector=anchor_result.field_selectors.get("link", ""),
            content_selector=anchor_result.field_selectors.get("content", ""),
            timestamp_selector=anchor_result.field_selectors.get("timestamp", ""),
            author_selector=anchor_result.field_selectors.get("author", ""),
            thumbnail_selector=anchor_result.field_selectors.get("thumbnail", ""),
            confidence=anchor_result.confidence,
            item_count=anchor_result.item_count,
            item_selector_union=" | " in anchor_result.item_selector,
        )
        # Prepend if not already the exact same item selector.
        existing = {c.item_selector for c in res.xpath_candidates}
        if anchored.item_selector not in existing:
            res.xpath_candidates = [anchored] + list(res.xpath_candidates)

    if refine_examples:
        res.refine_examples = refine_examples
        update_discovery(discover_id, {
            "url": result.url,
            "timestamp": result.timestamp.isoformat(),
            "results": res.model_dump(),
            "errors": result.errors,
        })

    trace_store.add_action(discover_id, {
        "kind": "global-refine",
        "panel": "global",
        "mode": "preview-fragment-refined",
        "inputs": {
            "refine_examples": refine_examples,
            "candidate_counts": {
                "rss": len(res.rss_feeds),
                "api": len(res.api_endpoints),
                "embedded": len(res.embedded_json),
                "xpath": len(res.xpath_candidates),
                "graphql": len(res.graphql_operations),
            },
        },
        "provenance": {
            "method": (
                "Re-runs every candidate's preview. When refine_examples is set, "
                "fetches HTML once and runs XPath candidates in parallel against "
                "shared_html via _scrape_xpath_from_selector."
            ),
        },
    })

    def _render_preview(items, errors, warnings):
        total = len(items)
        fc = {
            "title":   sum(1 for it in items if it.title),
            "link":    sum(1 for it in items if it.link),
            "date":    sum(1 for it in items if it.timestamp),
            "content": sum(1 for it in items if it.content),
        }
        return templates.get_template("partials/preview_table.html").render(
            request=request,
            items=[it.model_dump() for it in items],
            total=total,
            field_counts=fc,
            errors=errors,
            warnings=warnings,
            refine_url=None,
        )

    response_data: dict[str, dict[str, str]] = {}

    if res.xpath_candidates:
        response_data["xpath"] = {}

        if refine_examples:
            # Fetch once, run all XPath candidates in parallel against shared HTML/selector.
            try:
                shared_html, shared_sel, _ = await fetch_and_parse(
                    result.url, services, timeout=30
                )
            except RuntimeError as exc:
                for idx in range(len(res.xpath_candidates)):
                    response_data["xpath"][str(idx)] = (
                        f'<div class="preview-error">Fetch failed: {str(exc)[:200]}</div>'
                    )
                return JSONResponse(response_data)

            async def _run_xpath_candidate(idx: int, c) -> tuple:
                selectors = ScrapeSelectors(
                    item=c.item_selector,
                    item_title=c.title_selector,
                    item_link=c.link_selector,
                    item_content=c.content_selector,
                    item_timestamp=c.timestamp_selector,
                    item_author=c.author_selector,
                    item_thumbnail=c.thumbnail_selector,
                    title_examples=refine_examples.get("title", []),
                    link_examples=refine_examples.get("link", []),
                    content_examples=refine_examples.get("content", []),
                    timestamp_examples=refine_examples.get("timestamp", []),
                    author_examples=refine_examples.get("author", []),
                    thumbnail_examples=refine_examples.get("thumbnail", []),
                )
                req = ScrapeRequest(
                    url=result.url,
                    strategy=FeedStrategy.XPATH,
                    selectors=selectors,
                    services=services,
                    adaptive=False,
                )
                try:
                    items, warnings, _ = await _scrape_xpath_from_selector(
                        req, shared_sel, shared_html
                    )
                    return idx, items[:10], [], warnings
                except Exception as exc:
                    return idx, [], [str(exc)[:200]], []

            tasks = [_run_xpath_candidate(idx, c) for idx, c in enumerate(res.xpath_candidates)]
            for idx, items, errors, warnings in await asyncio.gather(*tasks):
                response_data["xpath"][str(idx)] = _render_preview(items, errors, warnings)

        else:
            # No refine examples — one scrape per candidate (existing behaviour)
            for idx, c in enumerate(res.xpath_candidates):
                selectors = ScrapeSelectors(
                    item=c.item_selector,
                    item_title=c.title_selector,
                    item_link=c.link_selector,
                    item_content=c.content_selector,
                    item_timestamp=c.timestamp_selector,
                    item_author=c.author_selector,
                    item_thumbnail=c.thumbnail_selector,
                )
                req = ScrapeRequest(
                    url=result.url,
                    strategy=FeedStrategy.XPATH,
                    selectors=selectors,
                    services=services,
                    adaptive=False,
                )
                try:
                    scrape = await run_scrape(req)
                    response_data["xpath"][str(idx)] = _render_preview(
                        scrape.items[:10], scrape.errors, scrape.warnings
                    )
                except Exception as exc:
                    response_data["xpath"][str(idx)] = f'<div class="preview-error">{str(exc)[:200]}</div>'

    # Non-XPath types: skip when refine_examples is set (they don't benefit from text examples)
    if not refine_examples:
        if res.rss_feeds:
            response_data["rss"] = {}
            for idx, c in enumerate(res.rss_feeds):
                req = ScrapeRequest(
                    url=c.url, strategy=FeedStrategy.RSS, services=services, adaptive=False,
                )
                try:
                    scrape = await run_scrape(req)
                    response_data["rss"][str(idx)] = _render_preview(
                        scrape.items[:10], scrape.errors, scrape.warnings
                    )
                except Exception as exc:
                    response_data["rss"][str(idx)] = f'<div class="preview-error">{str(exc)[:200]}</div>'

        if res.api_endpoints:
            response_data["api"] = {}
            for idx, c in enumerate(res.api_endpoints):
                fm = c.field_mapping or {}
                req = ScrapeRequest(
                    url=c.url, strategy=FeedStrategy.JSON_API,
                    selectors=ScrapeSelectors(
                        item=c.item_path,
                        item_title=fm.get("title", ""),
                        item_link=fm.get("link", ""),
                        item_content=fm.get("content", ""),
                        item_timestamp=fm.get("timestamp", ""),
                        item_author=fm.get("author", ""),
                        item_thumbnail=fm.get("thumbnail", ""),
                    ),
                    method=c.method or "GET",
                    request_body=c.request_body or "",
                    request_headers=dict(c.request_headers or {}),
                    pagination=c.pagination,
                    max_pages=1,
                    services=services, adaptive=False,
                )
                try:
                    scrape = await run_scrape(req)
                    response_data["api"][str(idx)] = _render_preview(
                        scrape.items[:10], scrape.errors, scrape.warnings
                    )
                except Exception as exc:
                    response_data["api"][str(idx)] = f'<div class="preview-error">{str(exc)[:200]}</div>'

        if res.embedded_json:
            response_data["embedded"] = {}
            for idx, c in enumerate(res.embedded_json):
                req = ScrapeRequest(
                    url=result.url,
                    strategy=FeedStrategy.EMBEDDED_JSON,
                    selectors=ScrapeSelectors(item=c.path),
                    services=services,
                    adaptive=False,
                )
                try:
                    scrape = await run_scrape(req)
                    response_data["embedded"][str(idx)] = _render_preview(
                        scrape.items[:10], scrape.errors, scrape.warnings
                    )
                except Exception as exc:
                    response_data["embedded"][str(idx)] = f'<div class="preview-error">{str(exc)[:200]}</div>'

        if res.graphql_operations:
            response_data["graphql"] = {}
            for idx, gql_op in enumerate(res.graphql_operations):
                from app.discovery.field_mapper import auto_map_fields
                field_map = auto_map_fields(gql_op.sample_keys)
                req = ScrapeRequest(
                    url=gql_op.endpoint,
                    strategy=FeedStrategy.GRAPHQL,
                    graphql=gql_op,
                    selectors=ScrapeSelectors(
                        item_title=field_map.get("title", ""),
                        item_link=field_map.get("link", ""),
                        item_content=field_map.get("content", ""),
                        item_timestamp=field_map.get("timestamp", ""),
                        item_author=field_map.get("author", ""),
                    ),
                    services=services,
                    adaptive=False,
                )
                try:
                    scrape = await run_scrape(req)
                    response_data["graphql"][str(idx)] = _render_preview(
                        scrape.items[:10], scrape.errors, scrape.warnings
                    )
                except Exception as exc:
                    response_data["graphql"][str(idx)] = f'<div class="preview-error">{str(exc)[:200]}</div>'

    # Fix 1: signal client to reload when a new anchored candidate was prepended,
    # so the results page re-renders with it at the top. Also surface a soft
    # notice when anchoring failed despite the user supplying examples.
    if anchor_result is not None:
        response_data["anchored"] = {  # type: ignore[assignment]
            "reload": True,
            "item_selector": anchor_result.item_selector,
            "item_count": anchor_result.item_count,
            "confidence": anchor_result.confidence,
            "warnings": list(anchor_result.warnings),
        }
    elif refine_examples and anchor_error is None:
        backend = (stored.get("results") or {}).get("backend_used", "")
        js_hint = (
            " The page was fetched without a browser"
            f" (backend={backend}) — text that only appears after JavaScript"
            " runs won't be found. Retry discovery with browser rendering."
            if backend and backend not in ("bundled", "stealthy",
                                           "playwright_server", "browserless",
                                           "scrapling_serve")
            else ""
        )
        response_data["anchor_notice"] = {  # type: ignore[assignment]
            "message": (
                "Couldn't locate your examples on the page — refine applied to "
                "existing candidates only. Check spelling and that the text "
                "appears on the live site." + js_hint
            ),
        }
    elif anchor_error:
        response_data["anchor_notice"] = {"message": anchor_error}  # type: ignore[assignment]

    return JSONResponse(response_data)


@router.post("/llm-xpath/{discover_id}")
async def llm_xpath_hunt(discover_id: str, request: Request):
    """Ask the LLM to propose XPath selectors, explicitly forbidding RSS/JSON/GraphQL.

    Called when the user wants an XPath strategy and the LLM would otherwise
    return RSS. Returns a new XPathCandidate prepended to the candidate list
    and persisted in the discovery cache.
    """
    from app.services.discovery_cache import load_discovery, update_discovery, load_browser_html
    from app.models.schemas import DiscoverResponse, XPathCandidate
    from app.llm.analyzer import xpath_hunt
    from app.scraping.scrape import fetch_and_parse
    from scrapling import Selector

    services = _service_config()
    llm = _llm_config()
    if llm is None:
        return JSONResponse(
            {"error": "No LLM configured. Add an LLM in Settings first."},
            status_code=400,
        )

    stored = load_discovery(discover_id)
    if stored is None:
        return JSONResponse({"error": "Discovery result expired."}, status_code=400)

    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})

    # Prefer cached browser HTML.
    cached = load_browser_html(discover_id)
    if cached:
        html = cached
    else:
        try:
            html, _, _ = await fetch_and_parse(result.url, services, timeout=30)
        except RuntimeError as exc:
            return JSONResponse({"error": f"Fetch failed: {str(exc)[:200]}"}, status_code=502)

    html_skeleton = stored.get("results", {}).get("html_skeleton", "")

    llm_capture: dict = {}
    try:
        proposal = await xpath_hunt(result.url, html, html_skeleton, llm, capture=llm_capture)
    except RuntimeError as exc:
        trace_store.add_action(discover_id, {
            "kind": "llm-xpath-hunt",
            "panel": "global",
            "provenance": {"method": "analyzer.xpath_hunt (forced-XPath prompt, RSS/JSON/GraphQL forbidden)"},
            "inputs": {
                "html_source": "cached browser HTML" if cached else "fresh fetch_and_parse",
                "html_bytes": len(html),
                "html_skeleton_bytes": len(html_skeleton),
            },
            "llm_call": llm_capture,
            "error": str(exc),
        })
        return JSONResponse({"error": str(exc)}, status_code=502)

    item_sel = proposal.get("item_selector") or ""
    if not item_sel:
        trace_store.add_action(discover_id, {
            "kind": "llm-xpath-hunt",
            "panel": "global",
            "provenance": {"method": "analyzer.xpath_hunt"},
            "inputs": {
                "html_source": "cached browser HTML" if cached else "fresh fetch_and_parse",
                "html_bytes": len(html),
                "html_skeleton_bytes": len(html_skeleton),
            },
            "llm_call": llm_capture,
            "outputs": {"proposal": proposal},
            "error": "LLM did not return an item_selector.",
        })
        return JSONResponse({"error": "LLM did not return an item_selector."}, status_code=422)

    # Probe the proposed selector before returning.
    probe_count = 0
    try:
        from lxml.html import document_fromstring
        _doc = document_fromstring(html)
        probe_count = len(_doc.xpath(item_sel))
    except Exception:
        pass

    new_candidate = XPathCandidate(
        item_selector=item_sel,
        title_selector=proposal.get("title_selector") or "",
        link_selector=proposal.get("link_selector") or "",
        content_selector=proposal.get("content_selector") or "",
        timestamp_selector=proposal.get("timestamp_selector") or "",
        author_selector=proposal.get("author_selector") or "",
        thumbnail_selector=proposal.get("thumbnail_selector") or "",
        confidence=0.7 if probe_count >= 2 else 0.3,
        item_count=probe_count,
    )

    res = result.results
    existing_sels = {c.item_selector for c in res.xpath_candidates}
    if item_sel not in existing_sels:
        res.xpath_candidates.insert(0, new_candidate)
        update_discovery(discover_id, {
            "url": result.url,
            "timestamp": result.timestamp.isoformat(),
            "results": res.model_dump(),
            "errors": result.errors,
        })

    trace_store.add_action(discover_id, {
        "kind": "llm-xpath-hunt",
        "panel": "global",
        "provenance": {
            "method": "analyzer.xpath_hunt (forced-XPath prompt, RSS/JSON/GraphQL forbidden)",
            "html_source": "cached browser HTML" if cached else "fresh fetch_and_parse",
        },
        "inputs": {
            "html_bytes": len(html),
            "html_skeleton_bytes": len(html_skeleton),
        },
        "llm_call": llm_capture,
        "outputs": {
            "proposal": proposal,
            "probe_count": probe_count,
            "candidate_index": 0,
        },
    })
    return JSONResponse({
        "item_selector": item_sel,
        "probe_count": probe_count,
        "reasoning": proposal.get("reasoning", ""),
        "candidate_index": 0,
        "reload": True,
    })


@router.post("/candidate-refine")
async def candidate_refine(request: Request):
    """Per-candidate refine with three modes: examples, llm, xpath."""
    from app.services.discovery_cache import load_discovery, update_discovery
    from app.models.schemas import DiscoverResponse, FeedStrategy, ScrapeRequest, ScrapeSelectors
    from app.scraping.scrape import run_scrape

    form = await request.form()
    discover_id = str(form.get("discover_id", "")).strip()
    index = int(form.get("index", 0))
    mode = str(form.get("mode", "examples")).strip()
    services = _service_config()

    stored = load_discovery(discover_id)
    if stored is None:
        return JSONResponse({"error": "Discovery result expired."}, status_code=400)

    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    res = result.results

    if index >= len(res.xpath_candidates):
        return JSONResponse({"error": "Invalid candidate index."}, status_code=400)

    c = res.xpath_candidates[index]

    async def _get_html_for_refine():
        """Return (html, sel) for refine anchoring.

        Preference order:
          1. Cached browser HTML (for discoveries that ran a browser backend).
          2. The raw_html artifact stored at discovery time — this is the exact
             bytes the skeleton/class-inventory were built from, so if the user
             sees their example in the UI's transparency panels it will match
             here too.
          3. Fresh fetch_and_parse as a last resort. Re-fetching can return a
             different page (anti-bot challenge, different JS branch), which
             is what produced the "None of your examples could be located"
             errors on http-mode discoveries.
        """
        from scrapling import Selector
        from app.services.discovery_cache import load_browser_html
        from app.scraping.scrape import fetch_and_parse
        cached = load_browser_html(discover_id)
        if cached:
            return cached, Selector(cached)
        artifact = trace_store.get_artifact(discover_id, "raw_html")
        if artifact and artifact.get("content"):
            content = artifact["content"]
            return content, Selector(content)
        html, sel, _ = await fetch_and_parse(result.url, services, timeout=30)
        return html, sel

    def _persist_candidate():
        if res.candidate_refinements is None:
            res.candidate_refinements = {}
        res.candidate_refinements[str(index)] = {
            "item_selector":      c.item_selector,
            "title_selector":     c.title_selector,
            "link_selector":      c.link_selector,
            "content_selector":   c.content_selector,
            "timestamp_selector": c.timestamp_selector,
            "author_selector":    c.author_selector,
            "thumbnail_selector": c.thumbnail_selector,
        }
        update_discovery(discover_id, {
            "url": result.url,
            "timestamp": result.timestamp.isoformat(),
            "results": res.model_dump(),
            "errors": result.errors,
        })

    def _render_preview_json(items, errors, warnings):
        total = len(items)
        fc = {
            "title":   sum(1 for it in items if it.title),
            "link":    sum(1 for it in items if it.link),
            "date":    sum(1 for it in items if it.timestamp),
            "content": sum(1 for it in items if it.content),
        }
        html = templates.get_template("partials/preview_table.html").render(
            request=request,
            items=[it.model_dump() for it in items],
            total=total,
            field_counts=fc,
            errors=errors,
            warnings=warnings,
            refine_url=None,
        )
        return JSONResponse({
            "preview_html": html,
            "selectors": {
                "item_selector":      c.item_selector,
                "title_selector":     c.title_selector,
                "link_selector":      c.link_selector,
                "content_selector":   c.content_selector,
                "timestamp_selector": c.timestamp_selector,
                "author_selector":    c.author_selector,
                "thumbnail_selector": c.thumbnail_selector,
            },
            "warnings": warnings,
        })

    selectors_before = {
        "item_selector":      c.item_selector,
        "title_selector":     c.title_selector,
        "link_selector":      c.link_selector,
        "content_selector":   c.content_selector,
        "timestamp_selector": c.timestamp_selector,
        "author_selector":    c.author_selector,
        "thumbnail_selector": c.thumbnail_selector,
    }

    def _selectors_after():
        return {
            "item_selector":      c.item_selector,
            "title_selector":     c.title_selector,
            "link_selector":      c.link_selector,
            "content_selector":   c.content_selector,
            "timestamp_selector": c.timestamp_selector,
            "author_selector":    c.author_selector,
            "thumbnail_selector": c.thumbnail_selector,
        }

    # ── mode: xpath ───────────────────────────────────────────────────────────
    if mode == "xpath":
        def _merge_union(f1: str, f2: str) -> str:
            f1, f2 = (f1 or "").strip(), (f2 or "").strip()
            return f"({f1}) | ({f2})" if f1 and f2 else f1 or f2

        c.item_selector      = (form.get("item_selector") or "").strip()
        c.title_selector     = _merge_union(form.get("title_selector", ""),     form.get("title_selector_2", ""))
        c.link_selector      = _merge_union(form.get("link_selector", ""),      form.get("link_selector_2", ""))
        c.content_selector   = _merge_union(form.get("content_selector", ""),   form.get("content_selector_2", ""))
        c.timestamp_selector = _merge_union(form.get("timestamp_selector", ""), form.get("timestamp_selector_2", ""))
        c.author_selector    = _merge_union(form.get("author_selector", ""),    form.get("author_selector_2", ""))
        c.thumbnail_selector = _merge_union(form.get("thumbnail_selector", ""), form.get("thumbnail_selector_2", ""))
        _persist_candidate()

        selectors = ScrapeSelectors(
            item=c.item_selector,
            item_title=c.title_selector,
            item_link=c.link_selector,
            item_content=c.content_selector,
            item_timestamp=c.timestamp_selector,
            item_author=c.author_selector,
            item_thumbnail=c.thumbnail_selector,
        )
        req = ScrapeRequest(
            url=result.url, strategy=FeedStrategy.XPATH,
            selectors=selectors, services=services, adaptive=False,
        )
        try:
            scrape = await run_scrape(req)
            trace_store.add_action(discover_id, {
                "kind": "candidate-refine",
                "panel": f"xpath:{index}",
                "mode": "xpath",
                "provenance": {
                    "method": "Manual XPath edit (advanced). Two-value fields merged via (A) | (B) union.",
                },
                "inputs": {
                    "form": {k: v for k, v in form.multi_items() if k != "discover_id"},
                    "selectors_before": selectors_before,
                },
                "outputs": {
                    "selectors_after": _selectors_after(),
                    "item_count": len(scrape.items),
                    "warnings": list(scrape.warnings),
                    "errors": list(scrape.errors),
                },
            })
            return _render_preview_json(scrape.items[:10], scrape.errors, scrape.warnings)
        except Exception as exc:
            trace_store.add_action(discover_id, {
                "kind": "candidate-refine", "panel": f"xpath:{index}", "mode": "xpath",
                "inputs": {"selectors_before": selectors_before},
                "error": str(exc),
            })
            return JSONResponse({"error": f"Preview failed: {str(exc)[:200]}"}, status_code=500)

    # ── mode: llm ─────────────────────────────────────────────────────────────
    if mode == "llm":
        from app.llm.analyzer import recommend_candidate_selectors
        from app.scraping.scrape import _scrape_xpath_from_selector
        from lxml import etree as _lxml_etree

        refine_examples: dict[str, list[str]] = {}
        for role in ("title", "link", "content", "timestamp", "author", "thumbnail"):
            val = str(form.get(f"{role}_example", "") or "").strip()
            if val:
                refine_examples[role] = [val]

        llm = _llm_config()
        if llm is None:
            return JSONResponse(
                {"error": "No LLM configured. Add an LLM in Settings first."},
                status_code=400,
            )

        # Prefer cached browser HTML so the LLM sees the same DOM the user saw.
        try:
            html, sel = await _get_html_for_refine()
        except RuntimeError as exc:
            return JSONResponse({"error": f"Fetch failed: {str(exc)[:200]}"}, status_code=502)

        llm_capture: dict = {}
        try:
            improved = await recommend_candidate_selectors(
                url=result.url,
                candidate=c,
                html_skeleton=stored.get("results", {}).get("html_skeleton", ""),
                llm=llm,
                refine_examples=refine_examples or None,
                raw_html=html,
                capture=llm_capture,
            )
        except RuntimeError as exc:
            trace_store.add_action(discover_id, {
                "kind": "candidate-refine",
                "panel": f"xpath:{index}",
                "mode": "llm",
                "provenance": {
                    "method": "analyzer.recommend_candidate_selectors (LLM-only mode; may change item_selector)",
                    "html_source": "cached browser HTML" if trace_store.get_artifact(discover_id, "browser_html") else "fresh fetch_and_parse",
                },
                "inputs": {
                    "refine_examples": refine_examples,
                    "html_bytes": len(html),
                    "selectors_before": selectors_before,
                },
                "llm_call": llm_capture,
                "error": str(exc),
            })
            return JSONResponse({"error": str(exc)}, status_code=502)

        reasoning = improved.pop("reasoning", "") or ""

        # Validate LLM-proposed item_selector before applying.
        new_item_sel = improved.get("item_selector")
        item_sel_warning = None
        if new_item_sel:
            try:
                _lxml_etree.XPath(new_item_sel)
                c.item_selector = new_item_sel
            except _lxml_etree.XPathSyntaxError as exc:
                item_sel_warning = f"LLM proposed invalid item_selector ({exc}); keeping original."

        c.title_selector     = improved.get("title_selector")     or c.title_selector
        c.link_selector      = improved.get("link_selector")      or c.link_selector
        c.content_selector   = improved.get("content_selector")   or c.content_selector
        c.timestamp_selector = improved.get("timestamp_selector") or c.timestamp_selector
        c.author_selector    = improved.get("author_selector")    or c.author_selector
        c.thumbnail_selector = improved.get("thumbnail_selector") or c.thumbnail_selector
        _persist_candidate()

        selectors = ScrapeSelectors(
            item=c.item_selector,
            item_title=c.title_selector,
            item_link=c.link_selector,
            item_content=c.content_selector,
            item_timestamp=c.timestamp_selector,
            item_author=c.author_selector,
            item_thumbnail=c.thumbnail_selector,
        )
        req = ScrapeRequest(
            url=result.url, strategy=FeedStrategy.XPATH,
            selectors=selectors, services=services, adaptive=False,
        )
        items, warnings, _ = await _scrape_xpath_from_selector(req, sel, html)
        if item_sel_warning:
            warnings = [item_sel_warning] + list(warnings)
        trace_store.add_action(discover_id, {
            "kind": "candidate-refine",
            "panel": f"xpath:{index}",
            "mode": "llm",
            "provenance": {
                "method": "analyzer.recommend_candidate_selectors (LLM-only; may change item_selector)",
            },
            "inputs": {
                "refine_examples": refine_examples,
                "html_bytes": len(html),
                "selectors_before": selectors_before,
            },
            "llm_call": llm_capture,
            "outputs": {
                "improved_raw": improved,
                "reasoning": reasoning,
                "selectors_after": _selectors_after(),
                "item_count": len(items),
                "warnings": list(warnings),
            },
        })
        resp = _render_preview_json(items[:10], [], warnings)
        # Attach reasoning to the response body.
        import json as _json
        body = _json.loads(resp.body)
        body["reasoning"] = reasoning
        return JSONResponse(body)

    # ── mode: multi (deterministic LCA, no LLM) ──────────────────────────────────
    if mode == "multi":
        from app.discovery.multi_field_anchor import decode_example_rows, find_items_from_rows
        from app.scraping.scrape import _scrape_xpath_from_selector

        rows = decode_example_rows(form)
        if not rows:
            return JSONResponse(
                {"error": "Provide at least one example field (title, date, link, etc.)."},
                status_code=400,
            )

        try:
            html, sel = await _get_html_for_refine()
        except RuntimeError as exc:
            return JSONResponse({"error": f"Fetch failed: {str(exc)[:200]}"}, status_code=502)

        outcome = find_items_from_rows(html, rows)
        if outcome is None:
            backend = stored.get("results", {}).get("backend_used", "")
            js_hint = (
                " The page was fetched with a non-browser backend"
                f" ({backend}) — text that only appears after JavaScript"
                " runs won't be found. Retry discovery with Browser rendering."
                if backend and backend not in ("browser", "bundled", "stealthy",
                                               "playwright_server", "browserless",
                                               "scrapling_serve")
                else ""
            )
            return JSONResponse({
                "error": (
                    "None of your examples could be located on the rendered page. "
                    "Check spelling and confirm the text appears on the live site."
                    + js_hint
                )
            }, status_code=422)

        c.item_selector = outcome.item_selector
        for role, rel_sel in outcome.field_selectors.items():
            if role in ("title", "link", "content", "timestamp", "author", "thumbnail"):
                setattr(c, f"{role}_selector", rel_sel)
        _persist_candidate()

        selectors = ScrapeSelectors(
            item=c.item_selector,
            item_title=c.title_selector,
            item_link=c.link_selector,
            item_content=c.content_selector,
            item_timestamp=c.timestamp_selector,
            item_author=c.author_selector,
            item_thumbnail=c.thumbnail_selector,
        )
        req = ScrapeRequest(
            url=result.url, strategy=FeedStrategy.XPATH,
            selectors=selectors, services=services, adaptive=False,
        )
        items, warnings, _ = await _scrape_xpath_from_selector(req, sel, html)
        all_warnings = list(outcome.warnings) + list(warnings)
        trace_store.add_action(discover_id, {
            "kind": "candidate-refine",
            "panel": f"xpath:{index}",
            "mode": "multi",
            "provenance": {
                "method": "multi_field_anchor.find_items_from_rows (deterministic LCA from example rows; no LLM)",
            },
            "inputs": {
                "rows": rows,
                "html_bytes": len(html),
                "selectors_before": selectors_before,
            },
            "outputs": {
                "lca_outcome": {
                    "item_selector": outcome.item_selector,
                    "field_selectors": outcome.field_selectors,
                    "confidence": outcome.confidence,
                    "item_count": outcome.item_count,
                    "warnings": list(outcome.warnings),
                },
                "selectors_after": _selectors_after(),
                "item_count": len(items),
                "warnings": all_warnings,
            },
        })
        return _render_preview_json(items[:10], [], all_warnings)

    # ── mode: smart (LCA + optional LLM polish) ───────────────────────────────
    if mode == "smart":
        from app.discovery.multi_field_anchor import decode_example_rows, find_items_from_rows
        from app.llm.analyzer import refine_with_item_samples
        from app.scraping.scrape import _scrape_xpath_from_selector
        from lxml import etree as _lxml_etree

        rows = decode_example_rows(form)
        if not rows:
            return JSONResponse(
                {"error": "Provide at least one example field (title, date, link, etc.)."},
                status_code=400,
            )

        try:
            html, sel = await _get_html_for_refine()
        except RuntimeError as exc:
            return JSONResponse({"error": f"Fetch failed: {str(exc)[:200]}"}, status_code=502)

        outcome = find_items_from_rows(html, rows)
        if outcome is None:
            backend = stored.get("results", {}).get("backend_used", "")
            js_hint = (
                " The page was fetched with a non-browser backend"
                f" ({backend}) — text that only appears after JavaScript"
                " runs won't be found. Retry discovery with Browser rendering."
                if backend and backend not in ("browser", "bundled", "stealthy",
                                               "playwright_server", "browserless",
                                               "scrapling_serve")
                else ""
            )
            return JSONResponse({
                "error": (
                    "None of your examples could be located on the rendered page. "
                    "Check spelling and confirm the text appears on the live site."
                    + js_hint
                )
            }, status_code=422)

        c.item_selector = outcome.item_selector
        for role, rel_sel in outcome.field_selectors.items():
            if role in ("title", "link", "content", "timestamp", "author", "thumbnail"):
                setattr(c, f"{role}_selector", rel_sel)
        _persist_candidate()

        reasoning = ""
        llm = _llm_config()
        llm_capture: dict = {}
        improved_raw: dict | None = None
        if llm is not None and outcome.item_outer_htmls:
            flat_examples = rows[0] if rows else {}
            try:
                improved = await refine_with_item_samples(
                    url=result.url,
                    candidate=c,
                    item_outer_htmls=outcome.item_outer_htmls,
                    examples=flat_examples,
                    llm=llm,
                    capture=llm_capture,
                )
                improved_raw = dict(improved)
                reasoning = improved.pop("reasoning", "") or ""
                for role in ("title", "link", "content", "timestamp", "author", "thumbnail"):
                    key = f"{role}_selector"
                    proposed = improved.get(key)
                    if proposed:
                        try:
                            _lxml_etree.XPath(proposed)
                            setattr(c, key, proposed)
                        except Exception:
                            pass
                _persist_candidate()
            except RuntimeError:
                pass

        selectors = ScrapeSelectors(
            item=c.item_selector,
            item_title=c.title_selector,
            item_link=c.link_selector,
            item_content=c.content_selector,
            item_timestamp=c.timestamp_selector,
            item_author=c.author_selector,
            item_thumbnail=c.thumbnail_selector,
        )
        req = ScrapeRequest(
            url=result.url, strategy=FeedStrategy.XPATH,
            selectors=selectors, services=services, adaptive=False,
        )
        items, warnings, _ = await _scrape_xpath_from_selector(req, sel, html)
        all_warnings = list(outcome.warnings) + list(warnings)
        trace_store.add_action(discover_id, {
            "kind": "candidate-refine",
            "panel": f"xpath:{index}",
            "mode": "smart",
            "provenance": {
                "method": (
                    "find_items_from_rows (LCA) → analyzer.refine_with_item_samples "
                    "(LLM polish of field selectors against real item outerHTML)."
                ),
            },
            "inputs": {
                "rows": rows,
                "html_bytes": len(html),
                "selectors_before": selectors_before,
                "lca_outcome": {
                    "item_selector": outcome.item_selector,
                    "field_selectors": outcome.field_selectors,
                    "confidence": outcome.confidence,
                    "item_count": outcome.item_count,
                    "item_outer_htmls": outcome.item_outer_htmls,
                    "warnings": list(outcome.warnings),
                },
            },
            "llm_call": llm_capture,
            "outputs": {
                "improved_raw": improved_raw,
                "reasoning": reasoning,
                "selectors_after": _selectors_after(),
                "item_count": len(items),
                "warnings": all_warnings,
            },
        })
        resp = _render_preview_json(items[:10], [], all_warnings)
        if reasoning:
            import json as _json
            body = _json.loads(resp.body)
            body["reasoning"] = reasoning
            return JSONResponse(body)
        return resp

    # ── mode: reanchor ────────────────────────────────────────────────────────
    if mode == "reanchor":
        from app.discovery.example_anchored import find_item_selectors_from_example
        from app.scraping.scrape import _scrape_xpath_from_selector

        anchor = ""
        for role in ("title", "link", "content"):
            v = str(form.get(f"{role}_example", "") or "").strip()
            if v:
                anchor = v
                break
        if not anchor:
            return JSONResponse(
                {"error": "Provide at least one example (title, link, or content) to re-anchor."},
                status_code=400,
            )

        try:
            html, sel = await _get_html_for_refine()
        except RuntimeError as exc:
            return JSONResponse({"error": f"Fetch failed: {str(exc)[:200]}"}, status_code=502)

        new_selectors = find_item_selectors_from_example(html, anchor)
        if not new_selectors:
            return JSONResponse({
                "error": (
                    f"Couldn't find '{anchor[:40]}...' on the page. "
                    "Is the example text exactly as shown on the live site?"
                )
            }, status_code=422)

        c.item_selector = new_selectors[0]
        _persist_candidate()

        selectors = ScrapeSelectors(
            item=c.item_selector,
            item_title=c.title_selector,
            item_link=c.link_selector,
            item_content=c.content_selector,
            item_timestamp=c.timestamp_selector,
            item_author=c.author_selector,
            item_thumbnail=c.thumbnail_selector,
        )
        req = ScrapeRequest(
            url=result.url, strategy=FeedStrategy.XPATH,
            selectors=selectors, services=services, adaptive=False,
        )
        items, warnings, _ = await _scrape_xpath_from_selector(req, sel, html)
        trace_store.add_action(discover_id, {
            "kind": "candidate-refine",
            "panel": f"xpath:{index}",
            "mode": "reanchor",
            "provenance": {
                "method": "example_anchored.find_item_selectors_from_example (first example text used as anchor)",
            },
            "inputs": {
                "anchor": anchor,
                "html_bytes": len(html),
                "selectors_before": selectors_before,
            },
            "outputs": {
                "new_item_selectors": new_selectors,
                "selectors_after": _selectors_after(),
                "item_count": len(items),
                "warnings": list(warnings),
            },
        })
        return _render_preview_json(items[:10], [], warnings)

    # ── mode: examples (default) ──────────────────────────────────────────────
    from app.scraping.scrape import _scrape_xpath_from_selector

    examples = {
        role: str(form.get(f"{role}_example", "") or "").strip()
        for role in ("title", "link", "content", "timestamp", "author", "thumbnail")
    }
    examples_lists = {k: [v] for k, v in examples.items() if v}

    selectors = ScrapeSelectors(
        item=c.item_selector,
        item_title=c.title_selector,
        item_link=c.link_selector,
        item_content=c.content_selector,
        item_timestamp=c.timestamp_selector,
        item_author=c.author_selector,
        item_thumbnail=c.thumbnail_selector,
        title_examples=examples_lists.get("title", []),
        link_examples=examples_lists.get("link", []),
        content_examples=examples_lists.get("content", []),
        timestamp_examples=examples_lists.get("timestamp", []),
        author_examples=examples_lists.get("author", []),
        thumbnail_examples=examples_lists.get("thumbnail", []),
    )
    req = ScrapeRequest(
        url=result.url, strategy=FeedStrategy.XPATH,
        selectors=selectors, services=services, adaptive=False,
    )

    try:
        html, sel = await _get_html_for_refine()
    except RuntimeError as exc:
        return JSONResponse({"error": f"Fetch failed: {str(exc)[:200]}"}, status_code=502)

    items, warnings, updated_sel = await _scrape_xpath_from_selector(req, sel, html)

    # If existing item_selector yielded nothing, try re-anchoring from example.
    if not items and examples_lists:
        from app.discovery.example_anchored import find_item_selectors_from_example
        anchor = next(iter(examples_lists.get("title") or examples_lists.get("link") or []), "")
        if anchor:
            new_selectors = find_item_selectors_from_example(html, anchor)
            if new_selectors:
                anchor_role = "title" if examples_lists.get("title") else "link"
                warnings = list(warnings) + [
                    f"Original selector matched 0 items. Re-anchored to {new_selectors[0]} "
                    f"using your '{anchor_role}' example."
                ]
                c.item_selector = new_selectors[0]
                _persist_candidate()
                selectors = ScrapeSelectors(
                    item=c.item_selector,
                    item_title=c.title_selector,
                    item_link=c.link_selector,
                    item_content=c.content_selector,
                    item_timestamp=c.timestamp_selector,
                    item_author=c.author_selector,
                    item_thumbnail=c.thumbnail_selector,
                    title_examples=examples_lists.get("title", []),
                    link_examples=examples_lists.get("link", []),
                    content_examples=examples_lists.get("content", []),
                    timestamp_examples=examples_lists.get("timestamp", []),
                    author_examples=examples_lists.get("author", []),
                    thumbnail_examples=examples_lists.get("thumbnail", []),
                )
                req = ScrapeRequest(
                    url=result.url, strategy=FeedStrategy.XPATH,
                    selectors=selectors, services=services, adaptive=False,
                )
                items, warnings2, updated_sel = await _scrape_xpath_from_selector(req, sel, html)
                warnings = warnings + list(warnings2)

    c.title_selector     = updated_sel.item_title     or c.title_selector
    c.link_selector      = updated_sel.item_link      or c.link_selector
    c.content_selector   = updated_sel.item_content   or c.content_selector
    c.timestamp_selector = updated_sel.item_timestamp or c.timestamp_selector
    c.author_selector    = updated_sel.item_author    or c.author_selector
    c.thumbnail_selector = updated_sel.item_thumbnail or c.thumbnail_selector
    _persist_candidate()

    trace_store.add_action(discover_id, {
        "kind": "candidate-refine",
        "panel": f"xpath:{index}",
        "mode": "examples",
        "provenance": {
            "method": (
                "_scrape_xpath_from_selector with per-field example text → rule_builder.recover_selector "
                "uses fuzzy-match to replace selectors that miss. Falls back to example_anchored."
            ),
        },
        "inputs": {
            "examples": examples_lists,
            "html_bytes": len(html),
            "selectors_before": selectors_before,
        },
        "outputs": {
            "selectors_after": _selectors_after(),
            "item_count": len(items),
            "warnings": list(warnings),
        },
    })
    return _render_preview_json(items[:10], [], warnings)


# ── Save ─────────────────────────────────────────────────────────────────────

@router.post("/save")
async def save(request: Request) -> RedirectResponse:
    from app.models.schemas import FeedCadence, FeedStrategy, ScrapeRequest, ScrapeSelectors
    from app.scraping.config_store import save_config
    from app.ui.feeds_store import get_feeds_store

    form = await request.form()

    def f(key: str) -> str:
        return str(form.get(key, "")).strip()

    strategy = f("strategy")
    name = f("name") or "Untitled Feed"
    source_url = f("source_url")
    cadence = f("cadence") or FeedCadence.DAILY.value
    fetch_backend_override = f("fetch_backend_override")
    llm_suggested = f("llm_suggested") == "1"
    sidecar_base = os.getenv("AUTOFEED_PUBLIC_URL", "http://autofeed-sidecar:8000")
    services = _service_config()

    _shared = dict(
        cadence=cadence,
        fetch_backend_override=fetch_backend_override,
        llm_suggested=llm_suggested,
    )

    try:
        def _register_new_feed(feed_id: str) -> None:
            from app.main import _scheduler
            from app.scheduler.runner import register_feed
            if _scheduler is not None and feed_id:
                feed_record = get_feeds_store().get(feed_id)
                if feed_record:
                    register_feed(_scheduler, feed_record)

        if strategy == "rss":
            feed_url = f("url")
            if not feed_url:
                raise ValueError("Missing feed URL")
            get_feeds_store().add(
                name=name,
                strategy="rss",
                source_url=source_url or feed_url,
                feed_url=feed_url,
                **_shared,
            )
        elif strategy == "json_api":
            import json as _json
            from app.models.schemas import PaginationSpec as _PaginationSpec
            url = f("url")
            try:
                req_headers = _json.loads(f("request_headers_json") or "{}") or {}
            except ValueError:
                req_headers = {}
            pagination = None
            pag_param = f("pagination_param")
            if pag_param:
                try:
                    per_page = int(f("pagination_per_page") or "0")
                except ValueError:
                    per_page = 0
                try:
                    start = int(f("pagination_start") or "1")
                except ValueError:
                    start = 1
                pagination = _PaginationSpec(
                    location=f("pagination_location") or "body",
                    param=pag_param,
                    kind=f("pagination_kind") or "page",
                    start=start,
                    per_page=per_page,
                    per_page_param=f("pagination_per_page_param"),
                    has_more_path=f("pagination_has_more_path"),
                    next_cursor_path=f("pagination_next_cursor_path"),
                    total_pages_path=f("pagination_total_pages_path"),
                )
            try:
                max_pages = max(1, min(50, int(f("max_pages") or "1")))
            except ValueError:
                max_pages = 1
            try:
                max_items = max(1, min(5000, int(f("max_items") or "250")))
            except ValueError:
                max_items = 250
            req = ScrapeRequest(
                url=url,
                strategy=FeedStrategy.JSON_API,
                selectors=ScrapeSelectors(
                    item=f("item_path"),
                    item_title=f("item_title"),
                    item_link=f("item_link"),
                    item_content=f("item_content"),
                    item_timestamp=f("item_timestamp"),
                ),
                method=(f("method") or "GET").upper(),
                request_body=f("request_body"),
                request_headers=req_headers,
                pagination=pagination,
                max_pages=max_pages,
                max_items=max_items,
                services=services,
                adaptive=False,
            )
            config_id = save_config(
                "scrape",
                req.model_dump(),
                post_process=lambda cid, p: {**p, "cache_key": cid},
            )
            feed_id = get_feeds_store().add(
                name=name,
                strategy="json_api",
                source_url=source_url or url,
                feed_url=f"{sidecar_base}/scrape/feed?id={config_id}",
                config_id=config_id,
                **_shared,
            )
            _register_new_feed(feed_id)
        elif strategy == "xpath":
            if not source_url:
                raise ValueError("Missing source URL for XPath strategy")
            req = ScrapeRequest(
                url=source_url,
                strategy=FeedStrategy.XPATH,
                selectors=ScrapeSelectors(
                    item=f("item_selector"),
                    item_title=f("title_selector"),
                    item_link=f("link_selector"),
                    item_content=f("content_selector"),
                    item_timestamp=f("timestamp_selector"),
                    title_example=f("title_example"),
                    link_example=f("link_example"),
                    content_example=f("content_example"),
                    timestamp_example=f("timestamp_example"),
                ),
                services=services,
                adaptive=False,
            )
            config_id = save_config(
                "scrape",
                req.model_dump(),
                post_process=lambda cid, p: {**p, "cache_key": cid},
            )
            feed_id = get_feeds_store().add(
                name=name,
                strategy="xpath",
                source_url=source_url,
                feed_url=f"{sidecar_base}/scrape/feed?id={config_id}",
                config_id=config_id,
                **_shared,
            )
            _register_new_feed(feed_id)
        elif strategy == "embedded_json":
            if not source_url:
                raise ValueError("Missing source URL for embedded JSON strategy")
            req = ScrapeRequest(
                url=source_url,
                strategy=FeedStrategy.EMBEDDED_JSON,
                selectors=ScrapeSelectors(
                    item=f("path"),
                    item_title=f("item_title"),
                    item_link=f("item_link"),
                    item_content=f("item_content"),
                    item_timestamp=f("item_timestamp"),
                ),
                services=services,
                adaptive=False,
            )
            config_id = save_config(
                "scrape",
                req.model_dump(),
                post_process=lambda cid, p: {**p, "cache_key": cid},
            )
            feed_id = get_feeds_store().add(
                name=name,
                strategy="embedded_json",
                source_url=source_url,
                feed_url=f"{sidecar_base}/scrape/feed?id={config_id}",
                config_id=config_id,
                **_shared,
            )
            _register_new_feed(feed_id)
        elif strategy == "graphql":
            import json as _json
            from app.models.schemas import GraphQLOperation
            op = GraphQLOperation(
                endpoint=f("graphql_endpoint"),
                operation_name=f("operation_name"),
                query=f("query"),
                variables=_json.loads(f("variables") or "{}"),
                response_path=f("response_path"),
            )
            req = ScrapeRequest(
                url=op.endpoint,
                strategy=FeedStrategy.GRAPHQL,
                graphql=op,
                selectors=ScrapeSelectors(
                    item_title=f("item_title"),
                    item_link=f("item_link"),
                    item_content=f("item_content"),
                    item_timestamp=f("item_timestamp"),
                ),
                services=services,
                adaptive=False,
            )
            config_id = save_config(
                "scrape",
                req.model_dump(),
                post_process=lambda cid, p: {**p, "cache_key": cid},
            )
            feed_id = get_feeds_store().add(
                name=name,
                strategy="graphql",
                source_url=source_url or op.endpoint,
                feed_url=f"{sidecar_base}/scrape/feed?id={config_id}",
                config_id=config_id,
                **_shared,
            )
            _register_new_feed(feed_id)
        else:
            request.session["flash"] = {
                "type": "error",
                "message": f"Unknown strategy: {strategy}",
            }
            return RedirectResponse("/", status_code=303)

        request.session["flash"] = {"type": "success", "message": f"Feed saved: {name}"}
        return RedirectResponse("/feeds", status_code=303)

    except Exception as exc:
        request.session["flash"] = {
            "type": "error",
            "message": f"Failed to save feed: {str(exc)[:200]}",
        }
        return RedirectResponse("/", status_code=303)


# ── Feeds ─────────────────────────────────────────────────────────────────────

@router.get("/feeds", response_class=HTMLResponse)
async def feeds_list(request: Request) -> HTMLResponse:
    from app.ui.feeds_store import get_feeds_store
    all_feeds = get_feeds_store().all()
    return templates.TemplateResponse(
        request,
        "feeds.html",
        _ctx(request, "Saved Feeds", feeds=all_feeds),
    )


@router.post("/feeds/{feed_id}/delete")
async def feed_delete(request: Request, feed_id: str) -> RedirectResponse:
    from app.ui.feeds_store import get_feeds_store
    store = get_feeds_store()
    deleted = store.delete(feed_id)
    if deleted:
        from app.main import _scheduler
        if _scheduler is not None:
            from app.scheduler.runner import unregister_feed
            unregister_feed(_scheduler, feed_id)
        request.session["flash"] = {"type": "success", "message": "Feed deleted."}
    else:
        request.session["flash"] = {"type": "error", "message": "Feed not found."}
    return RedirectResponse("/feeds", status_code=303)


@router.get("/analyze-apply/{feed_id}", response_class=HTMLResponse, response_model=None)
async def analyze_apply(request: Request, feed_id: str):
    """Show the pending LLM re-analysis for a drifted feed — user reviews before applying."""
    from app.ui.feeds_store import get_feeds_store
    from app.models.schemas import LLMRecommendation

    store = get_feeds_store()
    feed = store.get(feed_id)
    if feed is None:
        return templates.TemplateResponse(
            request, "discover_not_found.html",
            _ctx(request, "Feed not found", discover_id=""),
            status_code=404,
        )
    pending = feed.get("pending_llm_update")
    if not pending:
        request.session["flash"] = {"type": "info", "message": "No pending analysis for this feed."}
        return RedirectResponse("/feeds", status_code=303)

    try:
        rec = LLMRecommendation.model_validate(pending)
    except Exception:
        request.session["flash"] = {"type": "error", "message": "Could not parse pending analysis."}
        return RedirectResponse("/feeds", status_code=303)

    return templates.TemplateResponse(
        request,
        "analyze_apply.html",
        _ctx(
            request, f"Review analysis — {feed.get('name', feed_id)}",
            feed=feed, recommendation=rec.model_dump(),
        ),
    )


@router.post("/feeds/{feed_id}/dismiss-update")
async def feed_dismiss_update(request: Request, feed_id: str) -> RedirectResponse:
    from app.ui.feeds_store import get_feeds_store
    get_feeds_store().update(feed_id, pending_llm_update=None)
    request.session["flash"] = {"type": "success", "message": "Pending analysis dismissed."}
    return RedirectResponse("/feeds", status_code=303)


@router.post("/feeds/{feed_id}/backend")
async def feed_set_backend(request: Request, feed_id: str) -> RedirectResponse:
    """Change the fetch backend override for a saved feed.

    The scheduler and /scrape/feed both honour feed.fetch_backend_override, so
    this single write retargets both the scheduled refresh and live requests.
    Invalidates the cached Atom so the next fetch uses the new backend.
    """
    from app.ui.feeds_store import get_feeds_store

    valid = {"", "bundled", "playwright_server", "browserless", "scrapling_serve", "stealthy"}
    form = await request.form()
    override = str(form.get("fetch_backend_override", "")).strip()
    if override not in valid:
        request.session["flash"] = {"type": "error", "message": f"Unknown backend: {override}"}
        return RedirectResponse("/feeds", status_code=303)

    store = get_feeds_store()
    feed = store.get(feed_id)
    if feed is None:
        request.session["flash"] = {"type": "error", "message": "Feed not found."}
        return RedirectResponse("/feeds", status_code=303)

    store.update(feed_id, fetch_backend_override=override)

    cached = feed.get("cached_atom_path", "")
    if cached:
        try:
            from pathlib import Path
            Path(cached).unlink(missing_ok=True)
        except Exception:
            pass

    label = override or "(default)"
    request.session["flash"] = {"type": "success", "message": f"Backend set to {label}. Next refresh will use it."}
    return RedirectResponse("/feeds", status_code=303)


def _build_edit_scrape_request(
    feed: dict, form_values: dict, services
):
    """Reconstruct a ScrapeRequest from the recipe-editor form for the given feed.

    *form_values* is a plain {name: str} dict (caller flattened the form data).
    Returns (req, error). `error` is non-empty when the form is malformed."""
    import json as _json
    from app.models.schemas import (
        FeedStrategy, PaginationSpec, ScrapeRequest, ScrapeSelectors,
    )

    strategy = feed.get("strategy", "")
    url = form_values.get("url", "").strip() or feed.get("source_url", "")
    if strategy == "json_api":
        try:
            req_headers = _json.loads(form_values.get("request_headers_json") or "{}") or {}
        except ValueError:
            req_headers = {}
        pagination = None
        pag_param = form_values.get("pagination_param", "").strip()
        if pag_param:
            try:
                per_page = int(form_values.get("pagination_per_page") or "0")
            except ValueError:
                per_page = 0
            try:
                start = int(form_values.get("pagination_start") or "1")
            except ValueError:
                start = 1
            pagination = PaginationSpec(
                location=form_values.get("pagination_location") or "body",
                param=pag_param,
                kind=form_values.get("pagination_kind") or "page",
                start=start,
                per_page=per_page,
                per_page_param=form_values.get("pagination_per_page_param", ""),
                has_more_path=form_values.get("pagination_has_more_path", ""),
                next_cursor_path=form_values.get("pagination_next_cursor_path", ""),
                total_pages_path=form_values.get("pagination_total_pages_path", ""),
            )
        try:
            max_pages = max(1, min(50, int(form_values.get("max_pages") or "1")))
        except ValueError:
            max_pages = 1
        try:
            max_items = max(1, min(5000, int(form_values.get("max_items") or "250")))
        except ValueError:
            max_items = 250
        return ScrapeRequest(
            url=url,
            strategy=FeedStrategy.JSON_API,
            selectors=ScrapeSelectors(
                item=form_values.get("item_path", ""),
                item_title=form_values.get("item_title", ""),
                item_link=form_values.get("item_link", ""),
                item_content=form_values.get("item_content", ""),
                item_timestamp=form_values.get("item_timestamp", ""),
            ),
            method=(form_values.get("method") or "GET").upper(),
            request_body=form_values.get("request_body", ""),
            request_headers=req_headers,
            pagination=pagination,
            max_pages=max_pages,
            max_items=max_items,
            services=services,
            adaptive=False,
        ), ""
    if strategy == "xpath":
        return ScrapeRequest(
            url=url,
            strategy=FeedStrategy.XPATH,
            selectors=ScrapeSelectors(
                item=form_values.get("item_selector", ""),
                item_title=form_values.get("title_selector", ""),
                item_link=form_values.get("link_selector", ""),
                item_content=form_values.get("content_selector", ""),
                item_timestamp=form_values.get("timestamp_selector", ""),
            ),
            services=services,
            adaptive=False,
        ), ""
    if strategy == "embedded_json":
        return ScrapeRequest(
            url=url,
            strategy=FeedStrategy.EMBEDDED_JSON,
            selectors=ScrapeSelectors(
                item=form_values.get("path", ""),
                item_title=form_values.get("item_title", ""),
                item_link=form_values.get("item_link", ""),
                item_content=form_values.get("item_content", ""),
                item_timestamp=form_values.get("item_timestamp", ""),
            ),
            services=services,
            adaptive=False,
        ), ""
    return None, f"Editing strategy '{strategy}' is not supported yet."


def _recipe_from_config(cfg: dict) -> dict:
    """Flatten a saved ScrapeRequest config into the recipe dict the editor /
    debug prompt uses. The keys mirror the editor form field names."""
    sel = cfg.get("selectors") or {}
    strategy = cfg.get("strategy", "")
    if strategy == "json_api":
        out = {
            "item_path": sel.get("item", ""),
            "item_title": sel.get("item_title", ""),
            "item_link": sel.get("item_link", ""),
            "item_content": sel.get("item_content", ""),
            "item_timestamp": sel.get("item_timestamp", ""),
            "method": cfg.get("method", "GET"),
            "request_body": cfg.get("request_body", ""),
            "request_headers": cfg.get("request_headers", {}),
        }
        if cfg.get("pagination"):
            out["pagination"] = cfg["pagination"]
        return out
    if strategy == "xpath":
        return {
            "item_selector": sel.get("item", ""),
            "title_selector": sel.get("item_title", ""),
            "link_selector": sel.get("item_link", ""),
            "content_selector": sel.get("item_content", ""),
            "timestamp_selector": sel.get("item_timestamp", ""),
        }
    if strategy == "embedded_json":
        return {
            "path": sel.get("item", ""),
            "item_title": sel.get("item_title", ""),
            "item_link": sel.get("item_link", ""),
            "item_content": sel.get("item_content", ""),
            "item_timestamp": sel.get("item_timestamp", ""),
        }
    return {}


async def _fetch_source_sample(req, max_bytes: int = 12_000) -> str:
    """Grab a rough source sample (HTML for xpath/embedded_json, JSON for
    json_api) purely for the LLM debug prompt. Best-effort; never raises."""
    import httpx
    from app.models.schemas import FeedStrategy
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as c:
            if req.strategy == FeedStrategy.JSON_API:
                method = (req.method or "GET").upper()
                if method == "POST":
                    resp = await c.post(
                        req.url,
                        content=req.request_body or None,
                        headers=req.request_headers or None,
                    )
                else:
                    resp = await c.get(req.url, headers=req.request_headers or None)
            else:
                resp = await c.get(req.url)
            return resp.text[:max_bytes]
    except Exception as exc:
        return f"(source fetch failed: {exc})"


@router.get("/feeds/{feed_id}/edit", response_class=HTMLResponse)
async def feed_edit(request: Request, feed_id: str) -> HTMLResponse:
    from app.ui.feeds_store import get_feeds_store
    from app.scraping.config_store import load_config

    store = get_feeds_store()
    feed = store.get(feed_id)
    if feed is None:
        request.session["flash"] = {"type": "error", "message": "Feed not found."}
        return RedirectResponse("/feeds", status_code=303)

    strategy = feed.get("strategy", "")
    cfg: dict = {}
    if strategy == "rss":
        request.session["flash"] = {
            "type": "info",
            "message": "RSS feeds don't have an editable recipe — change backend or cadence on the feeds page instead.",
        }
        return RedirectResponse("/feeds", status_code=303)
    config_id = feed.get("config_id", "")
    if not config_id:
        request.session["flash"] = {"type": "error", "message": "No scrape config for this feed."}
        return RedirectResponse("/feeds", status_code=303)
    cfg = load_config("scrape", config_id) or {}
    recipe = _recipe_from_config(cfg)
    has_llm = _llm_config() is not None
    return templates.TemplateResponse(
        request,
        "feed_edit.html",
        _ctx(
            request,
            title=f"Edit {feed.get('name', 'feed')}",
            feed=feed,
            cfg=cfg,
            recipe=recipe,
            has_llm=has_llm,
        ),
    )


@router.post("/feeds/{feed_id}/preview-edits")
async def feed_preview_edits(request: Request, feed_id: str) -> JSONResponse:
    """Run a live preview of the recipe currently in the editor form. Does not
    persist. Returns JSON so the editor can render results inline."""
    from app.ui.feeds_store import get_feeds_store
    from app.scraping.scrape import run_scrape

    store = get_feeds_store()
    feed = store.get(feed_id)
    if feed is None:
        return JSONResponse({"error": "Feed not found"}, status_code=404)

    form = await request.form()
    form_values = {k: str(v) for k, v in form.items()}
    services = _service_config()
    req, err = _build_edit_scrape_request(feed, form_values, services)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    req.max_pages = 1
    req.max_items = 25
    try:
        result = await run_scrape(req)
    except Exception as exc:
        return JSONResponse({"error": f"Preview failed: {exc}"}, status_code=500)
    return JSONResponse({
        "item_count": result.item_count,
        "items": [
            {
                "title": it.title, "link": it.link, "content": it.content[:200],
                "timestamp": it.timestamp,
            }
            for it in result.items[:10]
        ],
        "errors": result.errors,
        "warnings": result.warnings,
        "fetch_backend_used": result.fetch_backend_used,
    })


@router.post("/feeds/{feed_id}/save-edits")
async def feed_save_edits(request: Request, feed_id: str) -> RedirectResponse:
    """Persist the edited recipe over the existing scrape config."""
    from pathlib import Path
    from app.ui.feeds_store import get_feeds_store
    from app.scraping.config_store import update_config

    store = get_feeds_store()
    feed = store.get(feed_id)
    if feed is None:
        request.session["flash"] = {"type": "error", "message": "Feed not found."}
        return RedirectResponse("/feeds", status_code=303)
    config_id = feed.get("config_id", "")
    if not config_id:
        request.session["flash"] = {"type": "error", "message": "No scrape config for this feed."}
        return RedirectResponse("/feeds", status_code=303)

    form = await request.form()
    form_values = {k: str(v) for k, v in form.items()}
    services = _service_config()
    req, err = _build_edit_scrape_request(feed, form_values, services)
    if err:
        request.session["flash"] = {"type": "error", "message": err}
        return RedirectResponse(f"/feeds/{feed_id}/edit", status_code=303)
    ok = update_config("scrape", config_id, req.model_dump())
    if not ok:
        request.session["flash"] = {"type": "error", "message": "Could not update scrape config on disk."}
        return RedirectResponse(f"/feeds/{feed_id}/edit", status_code=303)

    # Invalidate Atom cache + feed error state so the next refresh reflects edits.
    cached = feed.get("cached_atom_path", "")
    if cached:
        try:
            Path(cached).unlink(missing_ok=True)
        except Exception:
            pass
    store.update(feed_id, last_error="", consecutive_empty_refreshes=0)

    request.session["flash"] = {"type": "success", "message": "Recipe saved. Next refresh will use it."}
    return RedirectResponse(f"/feeds/{feed_id}/edit", status_code=303)


@router.post("/feeds/{feed_id}/debug")
async def feed_debug(request: Request, feed_id: str) -> JSONResponse:
    """Ship the current recipe + preview result + source sample to the LLM and
    ask for a diff. The editor applies the diff client-side so the user can
    preview and accept/reject before saving."""
    from app.ui.feeds_store import get_feeds_store
    from app.scraping.scrape import run_scrape
    from app.llm.analyzer import debug_recipe

    llm = _llm_config()
    if llm is None:
        return JSONResponse({"error": "LLM not configured. Set endpoint + API key in Settings."}, status_code=400)

    store = get_feeds_store()
    feed = store.get(feed_id)
    if feed is None:
        return JSONResponse({"error": "Feed not found"}, status_code=404)

    form = await request.form()
    form_values = {k: str(v) for k, v in form.items()}
    services = _service_config()
    req, err = _build_edit_scrape_request(feed, form_values, services)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    req.max_pages = 1
    req.max_items = 10

    try:
        preview = await run_scrape(req)
    except Exception as exc:
        preview = None
        preview_err = str(exc)
    else:
        preview_err = ""

    sample_items = []
    item_count = 0
    errors: list[str] = [preview_err] if preview_err else []
    warnings: list[str] = []
    if preview is not None:
        item_count = preview.item_count
        errors.extend(preview.errors)
        warnings.extend(preview.warnings)
        sample_items = [
            {"title": it.title, "link": it.link,
             "content": (it.content or "")[:200], "timestamp": it.timestamp}
            for it in preview.items[:3]
        ]

    source_sample = await _fetch_source_sample(req)
    recipe = {k: v for k, v in form_values.items() if k not in ("name", "cadence")}
    result = await debug_recipe(
        strategy=feed.get("strategy", ""),
        url=req.url,
        recipe=recipe,
        item_count=item_count,
        errors=errors,
        warnings=warnings,
        sample_items=sample_items,
        source_sample=source_sample,
        llm=llm,
    )
    return JSONResponse({
        "item_count": item_count,
        "preview_errors": errors,
        "preview_warnings": warnings,
        **result,
    })


@router.get("/feeds.opml")
async def feeds_opml(request: Request) -> Response:
    """Export all saved feeds as OPML for FreshRSS / other readers.

    Every feed — including XPath and JSON-API strategies — is exported with its
    autofeed ``/scrape/feed?id=…`` URL. The sidecar emits Atom XML from that
    endpoint regardless of the underlying strategy, so FreshRSS treats XPath
    feeds as plain Atom subscriptions; no XPath-extension attributes needed.
    """
    from xml.sax.saxutils import escape as _xe
    from datetime import datetime, timezone
    from app.ui.feeds_store import get_feeds_store

    feeds = get_feeds_store().all()
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head>",
        "    <title>AutoFeed — saved feeds</title>",
        f"    <dateCreated>{now}</dateCreated>",
        "  </head>",
        "  <body>",
    ]
    for f in feeds:
        title = _xe(f.get("name") or "Untitled Feed")
        xml_url = _xe(f.get("feed_url") or "")
        html_url = _xe(f.get("source_url") or "")
        if not xml_url:
            continue
        lines.append(
            f'    <outline type="rss" text="{title}" title="{title}" '
            f'xmlUrl="{xml_url}" htmlUrl="{html_url}" />'
        )
    lines.extend(["  </body>", "</opml>", ""])
    body = "\n".join(lines).encode()
    return Response(
        content=body,
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="autofeed.opml"'},
    )


@router.get("/feeds.xpath.opml")
async def feeds_opml_xpath(request: Request) -> Response:
    """Export saved feeds as FreshRSS-native OPML.

    For XPath feeds we emit ``type="HTML+XPath"`` outlines with ``frss:xPath*``
    attributes pointing at the source page — FreshRSS then runs the scrape
    itself via its built-in HTML+XPath feed kind, bypassing this sidecar. Other
    strategies (RSS, JSON API, embedded JSON, GraphQL) still point at the
    sidecar's ``/scrape/feed`` Atom endpoint because FreshRSS cannot replicate
    them natively.
    """
    from xml.sax.saxutils import escape as _xe, quoteattr as _qa
    from datetime import datetime, timezone
    from app.scraping.config_store import load_config
    from app.ui.feeds_store import get_feeds_store

    feeds = get_feeds_store().all()
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0" xmlns:frss="https://freshrss.org/opml">',
        "  <head>",
        "    <title>AutoFeed — FreshRSS-native export</title>",
        f"    <dateCreated>{now}</dateCreated>",
        "  </head>",
        "  <body>",
    ]

    for f in feeds:
        title = f.get("name") or "Untitled Feed"
        source_url = f.get("source_url") or ""
        feed_url = f.get("feed_url") or ""
        strategy = f.get("strategy", "")
        config_id = f.get("config_id", "")

        attrs: list[tuple[str, str]] = [("text", title), ("title", title)]

        if strategy == "xpath" and config_id and source_url:
            cfg = load_config("scrape", config_id)
            sel = (cfg or {}).get("selectors", {}) if cfg else {}
            attrs.append(("type", "HTML+XPath"))
            attrs.append(("xmlUrl", source_url))
            attrs.append(("htmlUrl", source_url))
            mapping = [
                ("frss:xPathItem",          sel.get("item", "")),
                ("frss:xPathItemTitle",     sel.get("item_title", "")),
                ("frss:xPathItemUri",       sel.get("item_link", "")),
                ("frss:xPathItemContent",   sel.get("item_content", "")),
                ("frss:xPathItemAuthor",    sel.get("item_author", "")),
                ("frss:xPathItemTimestamp", sel.get("item_timestamp", "")),
                ("frss:xPathItemThumbnail", sel.get("item_thumbnail", "")),
            ]
            for k, v in mapping:
                if v:
                    attrs.append((k, v))
            link_sel = sel.get("item_link", "")
            if link_sel:
                attrs.append(("frss:xPathItemUid", link_sel))
        else:
            if not feed_url:
                continue
            attrs.append(("type", "rss"))
            attrs.append(("xmlUrl", feed_url))
            if source_url:
                attrs.append(("htmlUrl", source_url))

        rendered = " ".join(f"{k}={_qa(v)}" for k, v in attrs)
        lines.append(f"    <outline {rendered} />")

    lines.extend(["  </body>", "</opml>", ""])
    body = "\n".join(lines).encode()
    return Response(
        content=body,
        media_type="text/x-opml",
        headers={"Content-Disposition": 'attachment; filename="autofeed-xpath.opml"'},
    )


@router.get("/feeds/{feed_id}/preview", response_class=HTMLResponse)
async def feed_preview(request: Request, feed_id: str) -> HTMLResponse:
    """Render a small HTML fragment of the most recent items in *feed_id*.

    Uses the cached Atom file if it exists and non-empty; otherwise does a live
    scrape. Returned as a fragment so the feeds page can inline it below the
    card without a full reload.
    """
    from app.ui.feeds_store import get_feeds_store
    from app.scheduler.runner import _ATOM_CACHE_DIR
    from pathlib import Path
    import feedparser

    store = get_feeds_store()
    feed = store.get(feed_id)
    if feed is None:
        return HTMLResponse(
            '<div class="preview-errors"><span class="preview-error-label">Feed not found.</span></div>',
            status_code=404,
        )

    items: list[dict] = []
    warnings: list[str] = []
    errors: list[str] = []

    atom_path = Path(feed.get("cached_atom_path", "") or _ATOM_CACHE_DIR / f"{feed_id}.atom")
    atom_bytes = atom_path.read_bytes() if atom_path.exists() else b""

    if atom_bytes and b"<entry" in atom_bytes:
        parsed = feedparser.parse(atom_bytes)
        for e in parsed.entries[:10]:
            items.append({
                "title": getattr(e, "title", ""),
                "link": getattr(e, "link", ""),
                "content": (getattr(e, "summary", "") or "")[:400],
                "timestamp": getattr(e, "published", "") or getattr(e, "updated", ""),
            })
    else:
        # Live scrape — reuse the same dispatch path /scrape/feed uses.
        from app.scraping.scrape import run_scrape
        from app.scraping.config_store import load_config
        from app.models.schemas import ScrapeRequest
        config_id = feed.get("config_id")
        if feed.get("strategy") == "rss":
            warnings.append("RSS feed — preview shows cached Atom once the scheduler runs.")
        elif not config_id:
            errors.append("No scrape config associated with this feed.")
        else:
            cfg = load_config("scrape", config_id)
            if cfg is None:
                errors.append("Saved scrape config missing.")
            else:
                override = feed.get("fetch_backend_override") or ""
                if override:
                    services = dict(cfg.get("services", {}))
                    services["fetch_backend"] = override
                    cfg = {**cfg, "services": services}
                req = ScrapeRequest.model_validate(cfg)
                try:
                    result = await run_scrape(req)
                    warnings.extend(result.warnings)
                    errors.extend(result.errors)
                    for it in result.items[:10]:
                        items.append({
                            "title": it.title,
                            "link": it.link,
                            "content": it.content,
                            "timestamp": it.timestamp,
                        })
                except Exception as exc:
                    errors.append(f"Scrape failed: {exc}")

    field_counts = {
        "title": sum(1 for i in items if i.get("title")),
        "content": sum(1 for i in items if i.get("content")),
        "link": sum(1 for i in items if i.get("link")),
        "date": sum(1 for i in items if i.get("timestamp")),
    }
    return templates.TemplateResponse(
        request,
        "partials/preview_table.html",
        {
            "request": request,
            "items": items,
            "total": len(items),
            "field_counts": field_counts,
            "warnings": warnings,
            "errors": errors,
            "refine_url": "",
        },
    )


@router.post("/feeds/{feed_id}/refresh-now")
async def feed_refresh_now(request: Request, feed_id: str) -> RedirectResponse:
    from app.ui.feeds_store import get_feeds_store
    from app.scheduler.runner import _run_feed_job

    store = get_feeds_store()
    if store.get(feed_id) is None:
        request.session["flash"] = {"type": "error", "message": "Feed not found."}
        return RedirectResponse("/feeds", status_code=303)

    try:
        await _run_feed_job(feed_id)
        request.session["flash"] = {"type": "success", "message": "Feed refreshed."}
    except Exception as exc:
        request.session["flash"] = {"type": "error", "message": f"Refresh failed: {exc}"}
    return RedirectResponse("/feeds", status_code=303)


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request) -> HTMLResponse:
    store = _store()
    s = store.get()
    s["llm_api_key_display"] = store.mask_api_key(s.get("llm_api_key", ""))
    return templates.TemplateResponse(request, "settings.html", _ctx(request, "Settings", settings=s))


@router.post("/settings")
async def settings_post(request: Request) -> RedirectResponse:
    store = _store()
    form = await request.form()

    def f(key: str, default: str = "") -> str:
        return str(form.get(key, default)).strip()

    changes: dict = {
        "llm_endpoint":          f("llm_endpoint"),
        "llm_model":             f("llm_model") or "gpt-4o-mini",
        "rss_bridge_url":        f("rss_bridge_url"),
        "rss_bridge_deploy_mode": f("rss_bridge_deploy_mode") or "auto",
        "fetch_backend":         f("fetch_backend") or "bundled",
        "playwright_server_url": f("playwright_server_url"),
        "browserless_url":       f("browserless_url"),
        "scrapling_serve_url":   f("scrapling_serve_url"),
        "services_auth_token":   f("services_auth_token"),
        "auto_deploy_bridges":   "auto_deploy_bridges" in form,
        "default_cadence":           f("default_cadence") or "1d",
        "default_stealth_mode":      f("default_stealth_mode") or "on_demand",
        "default_solve_cloudflare":  "default_solve_cloudflare" in form,
        "default_block_webrtc":      "default_block_webrtc" in form,
        "proxy_url":                 f("proxy_url"),
        "sftp_host":             f("sftp_host"),
        "sftp_port":             f("sftp_port") or "22",
        "sftp_user":             f("sftp_user"),
        "sftp_key_path":         f("sftp_key_path"),
        "sftp_target_dir":       f("sftp_target_dir"),
    }

    submitted_key = f("llm_api_key")
    if not store.is_masked_key(submitted_key):
        changes["llm_api_key"] = submitted_key

    store.update(**changes)
    request.session["flash"] = {"type": "success", "message": "Settings saved."}
    return RedirectResponse("/settings", status_code=303)


# ── Analyze ───────────────────────────────────────────────────────────────────

@router.get("/analyze/{discover_id}", response_class=HTMLResponse)
async def analyze(
    request: Request,
    discover_id: str,
    force: bool = False,
    force_strategy: str = "",
) -> HTMLResponse:
    import logging as _logging
    from app.llm.analyzer import recommend_strategy, should_invoke_llm, xpath_hunt
    from app.models.schemas import (
        AnalyzeRequest, AnalyzeResponse, DiscoverResponse,
        LLMRecommendation, FeedStrategy, XPathCandidate,
    )
    from app.services.discovery_cache import load_discovery, load_browser_html, update_discovery
    from app.scraping.scrape import fetch_and_parse

    stored = load_discovery(discover_id)
    if stored is None:
        return templates.TemplateResponse(
            request,
            "discover_not_found.html",
            _ctx(request, "Result not found", discover_id=discover_id),
            status_code=404,
        )

    target_url = stored.get("url", "")
    llm = _llm_config()
    services = _service_config()
    disc = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})

    if llm is None:
        return templates.TemplateResponse(
            request,
            "analyze.html",
            _ctx(
                request, f"Analysis — {target_url}",
                target_url=target_url, discover_id=discover_id,
                llm_missing=True, result=None,
                discovery=disc.results.model_dump(),
                probe_item_count=None, probe_warning=None,
                rss_skipped_warning=None,
            ),
        )

    # ── Forced XPath hunt (user rejected RSS/JSON) ────────────────────────────
    if force_strategy == "xpath":
        cached_html = load_browser_html(discover_id)
        if not cached_html:
            try:
                cached_html, _, _ = await fetch_and_parse(target_url, services, timeout=30)
            except RuntimeError as exc:
                cached_html = ""
        html_skeleton = stored.get("results", {}).get("html_skeleton", "")
        analysis_result = None
        probe_item_count = None
        probe_warning = None
        llm_capture: dict = {}
        if cached_html:
            try:
                proposal = await xpath_hunt(target_url, cached_html, html_skeleton, llm, capture=llm_capture)
                item_sel = proposal.get("item_selector") or ""
                if item_sel:
                    probe_item_count = 0
                    try:
                        from lxml.html import document_fromstring
                        _doc = document_fromstring(cached_html)
                        probe_item_count = len(_doc.xpath(item_sel))
                    except Exception:
                        pass
                    new_c = XPathCandidate(
                        item_selector=item_sel,
                        title_selector=proposal.get("title_selector") or "",
                        link_selector=proposal.get("link_selector") or "",
                        content_selector=proposal.get("content_selector") or "",
                        timestamp_selector=proposal.get("timestamp_selector") or "",
                        author_selector=proposal.get("author_selector") or "",
                        thumbnail_selector=proposal.get("thumbnail_selector") or "",
                        confidence=0.7 if probe_item_count >= 2 else 0.3,
                        item_count=probe_item_count,
                    )
                    existing = {c.item_selector for c in disc.results.xpath_candidates}
                    if item_sel not in existing:
                        disc.results.xpath_candidates.insert(0, new_c)
                        update_discovery(discover_id, {
                            "url": disc.url,
                            "timestamp": disc.timestamp.isoformat(),
                            "results": disc.results.model_dump(),
                            "errors": disc.errors,
                        })
                    rec = LLMRecommendation(
                        strategy=FeedStrategy.XPATH,
                        confidence=0.7 if probe_item_count >= 2 else 0.3,
                        reasoning=proposal.get("reasoning", ""),
                        selected_candidate_ref=item_sel[:80],
                    )
                    analysis_result = AnalyzeResponse(url=target_url, recommendation=rec)
                    if probe_item_count == 0:
                        probe_warning = (
                            f"XPath selector '{item_sel}' matched 0 items on the cached page."
                        )
                else:
                    analysis_result = AnalyzeResponse(
                        url=target_url,
                        errors=["LLM did not return an item_selector."],
                    )
            except RuntimeError as exc:
                analysis_result = AnalyzeResponse(url=target_url, errors=[f"LLM error: {exc}"])
        else:
            analysis_result = AnalyzeResponse(url=target_url, errors=["Page HTML unavailable."])

        trace_store.add_action(discover_id, {
            "kind": "analyze",
            "panel": "global",
            "mode": "force_strategy=xpath",
            "provenance": {
                "method": "analyzer.xpath_hunt (GET /analyze?force_strategy=xpath)",
                "html_source": "cached browser HTML" if cached_html else "fresh fetch_and_parse",
            },
            "inputs": {
                "html_bytes": len(cached_html),
                "html_skeleton_bytes": len(html_skeleton),
            },
            "llm_call": llm_capture,
            "outputs": {
                "proposal": locals().get("proposal"),
                "probe_item_count": probe_item_count,
                "probe_warning": probe_warning,
            },
        })
        return templates.TemplateResponse(
            request,
            "analyze.html",
            _ctx(
                request, f"Analysis — {target_url}",
                target_url=target_url, discover_id=discover_id,
                llm_missing=False,
                result=analysis_result.model_dump() if analysis_result else None,
                discovery=disc.results.model_dump(),
                probe_item_count=probe_item_count,
                probe_warning=probe_warning,
                rss_skipped_warning=None,
            ),
        )

    # ── Normal LLM strategy recommendation ────────────────────────────────────
    if force or disc.results.force_skip_rss:
        _logging.getLogger(__name__).info(
            "LLM short-circuit overridden (discover_id=%s, force=%s, force_skip_rss=%s)",
            discover_id, force, disc.results.force_skip_rss,
        )
        needs_llm, auto_strategy = True, ""
    else:
        needs_llm, auto_strategy = should_invoke_llm(disc.results)

    if not needs_llm:
        auto_rec = LLMRecommendation(
            strategy=FeedStrategy(auto_strategy),
            confidence=1.0,
            reasoning="Auto-selected (no LLM needed)",
        )
        analysis = AnalyzeResponse(url=target_url, recommendation=auto_rec)
    else:
        req = AnalyzeRequest(
            url=target_url,
            results=disc.results,
            html_skeleton=stored.get("results", {}).get("html_skeleton", ""),
            llm=llm,
            discover_id=discover_id,
        )
        llm_capture: dict = {}
        try:
            analysis = await recommend_strategy(req, capture=llm_capture)
        except Exception as exc:
            analysis = AnalyzeResponse(url=target_url, errors=[f"LLM error: {exc}"])
        trace_store.add_action(discover_id, {
            "kind": "analyze",
            "panel": "global",
            "mode": "strategy-recommendation",
            "provenance": {
                "method": "analyzer.recommend_strategy (LLM picks between RSS/JSON/GraphQL/embedded/XPath)",
            },
            "inputs": {
                "html_skeleton_bytes": len(stored.get("results", {}).get("html_skeleton", "")),
                "force": force,
                "force_skip_rss": disc.results.force_skip_rss,
            },
            "llm_call": llm_capture,
            "outputs": {
                "recommendation": analysis.recommendation.model_dump() if analysis.recommendation else None,
                "errors": list(analysis.errors or []),
                "tokens_used": analysis.tokens_used,
            },
        })

    # ── Probe the recommendation + detect RSS-under-skip ─────────────────────
    probe_item_count = None
    probe_warning = None
    rss_skipped_warning = None
    rec = analysis.recommendation

    if rec and disc.results.force_skip_rss and rec.strategy.value == "rss":
        rss_skipped_warning = (
            "The LLM recommended RSS despite 'Skip RSS' being active. "
            "Click 'Ask LLM to try XPath instead' to force an XPath search."
        )

    if rec and rec.strategy.value in ("xpath", "xml_xpath") and rec.selected_candidate_ref:
        ref = rec.selected_candidate_ref
        matched_c = next(
            (c for c in disc.results.xpath_candidates if ref in c.item_selector or c.item_selector in ref),
            disc.results.xpath_candidates[0] if disc.results.xpath_candidates else None,
        )
        if matched_c:
            try:
                cached_html = load_browser_html(discover_id)
                if not cached_html:
                    cached_html, _, _ = await fetch_and_parse(
                        target_url, services, timeout=5
                    )
                from lxml.html import document_fromstring
                _doc = document_fromstring(cached_html)
                probe_item_count = len(_doc.xpath(matched_c.item_selector))
                if probe_item_count == 0:
                    probe_warning = (
                        f"LLM suggested item_selector '{matched_c.item_selector}' "
                        "but it matched 0 items on the page."
                    )
            except Exception:
                pass

    return templates.TemplateResponse(
        request,
        "analyze.html",
        _ctx(
            request, f"Analysis — {target_url}",
            target_url=target_url, discover_id=discover_id,
            llm_missing=False, result=analysis.model_dump(),
            discovery=disc.results.model_dump(),
            probe_item_count=probe_item_count,
            probe_warning=probe_warning,
            rss_skipped_warning=rss_skipped_warning,
        ),
    )


# ── Bridge ────────────────────────────────────────────────────────────────────

@router.get("/bridge/{discover_id}", response_class=HTMLResponse)
async def bridge_form(request: Request, discover_id: str) -> HTMLResponse:
    from app.services.discovery_cache import load_discovery

    stored = load_discovery(discover_id)
    if stored is None:
        return templates.TemplateResponse(
            request,
            "discover_not_found.html",
            _ctx(request, "Result not found", discover_id=discover_id),
            status_code=404,
        )

    target_url = stored.get("url", "")
    return templates.TemplateResponse(
        request,
        "bridge.html",
        _ctx(
            request, f"Generate Bridge — {target_url}",
            target_url=target_url, discover_id=discover_id,
            has_llm=bool(_llm_config()),
            generated=None, deployed=None, hint="",
        ),
    )


@router.post("/bridge/generate", response_class=HTMLResponse)
async def bridge_generate(request: Request) -> HTMLResponse:
    from app.llm.analyzer import generate_bridge
    from app.models.schemas import BridgeGenerateRequest, BridgeGenerateResponse, DiscoverResponse
    from app.services.discovery_cache import load_discovery

    form = await request.form()
    discover_id = str(form.get("discover_id", "")).strip()
    hint = str(form.get("hint", "")).strip()

    stored = load_discovery(discover_id)
    if stored is None:
        request.session["flash"] = {"type": "error", "message": "Discovery result expired."}
        return RedirectResponse("/", status_code=303)

    target_url = stored.get("url", "")
    llm = _llm_config()

    if llm is None:
        generated = BridgeGenerateResponse(
            errors=["LLM not configured — set endpoint and API key in Settings."]
        )
    else:
        disc = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
        req = BridgeGenerateRequest(
            url=target_url,
            results=disc.results,
            html_skeleton=stored.get("results", {}).get("html_skeleton", ""),
            llm=llm,
            hint=hint,
            discover_id=discover_id,
        )
        llm_capture: dict = {}
        try:
            generated = await generate_bridge(req, capture=llm_capture)
        except Exception as exc:
            generated = BridgeGenerateResponse(errors=[f"Generation failed: {exc}"])
        trace_store.add_action(discover_id, {
            "kind": "bridge-generate",
            "panel": "global",
            "provenance": {
                "method": "analyzer.generate_bridge (LLM produces an RSS-Bridge PHP class)",
            },
            "inputs": {
                "hint": hint,
                "html_skeleton_bytes": len(stored.get("results", {}).get("html_skeleton", "")),
            },
            "llm_call": llm_capture,
            "outputs": {
                "bridge_name": generated.bridge_name,
                "php_bytes": len(generated.php_code),
                "sanity_warnings": list(generated.sanity_warnings),
                "soft_warnings": list(generated.soft_warnings),
                "errors": list(generated.errors or []),
            },
        })

    return templates.TemplateResponse(
        request,
        "bridge.html",
        _ctx(
            request, f"Generate Bridge — {target_url}",
            target_url=target_url, discover_id=discover_id,
            has_llm=bool(llm),
            generated=generated.model_dump(), deployed=None, hint=hint,
        ),
    )


@router.post("/bridge/deploy", response_class=HTMLResponse)
async def bridge_deploy(request: Request) -> HTMLResponse:
    from app.bridge.deploy import deploy_bridge, deploy_bridge_remote, _local_bridges_writable
    from app.models.schemas import BridgeDeployResponse

    form = await request.form()
    bridge_name = str(form.get("bridge_name", "")).strip()
    php_code = str(form.get("php_code", "")).strip()
    discover_id = str(form.get("discover_id", "")).strip()

    if not bridge_name or not php_code:
        request.session["flash"] = {
            "type": "error", "message": "Missing bridge name or code.",
        }
        return RedirectResponse("/", status_code=303)

    s = _store().get()
    services = _service_config()
    deploy_mode = s.get("rss_bridge_deploy_mode", "auto")
    bridges_dir = _bridges_dir()
    local_writable = _local_bridges_writable(bridges_dir)

    if deploy_mode == "local_only":
        result = deploy_bridge(bridge_name, php_code, bridges_dir)
    elif deploy_mode == "remote_only":
        if s.get("sftp_host") and s.get("sftp_user") and s.get("sftp_target_dir"):
            from app.bridge.sftp_deploy import deploy_bridge_via_sftp
            result = await deploy_bridge_via_sftp(
                name=bridge_name, code=php_code,
                host=s["sftp_host"], port=int(s.get("sftp_port", 22)),
                username=s["sftp_user"], key_path=s.get("sftp_key_path") or None,
                target_dir=s["sftp_target_dir"],
            )
        else:
            result = await deploy_bridge_remote(bridge_name, php_code, services=services, bridges_dir=bridges_dir)
    else:
        # auto: local first, then remote
        if local_writable:
            result = deploy_bridge(bridge_name, php_code, bridges_dir)
            if not result.deployed:
                result = await deploy_bridge_remote(bridge_name, php_code, services=services, bridges_dir=bridges_dir)
        elif s.get("sftp_host") and s.get("sftp_user") and s.get("sftp_target_dir"):
            from app.bridge.sftp_deploy import deploy_bridge_via_sftp
            result = await deploy_bridge_via_sftp(
                name=bridge_name, code=php_code,
                host=s["sftp_host"], port=int(s.get("sftp_port", 22)),
                username=s["sftp_user"], key_path=s.get("sftp_key_path") or None,
                target_dir=s["sftp_target_dir"],
            )
        else:
            result = await deploy_bridge_remote(bridge_name, php_code, services=services, bridges_dir=bridges_dir)

    deployed = BridgeDeployResponse(
        deployed=result.deployed, path=result.path, errors=result.errors,
    )

    return templates.TemplateResponse(
        request,
        "bridge.html",
        _ctx(
            request, f"Deploy — {bridge_name}",
            target_url="", discover_id=discover_id,
            has_llm=bool(_llm_config()),
            generated={
                "bridge_name": bridge_name, "filename": f"{bridge_name}.php",
                "php_code": php_code, "sanity_warnings": [], "soft_warnings": [], "errors": [],
            },
            deployed=deployed.model_dump(), hint="",
        ),
    )


# ── Debug / "Under the hood" transparency endpoints ──────────────────────────

@router.get("/debug/discover/{discover_id}")
async def debug_discover_bundle(discover_id: str):
    """Return the full trace bundle for a discovery: provenance + LLM prompts
    + per-action inputs/outputs. Used by the UI's 'Under the hood' panels."""
    bundle = trace_store.get_bundle(discover_id)
    if bundle is None:
        return JSONResponse({"error": "No trace recorded for this discover_id."}, status_code=404)
    return JSONResponse(bundle)


# ── LLM field-mapping escalation for API candidates ──────────────────────────

@router.post("/llm-api-map/{discover_id}")
async def llm_api_map(discover_id: str, request: Request) -> JSONResponse:
    """Ask the LLM to re-map a JSON API candidate's item_path + field_mapping.

    Updates the stored discovery in place so refreshing /d/<id> shows the new
    mapping. Returns the applied mapping as JSON for the frontend to reflect
    without a full reload.
    """
    from app.services.discovery_cache import load_discovery, update_discovery
    from app.models.schemas import DiscoverResponse
    from app.llm.analyzer import map_api_fields

    form = await request.form()
    try:
        index = int(form.get("index", -1))
    except ValueError:
        return JSONResponse({"error": "Invalid index"}, status_code=400)

    llm_cfg = _llm_config()
    if llm_cfg is None:
        return JSONResponse({"error": "LLM not configured — set it in Settings."}, status_code=400)

    stored = load_discovery(discover_id)
    if stored is None:
        return JSONResponse({"error": "Discovery expired or missing."}, status_code=404)
    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    res = result.results
    if index < 0 or index >= len(res.api_endpoints):
        return JSONResponse({"error": "Index out of range."}, status_code=400)

    endpoint = res.api_endpoints[index]
    capture: dict = {}
    outcome = await map_api_fields(
        site_url=result.url, endpoint=endpoint, llm=llm_cfg, capture=capture,
    )
    if outcome.get("error"):
        trace_store.add_action(discover_id, {
            "kind": "llm-api-map", "panel": f"api:{index}",
            "error": outcome["error"], "llm": capture,
        })
        return JSONResponse({"error": outcome["error"]}, status_code=502)

    endpoint.item_path = outcome["item_path"] or endpoint.item_path
    if outcome["field_mapping"]:
        endpoint.field_mapping = outcome["field_mapping"]
    endpoint.llm_mapped = True
    endpoint.llm_reasoning = outcome["reasoning"]
    endpoint.llm_caveats = outcome["caveats"]

    update_discovery(discover_id, {
        "url": result.url,
        "timestamp": result.timestamp.isoformat(),
        "results": res.model_dump(),
        "errors": result.errors,
    })
    trace_store.add_action(discover_id, {
        "kind": "llm-api-map", "panel": f"api:{index}",
        "inputs": {"endpoint_url": endpoint.url, "method": endpoint.method},
        "outputs": {
            "item_path": endpoint.item_path,
            "field_mapping": endpoint.field_mapping,
            "reasoning": endpoint.llm_reasoning,
            "caveats": endpoint.llm_caveats,
        },
        "llm": capture,
    })
    return JSONResponse({
        "item_path": endpoint.item_path,
        "field_mapping": endpoint.field_mapping,
        "reasoning": endpoint.llm_reasoning,
        "caveats": endpoint.llm_caveats,
    })


# ── Filter workbench ─────────────────────────────────────────────────────────


def _diff_bodies(captures: list) -> dict:
    """Inspect multiple captures for the same endpoint and pick out keys whose
    values varied. Returns a structure the workbench template can render."""
    import json as _json

    parsed: list[tuple[str, Any]] = []
    for cap in captures:
        body = getattr(cap, "request_body", "") or ""
        try:
            parsed.append((body, _json.loads(body) if body else {}))
        except (ValueError, TypeError):
            parsed.append((body, None))

    # If all bodies are identical dicts, no varying keys.
    all_dicts = [p[1] for p in parsed if isinstance(p[1], dict)]
    if not all_dicts:
        return {"varying_keys": [], "stable_body": parsed[0][0] if parsed else "", "kind": "raw"}

    # Collect top-level keys.
    all_keys: set[str] = set()
    for d in all_dicts:
        all_keys.update(d.keys())

    varying: dict[str, list] = {}
    stable: dict[str, Any] = {}
    for key in all_keys:
        values = [d.get(key) for d in all_dicts]
        distinct = []
        seen = []
        for v in values:
            try:
                key_blob = _json.dumps(v, sort_keys=True, ensure_ascii=False)
            except Exception:
                key_blob = repr(v)
            if key_blob not in seen:
                seen.append(key_blob)
                distinct.append(v)
        if len(distinct) > 1:
            varying[key] = distinct
        else:
            stable[key] = distinct[0] if distinct else None

    return {
        "varying_keys": list(varying.keys()),
        "varying": varying,
        "stable": stable,
        "kind": "json",
    }


@router.get("/api-workbench/{discover_id}", response_class=HTMLResponse)
async def api_workbench(request: Request, discover_id: str) -> HTMLResponse:
    from app.services.discovery_cache import load_discovery
    from app.models.schemas import DiscoverResponse

    try:
        index = int(request.query_params.get("index", 0))
    except ValueError:
        index = 0

    stored = load_discovery(discover_id)
    if stored is None:
        return HTMLResponse("<p>Discovery expired.</p>", status_code=404)
    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    if index < 0 or index >= len(result.results.api_endpoints):
        return HTMLResponse("<p>Index out of range.</p>", status_code=404)
    endpoint = result.results.api_endpoints[index]

    diff = _diff_bodies(endpoint.captures or [])

    return templates.TemplateResponse(
        request,
        "api_workbench.html",
        _ctx(
            request,
            f"Workbench — {endpoint.url}",
            discover_id=discover_id,
            index=index,
            endpoint=endpoint.model_dump(),
            diff=diff,
            source_url=result.url,
            has_llm=bool(_store().get().get("llm_endpoint")),
        ),
    )


@router.post("/api-workbench/{discover_id}/preview")
async def api_workbench_preview(discover_id: str, request: Request) -> JSONResponse:
    """Run a single replay using the workbench's edited body/headers/mapping."""
    from app.services.discovery_cache import load_discovery
    from app.models.schemas import (
        DiscoverResponse, FeedStrategy, ScrapeRequest, ScrapeSelectors,
    )
    from app.scraping.scrape import run_scrape
    import json as _json

    form = await request.form()
    try:
        index = int(form.get("index", 0))
    except ValueError:
        index = 0

    stored = load_discovery(discover_id)
    if stored is None:
        return JSONResponse({"error": "Discovery expired."}, status_code=404)
    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    if index < 0 or index >= len(result.results.api_endpoints):
        return JSONResponse({"error": "Index out of range."}, status_code=400)
    endpoint = result.results.api_endpoints[index]

    def f(k: str) -> str:
        return str(form.get(k, "")).strip()

    request_body = f("request_body") or endpoint.request_body
    try:
        headers = _json.loads(f("request_headers_json") or "{}") or {}
    except ValueError:
        headers = dict(endpoint.request_headers or {})
    item_path = f("item_path") or endpoint.item_path
    url_override = f("url") or endpoint.url
    method = (f("method") or endpoint.method or "GET").upper()

    req = ScrapeRequest(
        url=url_override,
        strategy=FeedStrategy.JSON_API,
        selectors=ScrapeSelectors(
            item=item_path,
            item_title=f("item_title") or (endpoint.field_mapping or {}).get("title", ""),
            item_link=f("item_link") or (endpoint.field_mapping or {}).get("link", ""),
            item_content=f("item_content") or (endpoint.field_mapping or {}).get("content", ""),
            item_timestamp=f("item_timestamp") or (endpoint.field_mapping or {}).get("timestamp", ""),
            item_author=f("item_author") or (endpoint.field_mapping or {}).get("author", ""),
            item_thumbnail=f("item_thumbnail") or (endpoint.field_mapping or {}).get("thumbnail", ""),
        ),
        method=method,
        request_body=request_body,
        request_headers=headers,
        pagination=endpoint.pagination,
        max_pages=1,
        max_items=25,
        services=_service_config(),
        adaptive=False,
    )
    try:
        scrape = await run_scrape(req)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:400]}, status_code=500)
    items = [it.model_dump() for it in scrape.items[:10]]
    return JSONResponse({
        "item_count": scrape.item_count,
        "items": items,
        "errors": scrape.errors,
        "warnings": scrape.warnings,
        "field_counts": {
            "title":   sum(1 for it in scrape.items if it.title),
            "link":    sum(1 for it in scrape.items if it.link),
            "timestamp": sum(1 for it in scrape.items if it.timestamp),
            "content": sum(1 for it in scrape.items if it.content),
        },
    })


@router.get("/debug/discover/{discover_id}/artifact/{kind}")
async def debug_discover_artifact(discover_id: str, kind: str):
    """Serve a full HTML/text artifact (raw_html, browser_html, pruned_html,
    html_skeleton, …). Always served inline as text/plain with the correct
    Content-Disposition so the browser offers it as a download.
    """
    art = trace_store.get_artifact(discover_id, kind)
    if art is None:
        return JSONResponse({"error": f"No artifact '{kind}' for this discover_id."}, status_code=404)
    safe_kind = "".join(ch for ch in kind if ch.isalnum() or ch in "_-.") or "artifact"
    filename = f"{discover_id}_{safe_kind}.html"
    return PlainTextResponse(
        content=art.get("content", ""),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
