"""AutoFeed web UI — HTML routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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
    return templates.TemplateResponse(
        request,
        "home.html",
        _ctx(request, "AutoFeed — Discover Feeds", recent_feeds=recent, prefill_url=prefill_url),
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

    has_results = bool(
        res.rss_feeds or res.api_endpoints or res.embedded_json
        or res.xpath_candidates or res.graphql_operations
    )

    return templates.TemplateResponse(
        request,
        "discover_results.html",
        _ctx(
            request,
            f"Discovery — {result.url}",
            target_url=result.url,
            discover_id=discover_id,
            meta=res.page_meta.model_dump(),
            errors=stored.get("errors", []),
            has_llm=has_llm,
            has_results=has_results,
            rss_feeds=_entries(discover_id, res.rss_feeds, "rss"),
            api_endpoints=_entries(discover_id, res.api_endpoints, "api"),
            embedded_json=_entries(discover_id, res.embedded_json, "embedded"),
            xpath_candidates=_entries(discover_id, res.xpath_candidates, "xpath"),
            graphql_operations=_entries(discover_id, res.graphql_operations, "graphql"),
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
            req = ScrapeRequest(
                url=c.url, strategy=FeedStrategy.JSON_API,
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
            "title": sum(1 for it in items if it.title),
            "link":  sum(1 for it in items if it.link),
            "date":  sum(1 for it in items if it.timestamp),
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
            },
        )
    except Exception as exc:
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
            "title": sum(1 for it in items if it.title),
            "link":  sum(1 for it in items if it.link),
            "date":  sum(1 for it in items if it.timestamp),
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
    from app.services.discovery_cache import load_discovery, update_discovery
    from app.models.schemas import DiscoverResponse, FeedStrategy, ScrapeRequest, ScrapeSelectors
    from app.scraping.scrape import run_scrape

    form = await request.form()
    discover_id = str(form.get("discover_id", "")).strip()
    services = _service_config()

    stored = load_discovery(discover_id)
    if stored is None:
        return JSONResponse({"error": "Discovery result expired."}, status_code=400)

    result = DiscoverResponse.model_validate({**stored, "discover_id": discover_id})
    res = result.results

    # Collect examples from form
    refine_examples: dict[str, list[str]] = {}
    for role in ["title", "link", "content", "timestamp", "author", "thumbnail"]:
        examples = [v.strip() for v in form.getlist(f"{role}_examples") if v.strip()]
        if examples:
            refine_examples[role] = examples

    # Store refine examples on the discovery record
    if refine_examples:
        res.refine_examples = refine_examples
        # Update stored discovery
        update_discovery(discover_id, {
            "url": result.url,
            "timestamp": result.timestamp.isoformat(),
            "results": res.model_dump(),
            "errors": result.errors,
        })

    # Build response: {type: {index: html}}
    response_data: dict[str, dict[str, str]] = {}

    # Process XPath candidates
    if res.xpath_candidates:
        response_data["xpath"] = {}
        for idx, c in enumerate(res.xpath_candidates):
            # Merge global refine examples with any stored per-candidate refinements
            selectors = ScrapeSelectors(
                item=c.item_selector,
                item_title=c.title_selector,
                item_link=c.link_selector,
                item_content=c.content_selector,
                item_timestamp=c.timestamp_selector,
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
                scrape = await run_scrape(req)
                items = scrape.items[:10]
                total = len(items)
                fc = {
                    "title": sum(1 for it in items if it.title),
                    "link": sum(1 for it in items if it.link),
                    "date": sum(1 for it in items if it.timestamp),
                }

                html = templates.get_template("partials/preview_table.html").render(
                    request=request,
                    items=[it.model_dump() for it in items],
                    total=total,
                    field_counts=fc,
                    errors=scrape.errors,
                    warnings=scrape.warnings,
                    refine_url=None,
                )
                response_data["xpath"][str(idx)] = html
            except Exception as exc:
                response_data["xpath"][str(idx)] = f'<div class="preview-error">{str(exc)[:200]}</div>'

    return JSONResponse(response_data)


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
            url = f("url")
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
async def analyze(request: Request, discover_id: str) -> HTMLResponse:
    from app.llm.analyzer import recommend_strategy, should_invoke_llm
    from app.models.schemas import AnalyzeRequest, AnalyzeResponse, DiscoverResponse, LLMRecommendation, FeedStrategy
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
    llm = _llm_config()
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
            ),
        )

    # T5.1: skip LLM when the choice is unambiguous
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
            html_skeleton=stored.get("html_skeleton", ""),
            llm=llm,
            discover_id=discover_id,
        )
        try:
            analysis = await recommend_strategy(req)
        except Exception as exc:
            analysis = AnalyzeResponse(url=target_url, errors=[f"LLM error: {exc}"])

    return templates.TemplateResponse(
        request,
        "analyze.html",
        _ctx(
            request, f"Analysis — {target_url}",
            target_url=target_url, discover_id=discover_id,
            llm_missing=False, result=analysis.model_dump(),
            discovery=disc.results.model_dump(),
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
            html_skeleton=stored.get("html_skeleton", ""),
            llm=llm,
            hint=hint,
            discover_id=discover_id,
        )
        try:
            generated = await generate_bridge(req)
        except Exception as exc:
            generated = BridgeGenerateResponse(errors=[f"Generation failed: {exc}"])

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
