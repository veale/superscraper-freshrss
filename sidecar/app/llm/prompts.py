"""LLM prompt templates and rendering for AutoFeed Phase 3."""
from __future__ import annotations

import json
from typing import Any

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

_RSS_PREFERRED = (
    "Prefer robustness in this order: rss > json_api > graphql > embedded_json > xpath."
)

_RSS_DISQUALIFIED = (
    "IMPORTANT: the user has explicitly asked to ignore RSS feeds on this page "
    "(they already know the advertised RSS is wrong, dead, or too generic). "
    "Treat RSS as disqualified. Prefer robustness in this order: "
    "json_api > graphql > embedded_json > xpath > rss_bridge. Under no circumstances "
    "return 'rss' as the strategy."
)

_STRATEGY_SYSTEM_BASE = (
    "You are a feed-discovery assistant. "
    "Given a URL, an HTML skeleton, and candidate feed-extraction strategies already detected, "
    "pick the single best strategy for a reliable RSS feed. "
    "{ordering_clause} "
    "Do NOT pick rss_bridge unless every other strategy has been evaluated and is unusable "
    "(e.g. heavy session state, anti-bot that only works with authenticated browser sessions, "
    "site-specific OAuth). When in doubt between xpath and rss_bridge, pick xpath. "
    "rss_bridge requires an external PHP runtime and is expensive to maintain; avoid it "
    "whenever a native strategy is viable. "
    "Reply with one JSON object matching the schema. No prose outside the JSON."
)

