"""AutoFeed Sidecar — FastAPI application."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.bridge.deploy import deploy_bridge_remote, _local_bridges_writable
from app.discovery.cascade import run_discovery
from app.discovery.graphql_detect import probe_graphql_endpoint
from app.llm.analyzer import generate_bridge, recommend_strategy
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    BridgeDeployRequest,
    BridgeDeployResponse,
    BridgeGenerateRequest,
    BridgeGenerateResponse,
    DiscoverRequest,
    DiscoverResponse,
    FeedStrategy,
    GraphQLOperation,
    HealthResponse,
    PreviewResponse,
    ScrapeRequest,
    ScrapeResponse,
)
from app.scraping.config_store import delete_config, load_config, save_config
from app.scraping.scrape import run_scrape
from app.services.config import ServiceConfig
from app.services.discovery_cache import load_discovery, store_discovery

def _bridges_dir() -> str:
    return os.getenv("AUTOFEED_BRIDGES_DIR", "/app/bridges")

# Browsers are launched per-request in network_intercept.intercept_network — no shared lifespan teardown is needed.
app = FastAPI(
    title="AutoFeed Sidecar",
    description="Discovery and scraping sidecar for the FreshRSS AutoFeed extension.",
    version="0.6.0",
)

_cors_origins_env = os.getenv("AUTOFEED_CORS_ORIGINS", "")
_cors_origins = [origin.strip() for origin in _cors_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def _get_rate_limit_key(request: Request) -> str:
    """Custom rate limit key that applies stricter limits for browser-based discovery."""
    # Get base key from IP
    base_key = get_remote_address(request)
    
    # Check if this is a browser-based discovery request by checking the request body
    # The limiter will call this before we have the body, so we check query params
    use_browser = request.query_params.get("use_browser", "").lower() == "true"
    
    if use_browser:
        return f"{base_key}:browser"
    return base_key

def _inbound_token() -> str | None:
    token = os.getenv("AUTOFEED_INBOUND_TOKEN")
    return token if token else None


def _check_inbound_token(request: Request, require: bool = True) -> None:
    token = _inbound_token()
    if token is None or not require:
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Unauthorized inbound token")


# ── Core endpoints ────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


class FeedHealthResponse(BaseModel):
    url: str
    is_alive: bool
    http_status: int | None = None
    parse_error: str = ""


@app.get("/feed/health")
async def feed_health(url: str) -> FeedHealthResponse:
    """Check liveness of a feed URL. Returns is_alive, http_status, and any parse errors."""
    from app.discovery.rss_autodiscovery import _probe_single_feed

    result = await _probe_single_feed(url)
    return FeedHealthResponse(
        url=url,
        is_alive=result.get("is_alive", False),
        http_status=result.get("http_status"),
        parse_error=result.get("parse_error", ""),
    )


# Separate limiter for browser-based discovery (more restrictive)
_browser_limiter = Limiter(key_func=_get_rate_limit_key)


@_browser_limiter.limit("3/minute")
async def _discover_with_browser(req: DiscoverRequest, request: Request) -> DiscoverResponse:
    """Handler for browser-based discovery with stricter rate limits."""
    _check_inbound_token(request, require=True)
    response = await run_discovery(req)
    payload = response.model_dump(mode="json")
    discover_id = store_discovery(payload)
    response.discover_id = discover_id
    return response


@app.post("/discover", response_model=DiscoverResponse)
async def discover(req: DiscoverRequest, request: Request) -> DiscoverResponse:
    """Handle discovery requests with appropriate rate limiting."""
    if req.use_browser:
        return await _discover_with_browser(req, request)
    # Non-browser requests use the standard 30/min limit
    _check_inbound_token(request, require=False)
    response = await run_discovery(req)
    payload = response.model_dump(mode="json")
    discover_id = store_discovery(payload)
    response.discover_id = discover_id
    return response


@app.get("/discover/{discover_id}", response_model=DiscoverResponse)
async def discover_get(discover_id: str, request: Request) -> DiscoverResponse:
    _check_inbound_token(request)
    stored = load_discovery(discover_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="discover_id not found")
    stored["discover_id"] = discover_id
    return DiscoverResponse.model_validate(stored)


@limiter.limit("30/minute")
@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    _check_inbound_token(request)
    if req.discover_id and req.results is None:
        stored = load_discovery(req.discover_id)
        if stored:
            req.results = DiscoverResponse.model_validate(
                {**stored, "discover_id": req.discover_id}
            ).results
            if not req.html_skeleton:
                req.html_skeleton = stored.get("html_skeleton", "")
        else:
            raise HTTPException(status_code=400, detail="discover_id not found")
    return await recommend_strategy(req)


@limiter.limit("30/minute")
@app.post("/bridge/generate", response_model=BridgeGenerateResponse)
async def bridge_generate(req: BridgeGenerateRequest, request: Request) -> BridgeGenerateResponse:
    _check_inbound_token(request)
    if req.discover_id and req.results is None:
        stored = load_discovery(req.discover_id)
        if stored:
            req.results = DiscoverResponse.model_validate(
                {**stored, "discover_id": req.discover_id}
            ).results
            if not req.html_skeleton:
                req.html_skeleton = stored.get("html_skeleton", "")
        else:
            raise HTTPException(status_code=400, detail="discover_id not found")
    return await generate_bridge(req)


@limiter.limit("30/minute")
@app.post("/bridge/deploy", response_model=BridgeDeployResponse)
async def bridge_deploy(req: BridgeDeployRequest, request: Request) -> BridgeDeployResponse:
    """Deploy a generated RSS-Bridge PHP file.
    
    Supports multiple deployment modes:
    - auto: try local first, then remote
    - local_only: only write to local bridges directory
    - remote_only: only use remote deployment (HTTP API or SFTP)
    """
    _check_inbound_token(request)
    
    deploy_mode = req.deploy_mode or "auto"
    errors = []
    
    # Local deployment (shared volume)
    local_writable = _local_bridges_writable(_bridges_dir())
    
    if deploy_mode == "local_only":
        # Force local only
        from app.bridge.deploy import deploy_bridge
        result = deploy_bridge(req.bridge_name, req.php_code, _bridges_dir())
        return BridgeDeployResponse(
            deployed=result.deployed,
            path=result.path,
            errors=result.errors,
        )
    
    if deploy_mode == "remote_only":
        # Force remote only - check for SFTP first
        if req.sftp_host and req.sftp_user and req.sftp_target_dir:
            from app.bridge.sftp_deploy import deploy_bridge_via_sftp
            result = await deploy_bridge_via_sftp(
                name=req.bridge_name,
                code=req.php_code,
                host=req.sftp_host,
                port=req.sftp_port or 22,
                username=req.sftp_user,
                key_path=req.sftp_key_path or None,
                target_dir=req.sftp_target_dir,
            )
            return BridgeDeployResponse(
                deployed=result.deployed,
                path=result.path,
                errors=result.errors,
            )
        else:
            # Use HTTP API remote
            result = await deploy_bridge_remote(
                req.bridge_name,
                req.php_code,
                services=req.services or ServiceConfig(),
                bridges_dir=_bridges_dir(),
            )
            return BridgeDeployResponse(
                deployed=result.deployed,
                path=result.path,
                errors=result.errors,
            )
    
    # Auto mode: try local first, then remote
    if local_writable:
        from app.bridge.deploy import deploy_bridge
        result = deploy_bridge(req.bridge_name, req.php_code, _bridges_dir())
        if result.deployed:
            return BridgeDeployResponse(
                deployed=True,
                path=result.path,
                errors=[],
            )
        # Local failed, fall through to remote
    
    # Try remote deployment
    if req.sftp_host and req.sftp_user and req.sftp_target_dir:
        from app.bridge.sftp_deploy import deploy_bridge_via_sftp
        result = await deploy_bridge_via_sftp(
            name=req.bridge_name,
            code=req.php_code,
            host=req.sftp_host,
            port=req.sftp_port or 22,
            username=req.sftp_user,
            key_path=req.sftp_key_path or None,
            target_dir=req.sftp_target_dir,
        )
        return BridgeDeployResponse(
            deployed=result.deployed,
            path=result.path,
            errors=result.errors,
        )
    
    # Fall back to HTTP API remote
    result = await deploy_bridge_remote(
        req.bridge_name,
        req.php_code,
        services=req.services or ServiceConfig(),
        bridges_dir=_bridges_dir(),
    )
    return BridgeDeployResponse(
        deployed=result.deployed,
        path=result.path,
        errors=result.errors,
    )


# ── Phase 4: /scrape ──────────────────────────────────────────────────────────

@limiter.limit("30/minute")
@app.post("/scrape", response_model=ScrapeResponse)
async def scrape(req: ScrapeRequest, request: Request) -> ScrapeResponse:
    _check_inbound_token(request)
    return await run_scrape(req)


@limiter.limit("30/minute")
@app.post("/preview", response_model=PreviewResponse)
async def preview(req: ScrapeRequest, request: Request) -> PreviewResponse:
    """Preview endpoint for inline candidate previews. Caps items to 10, disables caching."""
    _check_inbound_token(request)

    # Override settings for preview: no caching, cap items
    preview_req = req.model_copy(update={
        "adaptive": False,
        "cache_key": "",
    })

    result = await run_scrape(preview_req)

    # Cap items to 10 for preview
    capped_items = result.items[:10]

    # Calculate field counts from the capped items
    field_counts = {
        "title": sum(1 for item in capped_items if item.title),
        "link": sum(1 for item in capped_items if item.link),
        "timestamp": sum(1 for item in capped_items if item.timestamp),
        "content": sum(1 for item in capped_items if item.content),
        "author": sum(1 for item in capped_items if item.author),
    }

    return PreviewResponse(
        url=result.url,
        timestamp=result.timestamp,
        strategy=result.strategy,
        items=capped_items,
        item_count=len(capped_items),
        selector_hits=result.item_count,
        field_counts=field_counts,
        fetch_backend_used=result.fetch_backend_used,
        errors=result.errors,
        warnings=result.warnings,
    )


@limiter.limit("30/minute")
@app.post("/scrape/config")
async def scrape_config_create(req: ScrapeRequest, request: Request) -> dict:
    """Save a scrape config and return its id + the Atom feed URL."""
    _check_inbound_token(request)
    payload = req.model_dump()
    config_id = save_config(
        "scrape",
        payload,
        post_process=lambda cid, p: {**p, "cache_key": cid},
    )

    sidecar_base = os.getenv("AUTOFEED_PUBLIC_URL", "http://autofeed-sidecar:8000")
    feed_url = f"{sidecar_base}/scrape/feed?id={config_id}"
    return {"config_id": config_id, "feed_url": feed_url}


@app.get("/scrape/config/{config_id}")
async def scrape_config_get(config_id: str) -> dict:
    cfg = load_config("scrape", config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return cfg


@app.delete("/scrape/config/{config_id}", status_code=204)
async def scrape_config_delete(config_id: str) -> None:
    if not delete_config("scrape", config_id):
        raise HTTPException(status_code=404, detail="Config not found")


@app.get("/scrape/feed")
async def scrape_feed(id: str) -> Response:
    """Run a saved scrape config and return Atom XML."""
    cfg = load_config("scrape", id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")

    req = ScrapeRequest.model_validate(cfg)
    result = await run_scrape(req)
    atom = _build_atom(result, feed_id=id)
    return Response(content=atom, media_type="application/atom+xml")


# ── Atom serialisation helper ─────────────────────────────────────────────────

def _build_atom(result: ScrapeResponse, feed_id: str) -> bytes:
    from feedgen.feed import FeedGenerator  # local import — optional dep
    import hashlib

    fg = FeedGenerator()
    fg.id(f"autofeed:scrape:{feed_id}")
    fg.title(result.url)
    fg.link(href=result.url)
    fg.updated(result.timestamp)
    fg.author({"name": "AutoFeed"})

    for item in result.items:
        fe = fg.add_entry()
        entry_id = item.link or (
            "autofeed:item:" + hashlib.sha256(item.title.encode()).hexdigest()[:16]
        )
        fe.id(entry_id)
        fe.title(item.title or "(untitled)")
        if item.link:
            fe.link(href=item.link)
        if item.content:
            fe.content(item.content, type="html")
        if item.author:
            fe.author({"name": item.author})
        # Parse timestamp — omit if unparseable.
        if item.timestamp:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(item.timestamp)
                fe.published(dt)
            except Exception:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(item.timestamp.rstrip("Z"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    fe.published(dt)
                except Exception:
                    pass

    return fg.atom_str(pretty=True)


# ── Phase 5: /graphql ─────────────────────────────────────────────────────────

class GraphQLProbeRequest(BaseModel):
    endpoint: str
    services: ServiceConfig = Field(default_factory=ServiceConfig)
    introspect: bool = True


class GraphQLProbeResponse(BaseModel):
    endpoint: str
    operations: list[GraphQLOperation] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


@app.post("/graphql/probe", response_model=GraphQLProbeResponse)
async def graphql_probe(req: GraphQLProbeRequest) -> GraphQLProbeResponse:
    ops = await probe_graphql_endpoint(
        req.endpoint, req.services.normalised(), introspect=req.introspect
    )
    return GraphQLProbeResponse(endpoint=req.endpoint, operations=ops)


@limiter.limit("30/minute")
@app.post("/graphql/config")
async def graphql_config_create(req: GraphQLProbeRequest, request: Request) -> dict:
    """Save a GraphQL operation config and return its id + the Atom feed URL."""
    _check_inbound_token(request)
    payload = req.model_dump()
    config_id = save_config("graphql", payload)
    sidecar_base = os.getenv("AUTOFEED_PUBLIC_URL", "http://autofeed-sidecar:8000")
    feed_url = f"{sidecar_base}/graphql/feed?id={config_id}"
    return {"config_id": config_id, "feed_url": feed_url}


@app.get("/graphql/config/{config_id}")
async def graphql_config_get(config_id: str) -> dict:
    cfg = load_config("graphql", config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return cfg


@app.delete("/graphql/config/{config_id}", status_code=204)
async def graphql_config_delete(config_id: str) -> None:
    if not delete_config("graphql", config_id):
        raise HTTPException(status_code=404, detail="Config not found")


@app.get("/graphql/feed")
async def graphql_feed(id: str) -> Response:
    """Replay a saved GraphQL operation and return Atom XML."""
    cfg = load_config("graphql", id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")

    endpoint = cfg.get("endpoint", "")
    services = ServiceConfig.model_validate(cfg.get("services") or {}).normalised()
    introspect = cfg.get("introspect", False)

    ops = await probe_graphql_endpoint(endpoint, services, introspect=introspect)

    # If probe returns nothing, re-run with the saved operation directly.
    # For now, return the probe results as an Atom feed.
    atom = _build_graphql_atom(ops, endpoint=endpoint, feed_id=id)
    return Response(content=atom, media_type="application/atom+xml")


def _build_graphql_atom(ops: list[GraphQLOperation], endpoint: str, feed_id: str) -> bytes:
    from feedgen.feed import FeedGenerator
    import hashlib

    fg = FeedGenerator()
    fg.id(f"autofeed:graphql:{feed_id}")
    fg.title(endpoint)
    fg.link(href=endpoint)
    fg.updated(datetime.now(timezone.utc))
    fg.author({"name": "AutoFeed"})

    # Key-role buckets for heuristic field mapping.
    _TITLE_KEYS = {"title", "name", "headline", "subject"}
    _LINK_KEYS = {"url", "uri", "href", "link", "permalink"}
    _CONTENT_KEYS = {"content", "body", "description", "summary", "excerpt"}
    _DATE_KEYS = {"date", "published", "created", "timestamp", "published_at", "created_at"}

    for op in ops:
        fe = fg.add_entry()
        entry_id = op.endpoint + ":" + op.operation_name
        fe.id("autofeed:gql:" + hashlib.sha256(entry_id.encode()).hexdigest()[:16])
        fe.title(op.operation_name or op.endpoint or "(untitled)")
        if op.endpoint:
            fe.link(href=op.endpoint)
        keys_lower = {k.lower() for k in op.sample_keys}
        content_parts = [f"Score: {op.feed_score:.2f}", f"Items: {op.item_count}"]
        if op.sample_keys:
            content_parts.append(f"Fields: {', '.join(op.sample_keys)}")
        fe.content("\n".join(content_parts), type="text")

    return fg.atom_str(pretty=True)


# ── Tier 3: SFTP Deployment ───────────────────────────────────────────────────

class SftpTestRequest(BaseModel):
    host: str
    port: int = 22
    user: str
    key_path: str = ""
    target_dir: str


class SftpTestResponse(BaseModel):
    ok: bool
    error: str = ""


@app.post("/sftp/test", response_model=SftpTestResponse)
async def sftp_test(req: SftpTestRequest, request: Request) -> SftpTestResponse:
    """Test SFTP connection to a remote host."""
    _check_inbound_token(request)

    from app.bridge.sftp_deploy import test_sftp_connection

    result = await test_sftp_connection(
        host=req.host,
        port=req.port,
        username=req.user,
        key_path=req.key_path or None,
        target_dir=req.target_dir,
    )

    return SftpTestResponse(
        ok=result.deployed,
        error="; ".join(result.errors) if result.errors else "",
    )


class SftpDeployRequest(BaseModel):
    name: str
    code: str
    host: str
    port: int = 22
    user: str
    key_path: str = ""
    target_dir: str


@app.post("/sftp/deploy")
async def sftp_deploy(req: SftpDeployRequest, request: Request) -> dict:
    """Deploy a bridge PHP file via SFTP."""
    _check_inbound_token(request)

    from app.bridge.sftp_deploy import deploy_bridge_via_sftp

    result = await deploy_bridge_via_sftp(
        name=req.name,
        code=req.code,
        host=req.host,
        port=req.port,
        username=req.user,
        key_path=req.key_path or None,
        target_dir=req.target_dir,
    )

    if not result.deployed:
        raise HTTPException(status_code=400, detail="; ".join(result.errors))

    return {"deployed": True, "path": result.path}
