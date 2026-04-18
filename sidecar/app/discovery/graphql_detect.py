"""GraphQL detection — inline capture analysis and best-effort introspection."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.discovery.scoring import find_best_array_path, score_feed_likeness
from app.models.schemas import GraphQLOperation
from app.services.config import ServiceConfig

_INTROSPECTION_QUERY = (
    "{ __schema { queryType { name fields { name type { name kind ofType { name kind } } } } } }"
)


def _is_graphql_request(resp: dict[str, Any]) -> bool:
    url = resp.get("url", "")
    ct = resp.get("content_type", "").lower()
    body = resp.get("request_post_data") or ""

    if re.search(r"/graphql/?(?:\?|$)", url, re.IGNORECASE):
        return True
    if "graphql" in ct:
        return True
    if body.startswith("{"):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict) and isinstance(parsed.get("query"), str):
                q = parsed["query"]
                if "{" in q and ("query" in q or "mutation" in q or q.startswith("{")):
                    return True
        except json.JSONDecodeError:
            pass
    return False


def _extract_op_name(query: str) -> str:
    m = re.search(r"(?:query|mutation|subscription)\s+(\w+)", query)
    return m.group(1) if m else ""


def _extract_op_type(query: str) -> str:
    m = re.match(r"\s*(query|mutation|subscription)\b", query)
    if m:
        return m.group(1)
    return "query"


async def detect_graphql_in_capture(
    captured: list[dict[str, Any]],
) -> list[GraphQLOperation]:
    """Inspect a network capture and return feed-like GraphQL operations."""
    ops: list[GraphQLOperation] = []
    seen: set[tuple[str, str]] = set()

    for resp in captured:
        if not _is_graphql_request(resp):
            continue

        body = resp.get("request_post_data") or ""
        try:
            parsed_body = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed_body = {}

        query = parsed_body.get("query", "")
        op_name = parsed_body.get("operationName", "") or _extract_op_name(query)
        op_type = _extract_op_type(query)

        key = (resp.get("url", ""), op_name)
        if key in seen:
            continue
        seen.add(key)

        # Use find_best_array_path first — GraphQL responses are often nested under
        # {"data": {"posts": [...]}} where the top-level score would be 0.
        candidates = find_best_array_path(resp.get("body"))
        if candidates:
            best_path, items, best_score = candidates[0]
        else:
            best_score = score_feed_likeness(resp.get("body"))
            best_path, items = "", []
        if best_score < 0.15:
            continue
        sample_keys = sorted({k for it in items[:5] for k in it.keys()})[:15]

        ops.append(GraphQLOperation(
            endpoint=resp.get("url", ""),
            operation_name=op_name,
            operation_type=op_type,
            query=query,
            variables=parsed_body.get("variables") or {},
            response_path=best_path,
            item_count=len(items),
            sample_keys=sample_keys,
            feed_score=best_score,
            detected_via="network_capture",
        ))

    ops.sort(key=lambda o: o.feed_score, reverse=True)
    return ops[:5]


async def probe_graphql_endpoint(
    endpoint: str,
    services: ServiceConfig,
    *,
    timeout: int = 15,
    introspect: bool = True,
) -> list[GraphQLOperation]:
    """Best-effort introspection probe against *endpoint*.

    Returns [] without raising if introspection is blocked or the server is unreachable.
    """
    if not introspect:
        return []

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if services.auth_token:
        headers["Authorization"] = f"Bearer {services.auth_token}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                endpoint,
                json={"query": _INTROSPECTION_QUERY},
                headers=headers,
            )
        if r.status_code != 200:
            return []
        schema = r.json().get("data", {}).get("__schema")
        if not schema:
            return []
    except (httpx.HTTPError, json.JSONDecodeError):
        return []

    # Walk queryType fields, pick LIST-returning ones with feed-like field names.
    query_type = schema.get("queryType", {}) or {}
    fields = query_type.get("fields") or []
    _FEED_KEYS = {"title", "name", "url", "link", "date", "published", "content", "body"}
    candidates: list[tuple[float, str]] = []

    for field in fields[:20]:
        ft = field.get("type", {}) or {}
        # Unwrap NonNull wrapper if present.
        if ft.get("kind") == "NON_NULL":
            ft = ft.get("ofType") or {}
        if ft.get("kind") != "LIST":
            continue
        item_type = ft.get("ofType") or {}
        if item_type.get("kind") == "NON_NULL":
            item_type = item_type.get("ofType") or {}
        type_name = item_type.get("name") or ""
        # Heuristic: type name suggests feed items.
        name_lower = (field.get("name") or "").lower()
        type_lower = type_name.lower()
        score = sum(1 for k in _FEED_KEYS if k in type_lower or k in name_lower)
        if score > 0:
            candidates.append((score, field.get("name", "")))

    candidates.sort(reverse=True)
    ops: list[GraphQLOperation] = []

    for _, field_name in candidates[:3]:
        probe_query = f"{{ {field_name} {{ title url link date published content body }} }}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    endpoint,
                    json={"query": probe_query},
                    headers=headers,
                )
            if r.status_code != 200:
                continue
            data = r.json().get("data", {})
            items_raw = data.get(field_name)
            sc = score_feed_likeness(items_raw)
            if sc < 0.15:
                continue
            path_candidates = find_best_array_path(items_raw)
            best_path = path_candidates[0][0] if path_candidates else ""
            items = path_candidates[0][1] if path_candidates else (items_raw if isinstance(items_raw, list) else [])
            sample_keys = sorted({k for it in items[:5] for k in (it.keys() if isinstance(it, dict) else [])})[:15]
            ops.append(GraphQLOperation(
                endpoint=endpoint,
                operation_name=field_name,
                operation_type="query",
                query=probe_query,
                variables={},
                response_path=best_path,
                item_count=len(items),
                sample_keys=sample_keys,
                feed_score=sc,
                detected_via="introspection",
            ))
        except (httpx.HTTPError, json.JSONDecodeError):
            continue

    return ops
