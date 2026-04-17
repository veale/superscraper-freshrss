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
    FeedStrategy,
    LLMRecommendation,
)


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

    warnings = _sanity_check_php(bridge_name, php_code)

    return BridgeGenerateResponse(
        bridge_name=bridge_name,
        filename=f"{bridge_name}.php",
        php_code=php_code,
        sanity_warnings=warnings,
    )


def _sanity_check_php(bridge_name: str, code: str) -> list[str]:
    warnings: list[str] = []

    if not code.lstrip().startswith("<?php"):
        warnings.append("PHP code does not start with <?php")

    if "?>" in code:
        warnings.append("PHP closing tag ?> found — omit it per RSS-Bridge convention")

    if "extends BridgeAbstract" not in code:
        warnings.append("Class does not extend BridgeAbstract")

    if f"class {bridge_name}Bridge" not in code:
        warnings.append(f"Expected class '{bridge_name}Bridge' not found in code")

    if "collectData" not in code:
        warnings.append("Missing collectData() method")

    for danger in ("shell_exec", "exec(", "system(", "passthru(", "eval("):
        if danger in code:
            warnings.append(f"Potentially dangerous call: {danger}")

    return warnings
