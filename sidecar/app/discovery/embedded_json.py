"""Step 2 — Detect feed-like JSON embedded in <script> tags."""

from __future__ import annotations

import json
import re
from typing import Any

from app.discovery.scoring import find_best_array_path, score_feed_likeness
from app.models.schemas import EmbeddedJSON

# Patterns that identify well-known embedded-JSON conventions.
_SCRIPT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Next.js
    ("script#__NEXT_DATA__", re.compile(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
    )),
    # Nuxt 2
    ("window.__NUXT__", re.compile(
        r'window\.__NUXT__\s*=\s*({.*});?\s*(?:</script>|$)', re.DOTALL
    )),
    # Generic initial-state patterns
    ("window.__INITIAL_STATE__", re.compile(
        r'window\.__INITIAL_STATE__\s*=\s*({.*});?\s*(?:</script>|$)', re.DOTALL
    )),
    ("window.__PRELOADED_STATE__", re.compile(
        r'window\.__PRELOADED_STATE__\s*=\s*({.*});?\s*(?:</script>|$)', re.DOTALL
    )),
    ("window.__data__", re.compile(
        r'window\.__data__\s*=\s*({.*});?\s*(?:</script>|$)', re.DOTALL
    )),
]

# Catch-all: any <script type="application/json"> that looks like data.
_JSON_SCRIPT_RE = re.compile(
    r'<script\s+type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', re.DOTALL
)

# Also match large inline JSON objects assigned to variables.
_INLINE_JSON_RE = re.compile(
    r'(?:var|let|const)\s+\w+\s*=\s*(\{.{500,}?\});', re.DOTALL
)


def detect_embedded_json(html: str) -> list[EmbeddedJSON]:
    """Scan HTML for embedded JSON blobs and return feed-like ones."""

    results: list[EmbeddedJSON] = []

    # ── Named patterns ────────────────────────────────────────────────────
    for label, pattern in _SCRIPT_PATTERNS:
        for m in pattern.finditer(html):
            _try_parse(m.group(1), label, results)

    # ── Generic application/json and application/ld+json scripts ─────────
    for m in _JSON_SCRIPT_RE.finditer(html):
        _try_parse(m.group(1), "script[type=application/json]", results)

    # ── Large inline JSON assignments ────────────────────────────────────
    for m in _INLINE_JSON_RE.finditer(html):
        _try_parse(m.group(1), "inline_json_assignment", results)

    # Sort by score descending.
    results.sort(key=lambda e: e.feed_score, reverse=True)
    return results


def _try_parse(raw: str, label: str, acc: list[EmbeddedJSON]) -> None:
    """Attempt to JSON-parse *raw*, walk the structure, and append any
    feed-like arrays to *acc*."""
    raw = raw.strip()
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Sometimes the blob is almost-JSON (trailing commas, etc.).
        # A second pass with some fixup can help.
        try:
            cleaned = re.sub(r',\s*([}\]])', r'\1', raw)
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return

    # Walk the parsed structure looking for arrays of objects.
    candidates = find_best_array_path(data)
    for path, items, sc in candidates:
        if sc < 0.15:
            continue
        sample_keys = sorted({k for item in items[:5] for k in item.keys()})[:15]
        acc.append(EmbeddedJSON(
            source=label,
            path=path,
            item_count=len(items),
            sample_keys=sample_keys,
            feed_score=sc,
        ))
