"""Pydantic models for the AutoFeed sidecar API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.services.config import ServiceConfig


# ── Enums ────────────────────────────────────────────────────────────────────

class FeedCadence(str, Enum):
    ON_DEMAND  = "on_demand"
    QUARTER    = "15m"
    HOURLY     = "1h"
    SIX_HOURLY = "6h"
    DAILY      = "1d"
    WEEKLY     = "1w"


class FeedStrategy(str, Enum):
    RSS = "rss"
    JSON_API = "json_api"
    JSON_DOT_NOTATION = "json_dot_notation"
    XPATH = "xpath"
    XML_XPATH = "xml_xpath"
    EMBEDDED_JSON = "embedded_json"
    RSS_BRIDGE = "rss_bridge"
    GRAPHQL = "graphql"


# ── Discovery request / response ─────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    url: str = Field(..., description="URL to discover feeds from")
    timeout: int = Field(30, ge=5, le=120, description="Discovery timeout in seconds")
    use_browser: bool = Field(
        False,
        description="Force browser-based discovery (Phase 2) even if RSS is found",
    )
    force_skip_rss: bool = Field(
        False,
        description="Treat any discovered RSS feed as if it were missing, forcing Phase 2",
    )
    force_stealth: bool = Field(
        False,
        description="Force stealth mode (anti-bot evasion) even if not detected",
    )
    services: ServiceConfig = Field(default_factory=ServiceConfig)
    initial_examples: list[dict[str, str]] = Field(
        default_factory=list,
        description="Optional per-row example field-sets from real items. "
                    "Index 0 describes one item, index 1 a possibly different "
                    "item (used to derive cross-family union selectors).",
    )


class RSSFeed(BaseModel):
    url: str
    title: Optional[str] = None
    feed_type: str = "rss"  # rss | atom | json_feed
    is_alive: bool = True
    http_status: Optional[int] = None
    parse_error: str = ""


class PaginationSpec(BaseModel):
    """How to paginate an API endpoint during replay.

    *location* says whether the paginator lives in the request body (JSON) or
    the URL query string. *kind* picks the step semantics: 'page' increments
    by one, 'offset' advances by *per_page*, 'cursor' uses the value read from
    *next_cursor_path* in the previous response.
    """

    location: str = "body"       # body | query
    param: str = ""              # e.g. pageNumber | page | offset | cursor
    kind: str = "page"           # page | offset | cursor
    start: int = 1               # first page/offset to request (cursors ignore this)
    per_page: int = 0            # page size when known (for offset pagination)
    per_page_param: str = ""     # if the API exposes a page-size param
    has_more_path: str = ""      # dot-path in response that yields a truthy "more" flag
    next_cursor_path: str = ""   # dot-path to next-cursor value
    total_pages_path: str = ""   # dot-path to total pages (stop when reached)


class APIEndpoint(BaseModel):
    url: str
    method: str = "GET"
    content_type: str = ""
    item_count: int = 0
    sample_keys: list[str] = Field(default_factory=list)
    sample_item: Optional[dict[str, Any]] = None
    feed_score: float = 0.0
    field_mapping: dict[str, str] = Field(default_factory=dict)
    item_path: str = ""
    request_body: str = ""        # raw request body as captured (usually JSON text)
    request_headers: dict[str, str] = Field(default_factory=dict)
    pagination: Optional[PaginationSpec] = None


class EmbeddedJSON(BaseModel):
    source: str = ""  # e.g. "script#__NEXT_DATA__"
    path: str = ""  # dot-notation path to the feed-like array
    item_count: int = 0
    sample_keys: list[str] = Field(default_factory=list)
    sample_item: Optional[dict[str, Any]] = None  # Sample item for LLM context
    feed_score: float = 0.0
    field_mapping: dict[str, str] = Field(default_factory=dict)


class XPathCandidate(BaseModel):
    item_selector: str
    title_selector: str = ""
    link_selector: str = ""
    content_selector: str = ""
    timestamp_selector: str = ""
    author_selector: str = ""
    thumbnail_selector: str = ""
    confidence: float = 0.0
    item_count: int = 0
    item_selector_union: bool = False


class PageMeta(BaseModel):
    has_javascript_content: bool = False
    frameworks_detected: list[str] = Field(default_factory=list)
    anti_bot_detected: bool = False
    page_title: str = ""
    canonical_url: str = ""


class GraphQLOperation(BaseModel):
    """A captured (or introspected) GraphQL operation that produces feed-like data."""
    endpoint: str
    operation_name: str = ""
    operation_type: str = "query"
    query: str = ""
    variables: dict[str, Any] = Field(default_factory=dict)
    response_path: str = ""
    item_count: int = 0
    sample_keys: list[str] = Field(default_factory=list)
    feed_score: float = 0.0
    detected_via: str = ""


class DiscoveryResults(BaseModel):
    rss_feeds: list[RSSFeed] = Field(default_factory=list)
    api_endpoints: list[APIEndpoint] = Field(default_factory=list)
    embedded_json: list[EmbeddedJSON] = Field(default_factory=list)
    xpath_candidates: list[XPathCandidate] = Field(default_factory=list)
    graphql_operations: list[GraphQLOperation] = Field(default_factory=list)
    page_meta: PageMeta = Field(default_factory=PageMeta)
    html_skeleton: str = ""
    phase2_used: bool = False
    stealth_used: bool = False
    force_skip_rss: bool = False
    # Global refine examples stored when user uses the global refine block
    refine_examples: dict[str, list[str]] = Field(default_factory=dict)
    # Per-candidate refinements stored when user uses per-candidate refine
    # Shape: {index: {field_selector: value, ...}}
    candidate_refinements: dict[str, dict[str, str]] = Field(default_factory=dict)
    backend_used: str = ""


class DiscoverResponse(BaseModel):
    url: str
    timestamp: datetime
    results: DiscoveryResults
    errors: list[str] = Field(default_factory=list)
    discover_id: str = ""
    # Transient: browser-rendered HTML from phase 2. Never serialised to the
    # discovery cache JSON — stored separately via store_browser_html().
    browser_html: str = Field(default="", exclude=True)


# ── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.6.0"
    phase: int = 5


# ── Phase 3: LLM analysis ────────────────────────────────────────────────────

class LLMConfig(BaseModel):
    endpoint: str
    api_key: str = ""
    model: str
    timeout: int = Field(60, ge=5, le=300)


class AnalyzeRequest(BaseModel):
    url: str
    results: DiscoveryResults | None = None
    html_skeleton: str = ""
    llm: LLMConfig | None = None   # if omitted, filled from server settings_store
    discover_id: str = ""


class LLMRecommendation(BaseModel):
    strategy: FeedStrategy
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    selected_candidate_ref: Optional[str] = None
    field_overrides: dict[str, str] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    url: str
    recommendation: Optional[LLMRecommendation] = None
    llm_raw: Optional[dict[str, Any]] = None
    tokens_used: Optional[int] = None
    errors: list[str] = Field(default_factory=list)


# ── Phase 3: RSS-Bridge generation / deployment ───────────────────────────────

class BridgeGenerateRequest(BaseModel):
    url: str
    html_skeleton: str = ""
    results: DiscoveryResults | None = None
    llm: LLMConfig | None = None   # if omitted, filled from server settings_store
    hint: str = ""
    discover_id: str = ""


class BridgeGenerateResponse(BaseModel):
    bridge_name: str = ""
    filename: str = ""
    php_code: str = ""
    sanity_warnings: list[str] = Field(default_factory=list)
    soft_warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class BridgeDeployRequest(BaseModel):
    bridge_name: str
    php_code: str
    services: Optional[ServiceConfig] = None
    # Tier 3: deployment mode
    deploy_mode: str = "auto"  # "auto", "local_only", "remote_only"
    # SFTP config (used when deploy_mode is remote_only or for SFTP)
    sftp_host: str = ""
    sftp_port: int = 22
    sftp_user: str = ""
    sftp_key_path: str = ""
    sftp_target_dir: str = ""


class BridgeDeployResponse(BaseModel):
    deployed: bool = False
    path: str = ""
    errors: list[str] = Field(default_factory=list)


# ── Phase 4: routine scraping ─────────────────────────────────────────────────

class ScrapeSelectors(BaseModel):
    """One of three selector modes — exactly one of these blocks should be filled."""
    item: str = ""
    item_title: str = ""
    item_link: str = ""
    item_content: str = ""
    item_timestamp: str = ""
    item_thumbnail: str = ""
    item_author: str = ""
    example_text: str = ""      # item-level text for AutoScraper-style item recovery
    # Plural examples for per-field recovery (multiple examples for union selectors)
    title_examples: list[str] = Field(default_factory=list)
    link_examples: list[str] = Field(default_factory=list)
    content_examples: list[str] = Field(default_factory=list)
    timestamp_examples: list[str] = Field(default_factory=list)
    author_examples: list[str] = Field(default_factory=list)
    thumbnail_examples: list[str] = Field(default_factory=list)

    # Legacy singular fields (for migration from saved configs)
    title_example: str = ""
    link_example: str = ""
    content_example: str = ""
    timestamp_example: str = ""
    author_example: str = ""
    thumbnail_example: str = ""

    @model_validator(mode="before")
    @classmethod
    def _migrate_singular_to_plural(cls, data: Any) -> Any:
        """Migrate singular *_example fields to plural *_examples lists."""
        if isinstance(data, dict):
            # Map of singular to plural field names
            singular_to_plural = {
                "title_example": "title_examples",
                "link_example": "link_examples",
                "content_example": "content_examples",
                "timestamp_example": "timestamp_examples",
                "author_example": "author_examples",
                "thumbnail_example": "thumbnail_examples",
            }
            for singular, plural in singular_to_plural.items():
                # If plural doesn't exist but singular does, migrate
                if singular in data:
                    singular_val = data[singular]
                    if singular_val and isinstance(singular_val, str):
                        # Add to plural list if not already present
                        if plural not in data or not data[plural]:
                            data[plural] = [singular_val]
                        elif isinstance(data[plural], list) and singular_val not in data[plural]:
                            data[plural].append(singular_val)
        return data


class ScrapeRequest(BaseModel):
    url: str
    strategy: FeedStrategy
    selectors: ScrapeSelectors = Field(default_factory=ScrapeSelectors)
    graphql: Optional[GraphQLOperation] = None
    services: ServiceConfig = Field(default_factory=ServiceConfig)
    timeout: int = Field(30, ge=5, le=120)
    adaptive: bool = True
    cache_key: str = ""
    max_pages: int = Field(1, ge=1, le=50)
    max_items: int = Field(250, ge=1, le=5000)
    stealth: bool = False
    solve_cloudflare: bool = False
    method: str = "GET"
    request_body: str = ""
    request_headers: dict[str, str] = Field(default_factory=dict)
    pagination: Optional[PaginationSpec] = None


class ScrapeItem(BaseModel):
    title: str = ""
    link: str = ""
    content: str = ""
    timestamp: str = ""
    thumbnail: str = ""
    author: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class ScrapeResponse(BaseModel):
    url: str
    timestamp: datetime
    strategy: FeedStrategy
    items: list[ScrapeItem] = Field(default_factory=list)
    item_count: int = 0
    drift_detected: bool = False
    cache_hit: bool = False
    fetch_backend_used: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    """Response from /preview used for inline candidate previews."""

    url: str
    timestamp: datetime
    strategy: FeedStrategy
    items: list[ScrapeItem] = Field(default_factory=list)
    item_count: int = 0
    selector_hits: int = 0
    field_counts: dict[str, int] = Field(default_factory=dict)
    fetch_backend_used: str = ""
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ── Saved feeds ───────────────────────────────────────────────────────────────

class SavedFeed(BaseModel):
    id: str
    name: str
    strategy: FeedStrategy
    source_url: str
    feed_url: str
    config_id: str = ""
    cadence: FeedCadence = FeedCadence.DAILY
    fetch_backend_override: str = ""
    stealth: bool = False
    solve_cloudflare: bool = False
    llm_suggested: bool = False
    created_at: datetime
    last_refresh_at: Optional[datetime] = None
    last_refresh_ok: Optional[bool] = None
    last_error: str = ""
    cached_atom_path: str = ""
    consecutive_empty_refreshes: int = 0
    pending_llm_update: Optional[LLMRecommendation] = None
