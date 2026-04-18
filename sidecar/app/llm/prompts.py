"""LLM prompt templates and rendering for AutoFeed Phase 3."""
from __future__ import annotations

from app.models.schemas import (
    AnalyzeRequest,
    APIEndpoint,
    BridgeGenerateRequest,
    EmbeddedJSON,
    GraphQLOperation,
    RSSFeed,
    XPathCandidate,
)

# ── Strategy selection ────────────────────────────────────────────────────────

STRATEGY_SYSTEM = (
    "You are a feed-discovery assistant. "
    "Given a URL, an HTML skeleton, and candidate feed-extraction strategies already detected, "
    "pick the single best strategy for a reliable RSS feed. "
    "Prefer robustness: official RSS > stable JSON API > embedded JSON > graphql > XPath. "
    "Prefer `graphql` over `xpath` when GraphQL operations are available, but rank it below "
    "`rss` and below a stable `json_api` (GraphQL endpoints often require auth headers or "
    "specific Content-Type that FreshRSS may not send). "
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
- graphql       ({n_gql}): {gql_summary}
- xpath         ({n_xp}):  {xp_summary}

HTML SKELETON (truncated):
{skeleton}

Return JSON:
{{"strategy": "rss|json_api|embedded_json|graphql|xpath|rss_bridge",
 "confidence": 0.0-1.0,
 "selected_candidate_ref": "rss[0]|api[2]|gql[0]|xpath[0]|null",
 "field_overrides": {{"itemTitle": "...", "itemUri": "...", ...}},
 "reasoning": "<= 2 sentences",
 "caveats": ["..."]}}
"""

# ── Bridge generation ─────────────────────────────────────────────────────────

BRIDGE_SYSTEM = (
    "You are an RSS-Bridge plugin author. Produce one self-contained PHP file "
    "subclassing `BridgeAbstract` that returns feed items via `collectData()`. "
    "\n\n"
    "REQUIRED CLASS CONSTANTS:\n"
    "- const NAME: short human name, e.g. 'Example Blog'\n"
    "- const URI: canonical site URL\n"
    "- const DESCRIPTION: one sentence\n"
    "- const MAINTAINER = 'AutoFeed-LLM' (non-negotiable; use single quotes)\n"
    "- const CACHE_TIMEOUT: seconds as integer, default 3600\n"
    "- const PARAMETERS: array keyed by context name. Use '[]' if no "
    "user-configurable inputs are needed. Every parameter must have "
    "'name', optional 'type' (text|number|checkbox|list), optional 'required', "
    "and optional 'exampleValue'.\n"
    "\n"
    "REQUIRED METHOD:\n"
    "- public function collectData(): fetch data, populate $this->items[] with "
    "associative arrays containing at minimum 'title', 'uri', 'content', "
    "'timestamp' (unix epoch or strtotime-parseable string), and optionally "
    "'author', 'enclosures' (array of URLs), 'categories' (array of strings).\n"
    "\n"
    "OPTIONAL CAPABILITIES:\n"
    "- detectParameters($url): implement only if the site's URL structure "
    "  maps cleanly to parameters. This is for AutoFeed's auto-subscribe flow.\n"
    "\n"
    "FETCHING RULES:\n"
    "- Use `getSimpleHTMLDOM($url)` for HTML pages, `getContents($url)` for "
    "JSON/XML, or `Json::decode()` to parse JSON strings. Never use cURL.\n"
    "- Respect `CACHE_TIMEOUT` — do not add per-request delays.\n"
    "\n"
    "FORBIDDEN:\n"
    "- No `?>` closing tag. No `shell_exec`, `exec()`, `system()`, `passthru()`, "
    "`eval()`, `file_get_contents()` on local paths.\n"
    "- No network calls in the constructor. No `__construct` override unless "
    "calling `parent::__construct($cache, $logger)` and nothing else.\n"
    "- No hard-coded authentication credentials.\n"
    "\n"
    "OUTPUT:\n"
    "Respond with JSON matching exactly: "
    '{"bridge_name": "ExampleBlogBridge", "php_code": "<?php\\n…"}. '
    "The `bridge_name` value must match `^[A-Z][A-Za-z0-9]*Bridge$` and include "
    "the `Bridge` suffix. The class name in the code must equal `bridge_name` "
    "and extend BridgeAbstract." 
    "No prose outside the JSON object."
)

_BRIDGE_ONE_SHOT = '''\
Reference example of a minimal valid bridge:

```php
<?php
class ExampleBlogBridge extends BridgeAbstract
{
    const NAME = 'Example Blog';
    const URI = 'https://example.com';
    const DESCRIPTION = 'Latest posts from Example Blog';
    const MAINTAINER = 'AutoFeed-LLM';
    const CACHE_TIMEOUT = 3600;
    const PARAMETERS = [];

    public function collectData()
    {
        $html = getSimpleHTMLDOM($this->getURI());
        foreach ($html->find('article.post') as $article) {
            $link = $article->find('h2 a', 0);
            $this->items[] = [
                'title'     => $link->plaintext,
                'uri'       => urljoin($this->getURI(), $link->href),
                'content'   => $article->find('div.excerpt', 0)->innertext ?? '',
                'timestamp' => strtotime($article->find('time', 0)->datetime ?? 'now'),
                'author'    => $article->find('.byline', 0)->plaintext ?? '',
            ];
        }
    }
}
```
End of reference example. Now generate one for the target URL below.
'''

BRIDGE_USER_TEMPLATE = """\
{one_shot}
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
        n_gql=len(r.graphql_operations),
        gql_summary=_gql_summary(r.graphql_operations),
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
        one_shot=_BRIDGE_ONE_SHOT,
    )
    return BRIDGE_SYSTEM, user


