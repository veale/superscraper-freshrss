"""Unit tests for fetch dispatcher backends."""
from __future__ import annotations

from typing import Any

from unittest.mock import AsyncMock

import pytest

from app.services.config import ServiceConfig
from app.services.fetch import _fetch_via_playwright_server, fetch_with_capture


@pytest.mark.asyncio
async def test_bundled_backend_calls_intercept_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_intercept(url: str, *, timeout: int, extra_wait: float):
        return "html", []

    mock_intercept = AsyncMock(side_effect=fake_intercept)
    monkeypatch.setattr(
        "app.discovery.network_intercept.intercept_network",
        mock_intercept,
    )
    config = ServiceConfig()
    html, responses = await fetch_with_capture("https://example.com", config)
    assert html == "html"
    assert responses == []
    mock_intercept.assert_awaited_once()


class _FakeScraplingClient:
    def __init__(self, *, captured: dict[str, Any], token: str | None = None) -> None:
        self._captured = captured
        self._token = token

    async def __aenter__(self) -> "_FakeScraplingClient":
        return self

    async def __aexit__(self, exc_type, exc, tb):  # type: ignore[override]
        return False

    async def post(self, endpoint, json, headers):  # type: ignore[override]
        self._captured["endpoint"] = endpoint
        self._captured["json"] = json
        self._captured["headers"] = headers

        class FakeResponse:
            status_code = 200

            def json(self) -> dict[str, Any]:
                return {"html": "rendered"}

        return FakeResponse()


@pytest.mark.asyncio
async def test_scrapling_serve_backend_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.services.fetch.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeScraplingClient(captured=captured),
    )
    config = ServiceConfig(
        fetch_backend="scrapling_serve",
        scrapling_serve_url="http://scrapling:8001",
    )
    html, responses = await fetch_with_capture("https://example.com", config)
    assert html == "rendered"
    assert responses == []
    assert captured["endpoint"].endswith("/fetch")


@pytest.mark.asyncio
async def test_scrapling_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.services.fetch.httpx.AsyncClient",
        lambda *args, **kwargs: _FakeScraplingClient(captured=captured),
    )
    config = ServiceConfig(
        fetch_backend="scrapling_serve",
        scrapling_serve_url="http://scrapling:8001",
        auth_token="secret",
    )
    await fetch_with_capture("https://example.com", config)
    assert captured["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_playwright_backend_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_intercept(url: str, *, timeout: int, extra_wait: float):
        return "html", []

    monkeypatch.setattr(
        "app.discovery.network_intercept.intercept_network",
        AsyncMock(side_effect=fake_intercept),
    )
    config = ServiceConfig(fetch_backend="playwright_server")
    html, responses = await fetch_with_capture("https://example.com", config)
    assert html == "html"
    assert responses == []


class _DummyBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _DummyPlaywright:
    def __init__(self, browser: _DummyBrowser) -> None:
        self.browser = browser
        self.chromium = self

    async def connect(self, ws_endpoint: str) -> _DummyBrowser:
        self.connected = ws_endpoint
        return self.browser


class _DummyAsyncCtx:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # type: ignore[override]
        return False


class _DummySemaphore:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "_DummySemaphore":
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # type: ignore[override]
        self.exited = True
        return False


@pytest.mark.asyncio
async def test_playwright_backend_uses_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy_browser = _DummyBrowser()
    dummy_playwright = _DummyPlaywright(dummy_browser)

    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: _DummyAsyncCtx(dummy_playwright),
    )

    called_args: list[tuple[_DummyBrowser, str, int, float]] = []

    async def fake_capture(browser, url, timeout, extra_wait):
        called_args.append((browser, url, timeout, extra_wait))
        return "html", []

    monkeypatch.setattr(
        "app.discovery.network_intercept._run_capture",
        AsyncMock(side_effect=fake_capture),
    )

    dummy_semaphore = _DummySemaphore()
    monkeypatch.setattr(
        "app.discovery.network_intercept._get_semaphore",
        lambda: dummy_semaphore,
    )

    config = ServiceConfig(
        fetch_backend="playwright_server",
        playwright_server_url="ws://fake-playwright:3000",
    )

    html, responses = await _fetch_via_playwright_server(
        "https://example.com",
        config,
        timeout=5,
        extra_wait=1.0,
    )

    assert html == "html"
    assert responses == []
    assert dummy_semaphore.entered
    assert dummy_semaphore.exited
    assert called_args[0][0] is dummy_browser
