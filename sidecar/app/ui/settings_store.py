"""Persistent user preferences — stored in /data/settings.json.

Precedence for any setting (highest first):
    1. Per-request value in the API body (programmatic callers)
    2. This settings store (written by the /settings UI page)
    3. Environment variables (existing ServiceConfig fallbacks, unchanged)
    4. Hardcoded defaults below
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "llm_endpoint": "",
    "llm_api_key": "",
    "llm_model": "gpt-4o-mini",
    "rss_bridge_url": "",
    "rss_bridge_deploy_mode": "auto",
    "fetch_backend": os.getenv("AUTOFEED_FETCH_BACKEND", "bundled"),
    "playwright_server_url": os.getenv("AUTOFEED_PLAYWRIGHT_WS", ""),
    "browserless_url": os.getenv("AUTOFEED_BROWSERLESS_WS", ""),
    "scrapling_serve_url": os.getenv("AUTOFEED_SCRAPLING_URL", ""),
    "services_auth_token": os.getenv("AUTOFEED_SERVICES_TOKEN", ""),
    "auto_deploy_bridges": False,
    "default_ttl": 86400,
    "sftp_host": "",
    "sftp_port": "22",
    "sftp_user": "",
    "sftp_key_path": "",
    "sftp_target_dir": "",
}


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw: dict[str, Any] = json.loads(self._path.read_text())
            for k in _DEFAULTS:
                if k in raw:
                    self._data[k] = raw[k]
        except Exception:
            pass  # keep defaults on any parse / IO error

    def get(self) -> dict[str, Any]:
        return dict(self._data)

    def update(self, **changes: Any) -> None:
        for k, v in changes.items():
            if k in _DEFAULTS:
                self._data[k] = v
        self._write()

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".settings-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self._data, f, indent=2)
                f.write("\n")
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── Convenience accessors for API route handlers ──────────────────────────

    def mask_api_key(self, key: str) -> str:
        if not key:
            return ""
        if len(key) <= 12:
            return "…" + key[-4:]
        return key[:4] + "…" + key[-4:]

    def is_masked_key(self, submitted: str) -> bool:
        stored = self._data.get("llm_api_key", "")
        if not stored:
            return False
        return submitted == self.mask_api_key(stored)


# ── Module-level singleton — call init_store() from main.py on startup ────────

_store: SettingsStore | None = None


def init_store(path: Path) -> None:
    global _store
    _store = SettingsStore(path)


def get_store() -> SettingsStore:
    if _store is None:
        raise RuntimeError("settings_store not initialised — call init_store() first")
    return _store