# ── Summary helpers ───────────────────────────────────────────────────────────

SUMMARY_MAX_LEN = 1500  # Cap each summary to 1500 chars post-render


def _cap_summary(text: str, total_count: int, shown_count: int) -> str:
    """Cap summary to SUMMARY_MAX_LEN, append '...and N more' if truncated."""
    if len(text) <= SUMMARY_MAX_LEN:
        return text
    remaining = total_count - shown_count
    truncated = text[:SUMMARY_MAX_LEN]
    if remaining > 0:
        return f"{truncated}...and {remaining} more"
    return f"{truncated}..."


def _rss_summary(feeds: list[RSSFeed]) -> str:
    if not feeds:
        return "none"
    total = len(feeds)
    shown = feeds[:3]
    text = "; ".join(
        f"{f.url} ({f.title or 'no title'})" for f in shown
    )
    return _cap_summary(text, total, len(shown))


def _api_summary(endpoints: list[APIEndpoint]) -> str:
    if not endpoints:
        return "none"
    total = len(endpoints)
    shown = endpoints[:3]
    parts = []
    for ep in shown:
        keys = ",".join(ep.sample_keys[:5])
        parts.append(f"{ep.url} score={ep.feed_score:.2f} keys=[{keys}]")
    text = "; ".join(parts)
    return _cap_summary(text, total, len(shown))


def _ej_summary(embedded: list[EmbeddedJSON]) -> str:
    if not embedded:
        return "none"
    total = len(embedded)
    shown = embedded[:3]
    parts = []
    for ej in shown:
        keys = ",".join(ej.sample_keys[:5])
        parts.append(f"source={ej.source} path={ej.path} keys=[{keys}]")
    text = "; ".join(parts)
    return _cap_summary(text, total, len(shown))


def _gql_summary(ops: list[GraphQLOperation]) -> str:
    if not ops:
        return "none"
    total = len(ops)
    shown = ops[:3]
    parts = []
    for op in shown:
        name = op.operation_name or "(anonymous)"
        parts.append(
            f"{op.endpoint} op={name} score={op.feed_score:.2f} items={op.item_count}"
        )
    text = "; ".join(parts)
    return _cap_summary(text, total, len(shown))


def _xp_summary(candidates: list[XPathCandidate]) -> str:
    if not candidates:
        return "none"
    total = len(candidates)
    shown = candidates[:3]
    parts = []
    for xp in shown:
        parts.append(
            f"selector={xp.item_selector} confidence={xp.confidence:.2f} items={xp.item_count}"
        )
    text = "; ".join(parts)
    return _cap_summary(text, total, len(shown))