STRATEGY_SYSTEM = _STRATEGY_SYSTEM_BASE.format(ordering_clause=_RSS_PREFERRED)

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
    from app.models.schemas import DiscoveryResults as _DR
    r = req.results or _DR()
    skeleton = req.html_skeleton or r.html_skeleton
    ordering = _RSS_DISQUALIFIED if r.force_skip_rss else _RSS_PREFERRED
    system = _STRATEGY_SYSTEM_BASE.format(ordering_clause=ordering)
    rss_for_prompt = [] if r.force_skip_rss else r.rss_feeds
    user = STRATEGY_USER_TEMPLATE.format(
        url=req.url,
        page_title=r.page_meta.page_title or "(unknown)",
        frameworks=", ".join(r.page_meta.frameworks_detected) or "none",
        anti_bot=str(r.page_meta.anti_bot_detected).lower(),
        n_rss=len(rss_for_prompt),
        rss_summary=_rss_summary(rss_for_prompt) if rss_for_prompt else "none (user-excluded)",
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
    return system, user


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

SUMMARY_MAX_LEN = 3000  # Cap each summary to 3000 chars post-render


def _truncate_values(obj, max_str=80):
    """Recursively truncate string values in a dict/list so we don't
    ship huge content to the LLM."""
    if isinstance(obj, dict):
        return {k: _truncate_values(v, max_str) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_values(v, max_str) for v in obj[:3]]
    if isinstance(obj, str) and len(obj) > max_str:
        return obj[:max_str] + "…"
    return obj


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
    """Summarize API endpoints with sample values for richer LLM context."""
    if not endpoints:
        return "none"
    total = len(endpoints)
    shown = endpoints[:3]
    parts = []
    for ep in shown:
        sample_str = ""
        if ep.sample_item:
            # Cap each string value at 80 chars, don't ship huge content blobs
            trimmed = _truncate_values(ep.sample_item, max_str=80)
            sample_str = json.dumps(trimmed, ensure_ascii=False)[:500]
        keys = ",".join(ep.sample_keys[:8])
        parts.append(
            f"{ep.url} score={ep.feed_score:.2f} method={ep.method}\n"
            f"  keys=[{keys}]\n"
            f"  sample={sample_str or '(no sample captured)'}"
        )
    text = "\n".join(parts)
    return _cap_summary(text, total, len(shown))


def _ej_summary(embedded: list[EmbeddedJSON]) -> str:
    """Summarize embedded JSON with sample values for richer LLM context."""
    if not embedded:
        return "none"
    total = len(embedded)
    shown = embedded[:3]
    parts = []
    for ej in shown:
        sample_str = ""
        if ej.sample_item:
            # Cap each string value at 80 chars, don't ship huge content blobs
            trimmed = _truncate_values(ej.sample_item, max_str=80)
            sample_str = json.dumps(trimmed, ensure_ascii=False)[:500]
        keys = ",".join(ej.sample_keys[:8])
        parts.append(
            f"source={ej.source} path={ej.path} score={ej.feed_score:.2f}\n"
            f"  keys=[{keys}]\n"
            f"  sample={sample_str or '(no sample captured)'}"
        )
    text = "\n".join(parts)
    return _cap_summary(text, total, len(shown))


def _gql_summary(ops: list[GraphQLOperation]) -> str:
    """Summarize GraphQL operations with query text, variables, and detected_via."""
    if not ops:
        return "none"
    total = len(ops)
    shown = ops[:3]
    parts = []
    for op in shown:
        name = op.operation_name or "(anonymous)"
        # Truncate query aggressively — 400 chars is enough to see the shape
        query_snippet = (op.query or "").strip()[:400]
        if len(op.query or "") > 400:
            query_snippet += "…"
        keys = ",".join(op.sample_keys[:8])
        variables = ",".join(op.variables.keys())[:100] if op.variables else ""
        parts.append(
            f"endpoint={op.endpoint}\n"
            f"  op={name} type={op.operation_type} via={op.detected_via}\n"
            f"  score={op.feed_score:.2f} items={op.item_count}\n"
            f"  response_path={op.response_path or '(root)'}\n"
            f"  variables=[{variables}]\n"
            f"  sample_keys=[{keys}]\n"
            f"  query: {query_snippet}"
        )
    text = "\n".join(parts)
    return _cap_summary(text, total, len(shown))


API_MAP_SYSTEM = (
    "You are a feed-mapping assistant. Given a sample JSON response body from a "
    "website's internal API, figure out which key path holds the list of feed "
    "items and which field of each item plays which role (title, link, content, "
    "timestamp, author, thumbnail). Dot-paths may be nested (e.g. 'data.results' "
    "or 'props.pageProps.posts'). Field mapping values are the key NAME inside "
    "each item (e.g. 'title', 'slug', 'createdDateTime'). If a field sits inside "
    "a nested object (e.g. `author.name`), return the dotted path. If a field "
    "looks like a slug/path and needs a base URL prepended, still return that "
    "field name (scraping will urljoin it). Only return fields you can justify "
    "from the sample. No prose. JSON only."
)

API_MAP_USER_TEMPLATE = """\
SITE URL: {site_url}
ENDPOINT: {method} {endpoint_url}
CONTENT TYPE: {content_type}
DETECTED ITEM PATH (may be wrong): {detected_item_path}
DETECTED FIELD MAPPING (may be incomplete): {detected_mapping}

REQUEST BODY (if POST): {request_body}

RESPONSE SAMPLE (may be truncated):
{response_sample}

Return JSON:
{{
  "item_path": "dot.path.to.list",
  "field_mapping": {{
    "title": "title|headline|name|...",
    "link":  "url|slug|path|...",
    "content": "abstract|body|description|...",
    "timestamp": "publishedAt|createdDateTime|...",
    "author": "byline|author.name|...",
    "thumbnail": "coverImage.url|image|..."
  }},
  "reasoning": "<= 2 sentences on why these fields were chosen",
  "caveats": ["e.g. link is a slug — prepend site origin", "..."]
}}
Omit any field role you're unsure about. Do not invent keys that aren't in the sample.
"""


def render_api_map_prompt(
    *,
    site_url: str,
    endpoint_url: str,
    method: str,
    content_type: str,
    detected_item_path: str,
    detected_mapping: dict[str, str],
    request_body: str,
    response_sample: Any,
) -> tuple[str, str]:
    sample_str = ""
    if response_sample is not None:
        try:
            sample_str = json.dumps(response_sample, ensure_ascii=False)[:8000]
        except Exception:
            sample_str = str(response_sample)[:8000]
    return API_MAP_SYSTEM, API_MAP_USER_TEMPLATE.format(
        site_url=site_url or "(unknown)",
        endpoint_url=endpoint_url,
        method=method,
        content_type=content_type or "application/json",
        detected_item_path=detected_item_path or "(none)",
        detected_mapping=json.dumps(detected_mapping or {}, ensure_ascii=False),
        request_body=(request_body or "(none)")[:1000],
        response_sample=sample_str or "(not captured)",
    )


# ── Recipe debug (Round 3) ────────────────────────────────────────────────────

DEBUG_RECIPE_SYSTEM = (
    "You are debugging a failing feed recipe. The user built an extraction "
    "recipe (selectors or field mapping) that previewed 0 items or the wrong "
    "items. Given the current recipe, the observed preview output (errors, "
    "warnings, item counts), and a sample of the source content (HTML for "
    "xpath strategies, JSON for json_api / embedded_json), propose a diff: "
    "only the fields you want to change. Keep your changes minimal — do not "
    "rewrite fields that already work. Explain your reasoning in <= 2 "
    "sentences. If the source sample looks insufficient to debug (e.g. "
    "placeholder HTML, auth wall), say so in caveats rather than guessing. "
    "Return JSON only."
)

DEBUG_RECIPE_USER_TEMPLATE = """\
STRATEGY: {strategy}
FEED URL: {url}
CURRENT RECIPE:
{recipe}

PREVIEW RESULT:
items_returned: {item_count}
errors: {errors}
warnings: {warnings}
sample_items_extracted: {sample_items}

SOURCE SAMPLE (truncated):
{source_sample}

Return JSON:
{{
  "diff": {{
    // Include ONLY fields you want to change. Omit ones that look correct.
    // For xpath: "item_selector", "title_selector", "link_selector",
    //   "content_selector", "timestamp_selector".
    // For json_api / embedded_json: "item_path", "item_title", "item_link",
    //   "item_content", "item_timestamp".
    // For json_api also: "request_body" (raw JSON string) or
    //   "request_headers" (object, will be merged) if the request shape is wrong.
  }},
  "reasoning": "<= 2 sentences on what was wrong and how the diff fixes it",
  "caveats": ["e.g. source sample was truncated and may miss items"]
}}
"""


def render_debug_recipe_prompt(
    *,
    strategy: str,
    url: str,
    recipe: dict,
    item_count: int,
    errors: list[str],
    warnings: list[str],
    sample_items: list[dict],
    source_sample: str,
) -> tuple[str, str]:
    """Render the debug-recipe prompt.

    *source_sample* is caller-truncated — HTML for xpath, JSON string for
    json_api/embedded_json. We clamp to 12_000 chars here as a safety net so an
    accidentally-enormous sample doesn't blow the context window.
    """
    trimmed_items = _truncate_values(sample_items[:3], max_str=200) if sample_items else []
    return DEBUG_RECIPE_SYSTEM, DEBUG_RECIPE_USER_TEMPLATE.format(
        strategy=strategy,
        url=url,
        recipe=json.dumps(recipe, ensure_ascii=False, indent=2),
        item_count=item_count,
        errors=json.dumps(errors or [], ensure_ascii=False),
        warnings=json.dumps(warnings or [], ensure_ascii=False),
        sample_items=json.dumps(trimmed_items, ensure_ascii=False),
        source_sample=(source_sample or "(not captured)")[:12_000],
    )


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
