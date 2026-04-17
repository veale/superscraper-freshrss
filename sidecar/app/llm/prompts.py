"""LLM prompt templates and rendering for AutoFeed Phase 3."""
from __future__ import annotations

from app.models.schemas import (
    AnalyzeRequest,
    APIEndpoint,
    BridgeGenerateRequest,
    EmbeddedJSON,
    RSSFeed,
    XPathCandidate,
)

# ── Strategy selection ────────────────────────────────────────────────────────

STRATEGY_SYSTEM = (
    "You are a feed-discovery assistant. "
    "Given a URL, an HTML skeleton, and candidate feed-extraction strategies already detected, "
    "pick the single best strategy for a reliable RSS feed. "
    "Prefer robustness: official RSS > stable JSON API > embedded JSON > XPath. "
    "Only pick `rss_bridge` if nothing else will produce clean, stable items. "
    "Reply with one JSON object matching the schema. No prose outside the JSON."
)

STRATEGY_USER_TEMPLATE = """\
URL: {url}
TITLE: {page_title}  FRAMEWORKS: {frameworks}  ANTI_BOT: {anti_bot}

CANDIDATES:
- rss_feeds     ({n_rss}): {rss_summary}
- api_endpoints ({n_api}): {api_summary}
- embedded_json ({n_ej}):  {ej_summary}
- xpath         ({n_xp}):  {xp_summary}

HTML SKELETON (truncated):
{skeleton}

Return JSON:
{{"strategy": "rss|json_api|embedded_json|xpath|rss_bridge",
 "confidence": 0.0-1.0,
 "selected_candidate_ref": "rss[0]|api[2]|xpath[0]|null",
 "field_overrides": {{"itemTitle": "...", "itemUri": "...", ...}},
 "reasoning": "<= 2 sentences",
 "caveats": ["..."]}}
"""

# ── Bridge generation ─────────────────────────────────────────────────────────

BRIDGE_SYSTEM = (
    "You are an RSS-Bridge plugin author. "
    "Produce one self-contained PHP file subclassing `BridgeAbstract` with a `collectData()` method "
    "yielding items with `title`, `uri`, `content`, `timestamp`, optional `enclosures`. "
    "Rules: (1) begins with `<?php`, no closing tag; "
    "(2) class name = `{BridgeName}Bridge`, file `{BridgeName}Bridge.php`; "
    "(3) use `getSimpleHTMLDOM()`, not cURL; "
    "(4) no network calls in constructor; "
    "(5) `CACHE_TIMEOUT = 3600`; "
    "(6) no shell/file/eval. "
    'Respond with JSON {"bridge_name": "...", "php_code": "..."}. Nothing else.'
)

BRIDGE_USER_TEMPLATE = """\
TARGET URL: {url}
TITLE: {page_title}
HINT: {hint}

CANDIDATES (compact):
- rss_feeds     ({n_rss}): {rss_summary}
- api_endpoints ({n_api}): {api_summary}
- embedded_json ({n_ej}):  {ej_summary}
- xpath         ({n_xp}):  {xp_summary}

HTML SKELETON (truncated):
{skeleton}
"""


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_strategy_prompt(req: AnalyzeRequest) -> tuple[str, str]:
    """Return (system, user) strings for strategy selection."""
    r = req.results
    skeleton = req.html_skeleton or r.html_skeleton
    user = STRATEGY_USER_TEMPLATE.format(
        url=req.url,
        page_title=r.page_meta.page_title or "(unknown)",
        frameworks=", ".join(r.page_meta.frameworks_detected) or "none",
        anti_bot=str(r.page_meta.anti_bot_detected).lower(),
        n_rss=len(r.rss_feeds),
        rss_summary=_rss_summary(r.rss_feeds),
        n_api=len(r.api_endpoints),
        api_summary=_api_summary(r.api_endpoints),
        n_ej=len(r.embedded_json),
        ej_summary=_ej_summary(r.embedded_json),
        n_xp=len(r.xpath_candidates),
        xp_summary=_xp_summary(r.xpath_candidates),
        skeleton=skeleton[:8_000] if skeleton else "(not available)",
    )
    return STRATEGY_SYSTEM, user


def render_bridge_prompt(req: BridgeGenerateRequest) -> tuple[str, str]:
    """Return (system, user) strings for bridge generation."""
    r = req.results
    skeleton = req.html_skeleton or r.html_skeleton
    user = BRIDGE_USER_TEMPLATE.format(
        url=req.url,
        page_title=r.page_meta.page_title or "(unknown)",
        hint=req.hint or "none",
        n_rss=len(r.rss_feeds),
        rss_summary=_rss_summary(r.rss_feeds),
        n_api=len(r.api_endpoints),
        api_summary=_api_summary(r.api_endpoints),
        n_ej=len(r.embedded_json),
        ej_summary=_ej_summary(r.embedded_json),
        n_xp=len(r.xpath_candidates),
        xp_summary=_xp_summary(r.xpath_candidates),
        skeleton=skeleton[:8_000] if skeleton else "(not available)",
    )
    return BRIDGE_SYSTEM, user


# ── Summary helpers ───────────────────────────────────────────────────────────

def _rss_summary(feeds: list[RSSFeed]) -> str:
    if not feeds:
        return "none"
    return "; ".join(
        f"{f.url} ({f.title or 'no title'})" for f in feeds[:3]
    )


def _api_summary(endpoints: list[APIEndpoint]) -> str:
    if not endpoints:
        return "none"
    parts = []
    for ep in endpoints[:3]:
        keys = ",".join(ep.sample_keys[:5])
        parts.append(f"{ep.url} score={ep.feed_score:.2f} keys=[{keys}]")
    return "; ".join(parts)


def _ej_summary(embedded: list[EmbeddedJSON]) -> str:
    if not embedded:
        return "none"
    parts = []
    for ej in embedded[:3]:
        keys = ",".join(ej.sample_keys[:5])
        parts.append(f"source={ej.source} path={ej.path} keys=[{keys}]")
    return "; ".join(parts)


def _xp_summary(candidates: list[XPathCandidate]) -> str:
    if not candidates:
        return "none"
    parts = []
    for xp in candidates[:3]:
        parts.append(
            f"selector={xp.item_selector} confidence={xp.confidence:.2f} items={xp.item_count}"
        )
    return "; ".join(parts)
