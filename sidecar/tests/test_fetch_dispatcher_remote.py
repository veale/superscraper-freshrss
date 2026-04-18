"""Regression test for remote browser fetch with semaphore (Tier 0.2)."""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest


from app.services.fetch import (
    _fetch_via_playwright_server,
    _fetch_via_browserless,
)
from app.services.config import ServiceConfig


class DummySemaphore:
    def __init__(self):
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.exited = True
        return False


def _install_playwright_mocks(monkeypatch, dummy_semaphore):
    """Install mocks on the real `playwright.async_api` and `_get_semaphore`."""

    monkeypatch.setattr(
        "app.discovery.network_intercept._get_semaphore",
        lambda: dummy_semaphore,
    )

    monkeypatch.setattr(
        "app.discovery.network_intercept._run_capture",
        AsyncMock(return_value=("<html>test</html>", [])),
    )

    dummy_browser = MagicMock()
    dummy_browser.close = AsyncMock(return_value=None)

    dummy_pw = MagicMock()
    dummy_pw.chromium.connect = AsyncMock(return_value=dummy_browser)
    dummy_pw.chromium.connect_over_cdp = AsyncMock(return_value=dummy_browser)

    @asynccontextmanager
    async def fake_async_playwright_cm():
        yield dummy_pw

    def fake_async_playwright():
        return fake_async_playwright_cm()

    import playwright.async_api as real_pw_module
    monkeypatch.setattr(real_pw_module, "async_playwright", fake_async_playwright)


@pytest.mark.asyncio
async def test_fetch_via_playwright_server_no_semaphore_error(monkeypatch):
    dummy_semaphore = DummySemaphore()
    _install_playwright_mocks(monkeypatch, dummy_semaphore)

    config = ServiceConfig(
        fetch_backend="playwright_server",
        playwright_server_url="ws://localhost:9222",
    )

    html, responses = await _fetch_via_playwright_server(
        "https://example.com",
        config,
        timeout=30,
        extra_wait=0,
    )

    assert dummy_semaphore.entered, "Semaphore was not entered"
    assert dummy_semaphore.exited, "Semaphore was not exited"
    assert html == "<html>test</html>"
    assert responses == []


@pytest.mark.asyncio
async def test_fetch_via_browserless_no_semaphore_error(monkeypatch):
    dummy_semaphore = DummySemaphore()
    _install_playwright_mocks(monkeypatch, dummy_semaphore)

    config = ServiceConfig(
        fetch_backend="browserless",
        browserless_url="https://browserless.example",
    )

    html, responses = await _fetch_via_browserless(
        "https://example.com",
        config,
        timeout=30,
        extra_wait=0,
    )

    assert dummy_semaphore.entered, "Semaphore was not entered"
    assert dummy_semaphore.exited, "Semaphore was not exited"
    assert html == "<html>test</html>"
    assert responses == []
