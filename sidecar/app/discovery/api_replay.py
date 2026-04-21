"""Helpers for safely replaying captured API calls.

Two public functions:

* ``filter_replay_headers`` — drop session/identity headers (cookies, auth,
  UA) before persisting or replaying. Re-derives Origin/Referer from the
  target URL so they stay coherent when we call the endpoint later.
* ``detect_pagination`` — inspect a captured request body, URL query, and
  response body to guess a :class:`PaginationSpec`. Conservative: returns
  ``None`` when nothing obvious is present.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.models.schemas import PaginationSpec

_REPLAY_WHITELIST = {
    "content-type",
    "accept",
    "accept-language",
}

_PAGE_KEYS = ("pagenumber", "page", "pageindex", "p")
_OFFSET_KEYS = ("offset", "from", "skip", "start")
_CURSOR_KEYS = ("cursor", "after", "nextcursor", "pagecursor", "pagetoken")
_PER_PAGE_KEYS = ("perpage", "per_page", "pagesize", "page_size", "limit", "size")

_HAS_MORE_KEYS = ("hasmore", "has_more", "hasnext", "has_next", "more")
_NEXT_CURSOR_KEYS = ("nextcursor", "next_cursor", "endcursor", "end_cursor", "next")
_TOTAL_PAGES_KEYS = ("totalpages", "total_pages", "pagecount", "page_count", "pages")


def filter_replay_headers(captured: dict[str, str], target_url: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (captured or {}).items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        if k.lower() in _REPLAY_WHITELIST:
            out[k] = v
    parsed = urlparse(target_url)
    if parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        out.setdefault("Origin", origin)
        out.setdefault("Referer", origin + "/")
    return out


def _norm(k: str) -> str:
    return k.lower().replace("-", "").replace("_", "")


def _find_path(obj: Any, wanted: tuple[str, ...], prefix: str = "", depth: int = 0) -> str:
    if depth > 4 or obj is None:
        return ""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_norm = _norm(str(k))
            path = f"{prefix}.{k}" if prefix else str(k)
            if key_norm in wanted and not isinstance(v, (dict, list)):
                return path
            found = _find_path(v, wanted, path, depth + 1)
            if found:
                return found
    return ""


def _classify_body_key(key_norm: str) -> tuple[str, str] | None:
    if key_norm in _PAGE_KEYS:
        return "page", "page"
    if key_norm in _OFFSET_KEYS:
        return "offset", "offset"
    if key_norm in _CURSOR_KEYS:
        return "cursor", "cursor"
    return None


def detect_pagination(
    request_body: str,
    endpoint_url: str,
    response_body: Any,
) -> PaginationSpec | None:
    # Look in the request body first — it's where most JSON APIs put the knob.
    parsed_body: Any = None
    if request_body:
        try:
            parsed_body = json.loads(request_body)
        except (ValueError, TypeError):
            parsed_body = None

    if isinstance(parsed_body, dict):
        for k, v in parsed_body.items():
            cls = _classify_body_key(_norm(str(k)))
            if cls is None:
                continue
            kind, _ = cls
            start = v if isinstance(v, int) else (1 if kind == "page" else 0)
            per_page = 0
            per_page_param = ""
            for pk, pv in parsed_body.items():
                if _norm(str(pk)) in _PER_PAGE_KEYS and isinstance(pv, int):
                    per_page = pv
                    per_page_param = str(pk)
                    break
            return PaginationSpec(
                location="body",
                param=str(k),
                kind=kind,
                start=int(start),
                per_page=per_page,
                per_page_param=per_page_param,
                has_more_path=_find_path(response_body, _HAS_MORE_KEYS),
                next_cursor_path=_find_path(response_body, _NEXT_CURSOR_KEYS) if kind == "cursor" else "",
                total_pages_path=_find_path(response_body, _TOTAL_PAGES_KEYS),
            )

    parsed = urlparse(endpoint_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for k, vals in qs.items():
        cls = _classify_body_key(_norm(k))
        if cls is None:
            continue
        kind, _ = cls
        v = vals[0] if vals else ""
        try:
            start_int = int(v)
        except (ValueError, TypeError):
            start_int = 1 if kind == "page" else 0
        per_page = 0
        per_page_param = ""
        for pk, pvals in qs.items():
            if _norm(pk) in _PER_PAGE_KEYS and pvals:
                try:
                    per_page = int(pvals[0])
                    per_page_param = pk
                    break
                except ValueError:
                    pass
        return PaginationSpec(
            location="query",
            param=k,
            kind=kind,
            start=start_int,
            per_page=per_page,
            per_page_param=per_page_param,
            has_more_path=_find_path(response_body, _HAS_MORE_KEYS),
            next_cursor_path=_find_path(response_body, _NEXT_CURSOR_KEYS) if kind == "cursor" else "",
            total_pages_path=_find_path(response_body, _TOTAL_PAGES_KEYS),
        )
    return None
