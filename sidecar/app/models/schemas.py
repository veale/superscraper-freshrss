"""Pydantic models for the AutoFeed sidecar API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl


# ── Enums ────────────────────────────────────────────────────────────────────

class FeedStrategy(str, Enum):
    RSS = "rss"
    JSON_API = "json_api"
    JSON_DOT_NOTATION = "json_dot_notation"
    XPATH = "xpath"
    XML_XPATH = "xml_xpath"
    EMBEDDED_JSON = "embedded_json"
    RSS_BRIDGE = "rss_bridge"


# ── Discovery request / response ─────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    url: str = Field(..., description="URL to discover feeds from")
    timeout: int = Field(30, ge=5, le=120, description="Discovery timeout in seconds")
    use_browser: bool = Field(
        False,
        description="Force browser-based discovery (Phase 2) even if RSS is found",
    )


class RSSFeed(BaseModel):
    url: str
    title: Optional[str] = None
    feed_type: str = "rss"  # rss | atom | json_feed


class APIEndpoint(BaseModel):
    url: str
    method: str = "GET"
    content_type: str = ""
    item_count: int = 0
    sample_keys: list[str] = Field(default_factory=list)
    sample_item: Optional[dict[str, Any]] = None
    feed_score: float = 0.0


class EmbeddedJSON(BaseModel):
    source: str = ""  # e.g. "script#__NEXT_DATA__"
    path: str = ""  # dot-notation path to the feed-like array
    item_count: int = 0
    sample_keys: list[str] = Field(default_factory=list)
    feed_score: float = 0.0


class XPathCandidate(BaseModel):
    item_selector: str
    title_selector: str = ""
    link_selector: str = ""
    content_selector: str = ""
    timestamp_selector: str = ""
    thumbnail_selector: str = ""
    confidence: float = 0.0
    item_count: int = 0


class PageMeta(BaseModel):
    has_javascript_content: bool = False
    frameworks_detected: list[str] = Field(default_factory=list)
    anti_bot_detected: bool = False
    page_title: str = ""
    canonical_url: str = ""


class DiscoveryResults(BaseModel):
    rss_feeds: list[RSSFeed] = Field(default_factory=list)
    api_endpoints: list[APIEndpoint] = Field(default_factory=list)
    embedded_json: list[EmbeddedJSON] = Field(default_factory=list)
    xpath_candidates: list[XPathCandidate] = Field(default_factory=list)
    page_meta: PageMeta = Field(default_factory=PageMeta)
    html_skeleton: str = ""


class DiscoverResponse(BaseModel):
    url: str
    timestamp: datetime
    results: DiscoveryResults
    errors: list[str] = Field(default_factory=list)


# ── Health ───────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.3.0"
    phase: int = 3


# ── Phase 3: LLM analysis ────────────────────────────────────────────────────

class LLMConfig(BaseModel):
    endpoint: str
    api_key: str = ""
    model: str
    timeout: int = Field(60, ge=5, le=300)


class AnalyzeRequest(BaseModel):
    url: str
    results: DiscoveryResults
    html_skeleton: str = ""
    llm: LLMConfig


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
    results: DiscoveryResults
    llm: LLMConfig
    hint: str = ""


class BridgeGenerateResponse(BaseModel):
    bridge_name: str = ""
    filename: str = ""
    php_code: str = ""
    sanity_warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class BridgeDeployRequest(BaseModel):
    bridge_name: str
    php_code: str


class BridgeDeployResponse(BaseModel):
    deployed: bool = False
    path: str = ""
    errors: list[str] = Field(default_factory=list)
