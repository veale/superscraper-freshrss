"""Generic on-disk JSON config store for /scrape/config and /graphql/config.

Each entry is one JSON file: /app/data/{prefix}-configs/{id}.json
IDs are url-safe random slugs; path-traversal is rejected before any I/O.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from pathlib import Path
from typing import Callable

_DATA_DIR = Path(os.getenv("AUTOFEED_DATA_DIR", "/app/data"))
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _data_dir() -> Path:
    env = os.getenv("AUTOFEED_DATA_DIR")
    if env:
        return Path(env)
    return _DATA_DIR


def _config_dir(prefix: str) -> Path:
    return _data_dir() / f"{prefix}-configs"


def _safe(config_id: str) -> bool:
    return bool(_SAFE_ID.fullmatch(config_id))


def save_config(
    prefix: str,
    payload: dict,
    *,
    post_process: Callable[[str, dict], dict] | None = None,
) -> str:
    """Write *payload* to disk and return the new config_id."""
    config_id = secrets.token_urlsafe(12)
    if post_process is not None:
        payload = post_process(config_id, payload)
    d = _config_dir(prefix)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{config_id}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, p)
    return config_id


def load_config(prefix: str, config_id: str) -> dict | None:
    if not _safe(config_id):
        return None
    p = _config_dir(prefix) / f"{config_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def update_config(prefix: str, config_id: str, payload: dict) -> bool:
    """Overwrite an existing config in place. Returns False if *config_id*
    is unsafe or not present."""
    if not _safe(config_id):
        return False
    p = _config_dir(prefix) / f"{config_id}.json"
    if not p.exists():
        return False
    tmp = p.with_suffix(".tmp")
    payload = {**payload, "cache_key": config_id}
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, p)
    return True


def delete_config(prefix: str, config_id: str) -> bool:
    if not _safe(config_id):
        return False
    p = _config_dir(prefix) / f"{config_id}.json"
    if not p.exists():
        return False
    p.unlink()
    return True
