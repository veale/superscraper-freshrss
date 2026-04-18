"""Unit tests for LLMClient — all requests mocked via respx."""
from __future__ import annotations

import json
import sys
import os

import httpx
import pytest
import respx


from app.llm import LLMAuth, LLMMalformed, LLMTimeout
from app.llm.client import LLMClient

_ENDPOINT = "http://localhost:11434"
_COMPLETIONS_URL = f"{_ENDPOINT}/chat/completions"


def _llm_response(content: str, tokens: int = 42) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": tokens},
    }


@pytest.fixture
def client():
    return LLMClient(endpoint=_ENDPOINT, api_key="sk-test", model="llama3", timeout=10)


@pytest.mark.asyncio
async def test_chat_json_happy_path(client, respx_mock):
    payload = json.dumps({"strategy": "rss", "confidence": 0.95})
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response(payload, tokens=100))
    )

    result = await client.chat_json("sys", "user")
    assert result["strategy"] == "rss"
    assert result["confidence"] == 0.95


@pytest.mark.asyncio
async def test_chat_completion_captures_tokens(client, respx_mock):
    payload = json.dumps({"strategy": "xpath", "confidence": 0.7})
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response(payload, tokens=77))
    )

    result = await client.chat_completion("sys", "user")
    assert result.tokens_used == 77
    assert result.content["strategy"] == "xpath"


@pytest.mark.asyncio
async def test_regex_fallback_parses_prose_wrapped_json(client, respx_mock):
    prose = 'Sure! Here is the result: {"strategy": "embedded_json", "confidence": 0.8} Hope that helps!'
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response(prose))
    )

    result = await client.chat_json("sys", "user")
    assert result["strategy"] == "embedded_json"


@pytest.mark.asyncio
async def test_timeout_raises_llm_timeout(respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

    c = LLMClient(endpoint=_ENDPOINT, model="llama3", timeout=5)
    with pytest.raises(LLMTimeout):
        await c.chat_json("sys", "user")


@pytest.mark.asyncio
async def test_401_raises_llm_auth(client, respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )

    with pytest.raises(LLMAuth):
        await client.chat_json("sys", "user")


@pytest.mark.asyncio
async def test_403_raises_llm_auth(client, respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(403, text="Forbidden")
    )

    with pytest.raises(LLMAuth):
        await client.chat_json("sys", "user")


@pytest.mark.asyncio
async def test_malformed_json_raises_llm_malformed(client, respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response("this is not json at all"))
    )

    with pytest.raises(LLMMalformed):
        await client.chat_json("sys", "user")


@pytest.mark.asyncio
async def test_missing_choices_raises_llm_malformed(client, respx_mock):
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json={"usage": {"total_tokens": 5}})
    )

    with pytest.raises(LLMMalformed):
        await client.chat_json("sys", "user")


@pytest.mark.asyncio
async def test_bearer_token_sent(client, respx_mock):
    payload = json.dumps({"strategy": "rss", "confidence": 0.9})
    route = respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response(payload))
    )

    await client.chat_json("sys", "user")
    assert route.called
    sent_headers = route.calls[0].request.headers
    assert sent_headers["authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_no_api_key_omits_auth_header(respx_mock):
    payload = json.dumps({"strategy": "rss", "confidence": 0.9})
    route = respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=_llm_response(payload))
    )

    c = LLMClient(endpoint=_ENDPOINT, api_key="", model="llama3")
    await c.chat_json("sys", "user")
    sent_headers = route.calls[0].request.headers
    assert "authorization" not in sent_headers


@pytest.mark.asyncio
async def test_missing_usage_tokens_are_none(client, respx_mock):
    payload = json.dumps({"strategy": "rss", "confidence": 0.9})
    body = {"choices": [{"message": {"content": payload}}]}  # no usage key
    respx_mock.post(_COMPLETIONS_URL).mock(
        return_value=httpx.Response(200, json=body)
    )

    result = await client.chat_completion("sys", "user")
    assert result.tokens_used is None
