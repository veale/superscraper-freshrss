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

    async def chat_completion(
        self,
        system: str,
        user: str,
        capture: dict | None = None,
    ) -> CompletionResult:
        """Call the LLM and return parsed JSON content + token count.

        When *capture* is provided it is populated in-place with prompts,
        raw response content, and tokens so callers can record the full
        LLM interaction for transparency / debugging panels.
        """
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

        if capture is not None:
            capture["endpoint"] = f"{self.endpoint}/chat/completions"
            capture["model"] = self.model
            capture["system"] = system
            capture["user"] = user

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                resp = await http.post(
                    f"{self.endpoint}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            if capture is not None:
                capture["error"] = f"timeout after {self.timeout}s"
            raise LLMTimeout(f"Request timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            if capture is not None:
                capture["error"] = str(exc)
            raise LLMError(f"HTTP error contacting LLM: {exc}") from exc

        if resp.status_code in (401, 403):
            if capture is not None:
                capture["error"] = f"{resp.status_code} unauthorized"
            raise LLMAuth(f"LLM returned {resp.status_code} Unauthorized")
        if resp.status_code != 200:
            if capture is not None:
                capture["error"] = f"{resp.status_code}: {resp.text[:300]}"
            raise LLMError(
                f"LLM returned {resp.status_code}: {resp.text[:300]}"
            )

        try:
            data = resp.json()
            content_str: str = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            if capture is not None:
                capture["error"] = f"malformed: {exc}"
            raise LLMMalformed(
                f"Unexpected LLM response structure: {exc}"
            ) from exc

        tokens: Optional[int] = None
        try:
            tokens = int(data["usage"]["total_tokens"])
        except (KeyError, TypeError, ValueError):
            pass

        if capture is not None:
            capture["raw_content"] = content_str
            capture["tokens_used"] = tokens

        return CompletionResult(
            content=_parse_json(content_str),
            tokens_used=tokens,
        )

    async def chat_json(self, system: str, user: str) -> dict:
        """Convenience wrapper — returns just the parsed JSON dict."""
        result = await self.chat_completion(system, user)
        return result.content


def _parse_json(text: str) -> dict:
    """Parse JSON from LLM output, with a brace-balance fallback for prose-wrapped responses."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fallback: use brace-balance walker to find the first balanced {...} block
    # This handles cases like {"thinking": "..."} {"bridge_name": "..."} correctly
    balanced = _find_balanced_braces(text)
    if balanced:
        try:
            return json.loads(balanced)
        except json.JSONDecodeError:
            pass

    raise LLMMalformed(f"Could not parse JSON from LLM response: {text[:300]}")


def _find_balanced_braces(text: str) -> str | None:
    """Find the first balanced JSON object in text using brace counting.
    
    Scans for the first '{', then counts braces (skipping those inside strings)
    until the object is balanced. Returns the balanced substring or None.
    """
    start = text.find('{')
    if start == -1:
        return None
    
    depth = 0
    in_string = False
    escape_next = False
    
    for i in range(start, len(text)):
        char = text[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\':
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if in_string:
            continue
        
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    
    return None
