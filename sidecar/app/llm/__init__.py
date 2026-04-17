"""LLM client package for AutoFeed Phase 3."""
from __future__ import annotations


class LLMError(Exception):
    """Base class for all LLM-related errors."""


class LLMTimeout(LLMError):
    """LLM request timed out."""


class LLMAuth(LLMError):
    """LLM returned an authentication error (401/403)."""


class LLMMalformed(LLMError):
    """LLM response could not be parsed as JSON."""


__all__ = ["LLMError", "LLMTimeout", "LLMAuth", "LLMMalformed"]
