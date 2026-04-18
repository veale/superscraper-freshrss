"""Unit tests for ServiceConfig (Workstream A)."""
from __future__ import annotations

from typing import Any

import pytest

from app.services.config import ServiceConfig


def _clear_tracked_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "AUTOFEED_FETCH_BACKEND",
        "AUTOFEED_PLAYWRIGHT_WS",
        "AUTOFEED_BROWSERLESS_WS",
        "AUTOFEED_SCRAPLING_URL",
        "AUTOFEED_RSS_BRIDGE_URL",
        "AUTOFEED_SERVICES_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_default_service_config_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tracked_env(monkeypatch)
    config = ServiceConfig()
    assert config.fetch_backend == "bundled"
    assert config.playwright_server_url == ""
    assert config.browserless_url == ""
    assert config.scrapling_serve_url == ""
    assert config.rss_bridge_url == ""
    assert config.auth_token == ""


def test_chosen_backend_falls_back_on_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tracked_env(monkeypatch)
    config = ServiceConfig(fetch_backend="browserless")
    assert config.chosen_backend() == "bundled"


def test_chosen_backend_respects_url() -> None:
    config = ServiceConfig(
        fetch_backend="browserless",
        browserless_url="ws://remote:3000",
    )
    assert config.chosen_backend() == "browserless"


def test_normalised_strips_trailing_slashes() -> None:
    config = ServiceConfig(
        playwright_server_url="ws://playwright:3000/",
        browserless_url="ws://browserless:3000/",
        scrapling_serve_url="http://scrapling:8001/",
        rss_bridge_url="http://rss-bridge:3000/",
    )
    norm = config.normalised()
    assert norm.playwright_server_url == "ws://playwright:3000"
    assert norm.browserless_url == "ws://browserless:3000"
    assert norm.scrapling_serve_url == "http://scrapling:8001"
    assert norm.rss_bridge_url == "http://rss-bridge:3000"
    assert config.playwright_server_url.endswith("/")


def test_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tracked_env(monkeypatch)
    monkeypatch.setenv("AUTOFEED_FETCH_BACKEND", "scrapling_serve")
    config = ServiceConfig()
    assert config.fetch_backend == "scrapling_serve"


def test_override_beats_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tracked_env(monkeypatch)
    monkeypatch.setenv("AUTOFEED_FETCH_BACKEND", "scrapling_serve")
    config = ServiceConfig(fetch_backend="bundled")
    assert config.fetch_backend == "bundled"
