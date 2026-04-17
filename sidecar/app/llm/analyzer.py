"""LLM-backed strategy analyzer for AutoFeed Phase 3."""
from __future__ import annotations

from app.llm import LLMAuth, LLMError, LLMMalformed, LLMTimeout
from app.llm.client import LLMClient
from app.llm.prompts import render_strategy_prompt
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
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
