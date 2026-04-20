"""In-memory trace store for "Under the hood" transparency panels.

Records the provenance of every discovery, preview, refine, and LLM action
so the UI can show users exactly what HTML, prompts, and responses flowed
through the pipeline.

Not persisted to disk; capped per-entry by `_MAX_TRACES_PER_DISCOVERY`.
TTL-swept alongside the main discovery cache.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from typing import Any

_TRACES: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

_TTL_SECONDS = 2 * 60 * 60
_MAX_TRACES_PER_DISCOVERY = 60
_MAX_HTML_BYTES = 2 * 1024 * 1024  # truncate very large HTML to keep memory sane


def _now() -> float:
    return time.time()


def _sweep_locked() -> None:
    cutoff = _now() - _TTL_SECONDS
    dead = [k for k, v in _TRACES.items() if v.get("_touched", 0) < cutoff]
    for k in dead:
        _TRACES.pop(k, None)


def _touch_locked(discover_id: str) -> None:
    bundle = _TRACES.get(discover_id)
    if bundle is not None:
        bundle["_touched"] = _now()


def init_discovery_trace(discover_id: str, url: str) -> None:
    with _LOCK:
        _sweep_locked()
        _TRACES[discover_id] = {
            "discover_id": discover_id,
            "url": url,
            "created": _now(),
            "_touched": _now(),
            "discovery": {},
            "artifacts": {},
            "actions": [],
        }


def set_discovery(discover_id: str, section: str, data: Any) -> None:
    with _LOCK:
        bundle = _TRACES.get(discover_id)
        if bundle is None:
            return
        bundle["discovery"][section] = _clip(data)
        _touch_locked(discover_id)


def merge_discovery(discover_id: str, section: str, data: dict) -> None:
    with _LOCK:
        bundle = _TRACES.get(discover_id)
        if bundle is None:
            return
        bucket = bundle["discovery"].setdefault(section, {})
        if isinstance(bucket, dict):
            bucket.update(_clip(data))
        else:
            bundle["discovery"][section] = _clip(data)
        _touch_locked(discover_id)


def store_artifact(discover_id: str, kind: str, content: str) -> None:
    """Store a named HTML/text artifact (e.g. 'raw_html', 'browser_html',
    'pruned_html', 'html_skeleton'). Truncates very large values."""
    if content is None:
        return
    if not isinstance(content, str):
        content = str(content)
    truncated = False
    if len(content.encode("utf-8", errors="ignore")) > _MAX_HTML_BYTES:
        content = content[: _MAX_HTML_BYTES // 2]
        truncated = True
    with _LOCK:
        bundle = _TRACES.get(discover_id)
        if bundle is None:
            return
        bundle["artifacts"][kind] = {
            "size": len(content),
            "truncated": truncated,
            "content": content,
        }
        _touch_locked(discover_id)


def get_artifact(discover_id: str, kind: str) -> dict | None:
    with _LOCK:
        bundle = _TRACES.get(discover_id)
        if bundle is None:
            return None
        art = bundle["artifacts"].get(kind)
        if art is None:
            return None
        _touch_locked(discover_id)
        return dict(art)


def add_action(discover_id: str, action: dict) -> str:
    """Append an action record (refine/llm/preview) and return an action_id."""
    action_id = uuid.uuid4().hex[:10]
    record = {
        "action_id": action_id,
        "timestamp": _now(),
        **_clip(action),
    }
    with _LOCK:
        bundle = _TRACES.get(discover_id)
        if bundle is None:
            # Lazy-init so we never lose traces even if discovery wasn't recorded.
            bundle = {
                "discover_id": discover_id,
                "url": "",
                "created": _now(),
                "_touched": _now(),
                "discovery": {},
                "artifacts": {},
                "actions": [],
            }
            _TRACES[discover_id] = bundle
        actions = bundle["actions"]
        actions.append(record)
        # Cap memory growth — drop oldest when exceeding cap.
        if len(actions) > _MAX_TRACES_PER_DISCOVERY:
            del actions[0 : len(actions) - _MAX_TRACES_PER_DISCOVERY]
        _touch_locked(discover_id)
    return action_id


def get_bundle(discover_id: str) -> dict | None:
    with _LOCK:
        bundle = _TRACES.get(discover_id)
        if bundle is None:
            return None
        _touch_locked(discover_id)
        # Return a shallow copy without the heavy artifact contents — those
        # are served separately to keep the JSON response reasonable.
        artifacts_summary = {
            kind: {"size": a.get("size", 0), "truncated": a.get("truncated", False)}
            for kind, a in bundle["artifacts"].items()
        }
        return {
            "discover_id": bundle["discover_id"],
            "url": bundle.get("url", ""),
            "created": bundle.get("created"),
            "discovery": copy.deepcopy(bundle.get("discovery", {})),
            "artifacts": artifacts_summary,
            "actions": copy.deepcopy(bundle.get("actions", [])),
        }


def _clip(value: Any, _depth: int = 0) -> Any:
    """Deep-copy safe clipper: passthrough primitives; avoid storing non-JSON types."""
    if _depth > 8:
        return repr(value)[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # Cap individual string fields to keep memory bounded.
        if len(value) > _MAX_HTML_BYTES:
            return value[: _MAX_HTML_BYTES]
        return value
    if isinstance(value, dict):
        return {str(k): _clip(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v, _depth + 1) for v in value]
    return repr(value)[:500]
