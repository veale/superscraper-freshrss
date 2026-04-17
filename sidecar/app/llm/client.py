"""Minimal async LLM client — OpenAI-compatible chat/completions endpoint."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from app.llm import LLMAuth, LLMMalformed, LLMTimeout, LLMError


@dataclass
class CompletionResult:
    content: dict
    tokens_used: Optional[int] = None


class LLMClient:
    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        timeout: int = 60,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def chat_completion(self, system: str, user: str) -> CompletionResult:
        """Call the LLM and return parsed JSON content + token count."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(
                    f"{self.endpoint}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"Request timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"HTTP error contacting LLM: {exc}") from exc

        if resp.status_code in (401, 403):
            raise LLMAuth(f"LLM returned {resp.status_code} Unauthorized")
        if resp.status_code != 200:
            raise LLMError(
                f"LLM returned {resp.status_code}: {resp.text[:300]}"
            )

        try:
            data = resp.json()
            content_str: str = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMMalformed(
                f"Unexpected LLM response structure: {exc}"
            ) from exc

        tokens: Optional[int] = None
        try:
            tokens = int(data["usage"]["total_tokens"])
        except (KeyError, TypeError, ValueError):
            pass

        return CompletionResult(
            content=_parse_json(content_str),
            tokens_used=tokens,
        )

    async def chat_json(self, system: str, user: str) -> dict:
        """Convenience wrapper — returns just the parsed JSON dict."""
        result = await self.chat_completion(system, user)
        return result.content


def _parse_json(text: str) -> dict:
    """Parse JSON from LLM output, with a regex fallback for prose-wrapped responses."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: extract first {...} block (some providers add prose around the JSON)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise LLMMalformed(f"Could not parse JSON from LLM response: {text[:300]}")
