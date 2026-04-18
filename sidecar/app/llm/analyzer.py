"""LLM-backed strategy analyzer and bridge generator for AutoFeed Phase 3."""
from __future__ import annotations

from app.llm import LLMAuth, LLMError, LLMMalformed, LLMTimeout
from app.llm.client import LLMClient
from app.llm.prompts import render_bridge_prompt, render_strategy_prompt
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    BridgeGenerateRequest,
    BridgeGenerateResponse,
    DiscoveryResults,
    FeedStrategy,
    LLMRecommendation,
)


def should_invoke_llm(results: DiscoveryResults) -> tuple[bool, str]:
    """Return (needs_llm, auto_strategy) — skip the LLM when unambiguous."""
    if (
        not results.force_skip_rss
        and results.rss_feeds
        and any(f.is_alive for f in results.rss_feeds)
    ):
        return False, "rss"
    if (
        len(results.api_endpoints) == 1
        and results.api_endpoints[0].feed_score > 0.7
    ):
        return False, "json_api"
    return True, ""


async def recommend_strategy(req: AnalyzeRequest) -> AnalyzeResponse:
    """Call the LLM to pick the best feed strategy, return structured response."""
    client = LLMClient(
        endpoint=req.llm.endpoint,
        api_key=req.llm.api_key,
        model=req.llm.model,
        timeout=req.llm.timeout,
    )
    system, user = render_strategy_prompt(req)

    try:
        result = await client.chat_completion(system, user)
    except LLMTimeout as exc:
        return AnalyzeResponse(url=req.url, errors=[f"LLM timeout: {exc}"])
    except LLMAuth as exc:
        return AnalyzeResponse(url=req.url, errors=[f"LLM auth error: {exc}"])
    except LLMMalformed as exc:
        return AnalyzeResponse(url=req.url, errors=[f"LLM malformed response: {exc}"])
    except LLMError as exc:
        return AnalyzeResponse(url=req.url, errors=[f"LLM error: {exc}"])

    raw = result.content

    strategy_str = raw.get("strategy", "")
    try:
        strategy = FeedStrategy(strategy_str)
    except ValueError:
        return AnalyzeResponse(
            url=req.url,
            llm_raw=raw,
            errors=[f"Unknown strategy in LLM response: {strategy_str!r}"],
        )

    try:
        confidence = float(raw.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    field_overrides = raw.get("field_overrides") or {}
    if not isinstance(field_overrides, dict):
        field_overrides = {}

    caveats = raw.get("caveats") or []
    if not isinstance(caveats, list):
        caveats = []

    recommendation = LLMRecommendation(
        strategy=strategy,
        confidence=confidence,
        reasoning=str(raw.get("reasoning", "")),
        selected_candidate_ref=raw.get("selected_candidate_ref") or None,
        field_overrides={str(k): str(v) for k, v in field_overrides.items()},
        caveats=[str(c) for c in caveats],
    )

    return AnalyzeResponse(
        url=req.url,
        recommendation=recommendation,
        llm_raw=raw,
        tokens_used=result.tokens_used,
    )


async def recommend_candidate_selectors(
    url: str,
    candidate,
    html_skeleton: str,
    llm,
    refine_examples: dict[str, list[str]] | None = None,
    raw_html: str = "",
) -> dict:
    """Ask the LLM to improve one XPath candidate's selectors, including item_selector.

    Returns a dict with selector keys plus 'reasoning'. Any null value means keep current.
    """
    client = LLMClient(
        endpoint=llm.endpoint,
        api_key=llm.api_key,
        model=llm.model,
        timeout=llm.timeout,
    )

    system = (
        "You are an HTML feed-selector expert. Your job is to produce XPath "
        "selectors that extract a list of news/article items from a single "
        "rendered web page.\n"
        "\n"
        "You receive:\n"
        "  - the current item selector and field selectors (may be wrong),\n"
        "  - an HTML skeleton of the page,\n"
        "  - OPTIONAL user-supplied example values (title text, link URL, etc.)\n"
        "    that identify one real item on the page.\n"
        "\n"
        "Return a JSON object with these keys, any of which may be null to keep "
        "the existing value:\n"
        "  - item_selector (XPath expression starting with // that selects item containers)\n"
        "  - title_selector, link_selector, content_selector, timestamp_selector,\n"
        "    author_selector, thumbnail_selector (all RELATIVE XPath starting with .//)\n"
        "  - reasoning (one sentence explaining the change)\n"
        "\n"
        "Critical guidance:\n"
        "  - You MAY change item_selector. Do so when the current selector yields "
        "zero elements, selects obvious non-items (navigation, ads), or when "
        "example values suggest a different container is correct.\n"
        "  - XPath unions across different tag names are allowed and encouraged "
        "when a page has two parallel item families (e.g. "
        "'//li[contains(@class,\"grid-item\")] | //article[contains(@class,\"media-block\")]').\n"
        "  - Component-framework class names like 'media-block', 'media-list__item', "
        "'card', 'tile', 'teaser', 'grid-item', 'news-item' are STRONG positive "
        "signals for item containers. Do not dismiss them as boilerplate.\n"
        "  - If user examples are provided, your selectors MUST match them. Verify "
        "mentally: 'does my title_selector produce the example title when "
        "applied inside my item_selector?'\n"
        "  - Prefer semantic tags (article, li, section) and content-bearing class "
        "names over div-with-utility-classes.\n"
        "Return JSON only, no prose."
    )

    anchored_snippet = ""
    if refine_examples and raw_html:
        from app.utils.skeleton import build_anchored_snippet
        for role in ("title", "link", "content"):
            vals = refine_examples.get(role) or []
            if vals:
                anchored_snippet = build_anchored_snippet(raw_html, vals[0])
                if anchored_snippet:
                    break

    parts = [
        f"Page URL: {url}",
        "",
        "Current selectors:",
        f"  item: {candidate.item_selector}",
        f"  title: {candidate.title_selector}",
        f"  link: {candidate.link_selector}",
        f"  content: {candidate.content_selector}",
        f"  timestamp: {candidate.timestamp_selector}",
        f"  author: {candidate.author_selector}",
        f"  thumbnail: {candidate.thumbnail_selector}",
        "",
    ]

    if refine_examples:
        parts.append("USER EXAMPLES (one real item on this page):")
        for role, vals in refine_examples.items():
            if vals:
                parts.append(f"  {role}: {vals[0][:200]}")
        parts.append("Your selectors MUST reproduce these when applied to the page.")
        parts.append("")

    if anchored_snippet:
        parts.append("HTML snippet around the user's example (text PRESERVED — use this to verify your selectors):")
        parts.append(anchored_snippet)
        parts.append("")
        parts.append("Additional context — structural skeleton of the wider page:")
        parts.append(html_skeleton[:4000] if html_skeleton else "(not available)")
    else:
        parts.append("HTML skeleton (first 8000 chars, text collapsed to [text:N] placeholders):")
        parts.append(html_skeleton[:8000] if html_skeleton else "(not available)")

    parts.append("")
    parts.append("Propose improved selectors. Return JSON only.")
    user = "\n".join(parts)

    try:
        result = await client.chat_completion(system, user)
    except (LLMTimeout, LLMAuth, LLMMalformed, LLMError) as exc:
        raise RuntimeError(f"LLM error: {exc}") from exc

    raw = result.content
    _fields = (
        "item_selector",
        "title_selector", "link_selector", "content_selector",
        "timestamp_selector", "author_selector", "thumbnail_selector",
    )
    selectors = {k: raw.get(k) or None for k in _fields}
    selectors["reasoning"] = str(raw.get("reasoning", "") or "")
    return selectors


async def refine_with_item_samples(
    url: str,
    candidate,
    item_outer_htmls: list[str],
    examples: dict[str, str],
    llm,
) -> dict:
    """Ask the LLM to polish field selectors given actual item outerHTML samples.

    Precondition: item_selector has already been established (by LCA or heuristic).
    The LLM's job is narrow: confirm or correct each field's relative XPath, propose
    selectors for roles the user didn't supply examples for, and flag selectors that
    won't generalise.
    """
    client = LLMClient(
        endpoint=llm.endpoint, api_key=llm.api_key,
        model=llm.model, timeout=llm.timeout,
    )

    system = (
        "You are an HTML feed-selector expert refining a set of XPath "
        "selectors against KNOWN item samples. The item container has "
        "already been identified; your job is to produce RELATIVE XPath "
        "expressions (starting with .//) for each field so that, applied "
        "inside each item sample, they return the correct value.\n"
        "\n"
        "Return JSON with these keys, any of which may be null to keep "
        "the current value:\n"
        "  title_selector, link_selector, content_selector,\n"
        "  timestamp_selector, author_selector, thumbnail_selector,\n"
        "  reasoning (one sentence).\n"
        "\n"
        "Rules:\n"
        "  - DO NOT change the item container. Only field selectors.\n"
        "  - A field selector is correct only if it produces the same "
        "kind of value across ALL samples.\n"
        "  - Prefer class-based XPath (contains(@class,'x')) over "
        "position-based XPath ([1], [last()]).\n"
        "  - For link, return an XPath ending in /@href.\n"
        "  - For thumbnail, return an XPath ending in /@src (or /@data-src).\n"
        "  - If a field doesn't reliably exist in the samples, return null.\n"
        "\n"
        "Return JSON only. No prose."
    )

    parts = [f"Page URL: {url}", ""]
    parts.append(f"Item container selector (fixed): {candidate.item_selector}")
    parts.append("")
    parts.append("Current field selectors (you may change these):")
    parts.append(f"  title:     {candidate.title_selector or '(unset)'}")
    parts.append(f"  link:      {candidate.link_selector or '(unset)'}")
    parts.append(f"  content:   {candidate.content_selector or '(unset)'}")
    parts.append(f"  timestamp: {candidate.timestamp_selector or '(unset)'}")
    parts.append(f"  author:    {candidate.author_selector or '(unset)'}")
    parts.append(f"  thumbnail: {candidate.thumbnail_selector or '(unset)'}")
    parts.append("")

    if examples:
        parts.append("User examples (one real item on this page):")
        for role, val in examples.items():
            if val:
                parts.append(f"  {role}: {val[:200]}")
        parts.append("Your selectors MUST reproduce these for that item.")
        parts.append("")

    parts.append(f"Item samples ({len(item_outer_htmls)} items from the page, outerHTML):")
    for i, html_frag in enumerate(item_outer_htmls[:3], 1):
        parts.append(f"--- Item {i} ---")
        parts.append(html_frag[:4000])
    parts.append("")
    parts.append("Return JSON only.")
    user = "\n".join(parts)

    try:
        result = await client.chat_completion(system, user)
    except (LLMTimeout, LLMAuth, LLMMalformed, LLMError) as exc:
        raise RuntimeError(f"LLM error: {exc}") from exc

    raw = result.content
    fields = (
        "title_selector", "link_selector", "content_selector",
        "timestamp_selector", "author_selector", "thumbnail_selector",
    )
    selectors = {k: raw.get(k) or None for k in fields}
    selectors["reasoning"] = str(raw.get("reasoning", "") or "")
    return selectors


async def generate_bridge(req: BridgeGenerateRequest) -> BridgeGenerateResponse:
    """Call the LLM to generate an RSS-Bridge PHP script."""
    client = LLMClient(
        endpoint=req.llm.endpoint,
        api_key=req.llm.api_key,
        model=req.llm.model,
        timeout=req.llm.timeout,
    )
    system, user = render_bridge_prompt(req)

    try:
        result = await client.chat_completion(system, user)
    except LLMTimeout as exc:
        return BridgeGenerateResponse(errors=[f"LLM timeout: {exc}"])
    except LLMAuth as exc:
        return BridgeGenerateResponse(errors=[f"LLM auth error: {exc}"])
    except LLMMalformed as exc:
        return BridgeGenerateResponse(errors=[f"LLM malformed response: {exc}"])
    except LLMError as exc:
        return BridgeGenerateResponse(errors=[f"LLM error: {exc}"])

    raw = result.content
    bridge_name = str(raw.get("bridge_name", "")).strip()
    php_code = str(raw.get("php_code", "")).strip()

    if not bridge_name or not php_code:
        return BridgeGenerateResponse(
            errors=["LLM did not return both bridge_name and php_code fields"],
        )

    warnings, soft_warnings = _sanity_check_php(bridge_name, php_code)

    return BridgeGenerateResponse(
        bridge_name=bridge_name,
        filename=f"{bridge_name}.php",
        php_code=php_code,
        sanity_warnings=warnings,
        soft_warnings=soft_warnings,
    )


def _sanity_check_php(bridge_name: str, code: str) -> tuple[list[str], list[str]]:
    """Check PHP code for common issues. Returns (warnings, soft_warnings)."""
    warnings: list[str] = []
    soft_warnings: list[str] = []

    if not code.lstrip().startswith("<?php"):
        warnings.append("PHP code does not start with <?php")

    if "?>" in code:
        warnings.append("PHP closing tag ?> found — omit it per RSS-Bridge convention")

    if "extends BridgeAbstract" not in code:
        warnings.append("Class does not extend BridgeAbstract")

    if f"class {bridge_name}" not in code:
        warnings.append(f"Expected class '{bridge_name}' not found in code")

    if "collectData" not in code:
        warnings.append("Missing collectData() method")

    for const_name in ("const NAME", "const URI", "const DESCRIPTION"):
        if const_name not in code:
            warnings.append(f"Missing {const_name} constant")

    if "const MAINTAINER = 'AutoFeed-LLM'" not in code:
        warnings.append("const MAINTAINER should equal 'AutoFeed-LLM'")

    if "const PARAMETERS" not in code:
        warnings.append("const PARAMETERS is required even if empty")

    # Actually dangerous calls that should block/warn strongly
    dangerous_patterns = (
        "shell_exec",
        "system(",
        "passthru(",
        "popen(",
        "proc_open(",
        "eval(",
        "assert(",
        "create_function(",
        "pcntl_exec(",
    )
    for danger in dangerous_patterns:
        if danger in code:
            warnings.append(f"Dangerous call: {danger}")

    # Soft warnings - these are normal in RSS-Bridge but should be reviewed
    soft_patterns = ("file_get_contents", "fopen(", "curl_", "base64_decode")
    for pattern in soft_patterns:
        if pattern in code:
            # For file_get_contents, only warn if it looks like a local file read
            if pattern == "file_get_contents":
                # Check if there's a path starting with / or php://
                import re as _re
                if _re.search(r'file_get_contents\s*\(\s*["\'][\/php://]', code):
                    soft_warnings.append(f"file_get_contents with local path - review if expected")
                else:
                    soft_warnings.append(f"file_get_contents present - review if expected")
            else:
                soft_warnings.append(f"{pattern} present - review if you didn't expect it")

    return warnings, soft_warnings
