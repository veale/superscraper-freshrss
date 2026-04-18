"""Filesystem-backed discovery result cache for AutoFeed."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
import re

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _cache_dir() -> Path:
    return Path(os.getenv("AUTOFEED_DISCOVERY_CACHE_DIR", "/app/data/discover-cache"))


def _cache_ttl() -> int:
    return int(os.getenv("AUTOFEED_DISCOVERY_CACHE_TTL", "900"))


def _ensure_cache_dir() -> None:
    _cache_dir().mkdir(parents=True, exist_ok=True)


def _sweep_cache() -> None:
    if not _cache_dir().exists():
        return
    now = time.time()
    for path in _cache_dir().glob("*.json"):
        try:
            if now - path.stat().st_mtime > _cache_ttl():
                path.unlink()
        except OSError:
            continue


def store_discovery(payload: dict) -> str:
    """Persist *payload* and return a new discover_id."""
    _ensure_cache_dir()
    _sweep_cache()
    discover_id = secrets.token_urlsafe(12)
    path = _cache_dir() / f"{discover_id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    try:
        os.replace(tmp, path)
    except OSError:
        if tmp.exists():
            tmp.unlink()
        raise
    return discover_id


def load_discovery(discover_id: str) -> dict | None:
    """Return cached payload for *discover_id* or None if missing."""
    if not _ID_PATTERN.fullmatch(discover_id):
        return None
    path = _cache_dir() / f"{discover_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def update_discovery(discover_id: str, payload: dict) -> bool:
    """Update an existing discovery cache entry. Returns True on success."""
    if not _ID_PATTERN.fullmatch(discover_id):
        return False
    path = _cache_dir() / f"{discover_id}.json"
    if not path.exists():
        return False
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload))
        os.replace(tmp, path)
        return True
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return False
