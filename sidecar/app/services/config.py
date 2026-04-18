"""External-service configuration — Scrapling, Playwright, Browserless, RSS-Bridge.

Every setting follows the same precedence (highest first):
    1. Per-request value in the API body (the FreshRSS extension forwards user prefs)
    2. Environment variable (set in docker-compose.yml or `docker run -e ...`)
    3. Bundled fallback (in-process Playwright + Scrapling, no RSS-Bridge URL)
"""
from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field


# Fetch backends — what actually loads a URL when a browser is needed.
FetchBackend = Literal["bundled", "playwright_server", "browserless", "scrapling_serve"]


class ServiceConfig(BaseModel):
    """Routing/credentials for external helper services.

    Each field falls back to an env var and finally to the bundled default.
    All URLs are normalised by stripping trailing slashes.
    """

    fetch_backend: FetchBackend = Field(
        default_factory=lambda: os.getenv("AUTOFEED_FETCH_BACKEND", "bundled")  # type: ignore[arg-type]
    )

    # Playwright server (https://playwright.dev/docs/docker — `playwright run-server`).
    # Expected format: "ws://host:3000/" — Playwright connects via WebSocket.
    playwright_server_url: str = Field(
        default_factory=lambda: os.getenv("AUTOFEED_PLAYWRIGHT_WS", "")
    )

    # Browserless (https://www.browserless.io/) — expects a CDP WebSocket endpoint.
    # Expected format: "ws://host:3000?token=..." — the token, if any, is part of the URL.
    browserless_url: str = Field(
        default_factory=lambda: os.getenv("AUTOFEED_BROWSERLESS_WS", "")
    )

    # Scrapling-serve (https://github.com/D4Vinci/Scrapling — `scrapling serve`).
    # HTTP REST endpoint, e.g. "http://scrapling:8001". Used for stealth fetching
    # and adaptive scraping in Phase 4 when a remote Scrapling cluster is preferred.
    scrapling_serve_url: str = Field(
        default_factory=lambda: os.getenv("AUTOFEED_SCRAPLING_URL", "")
    )

    # External RSS-Bridge — used by Phase 3 deploy to mount writes through HTTP
    # if the shared volume is not present (e.g. Kubernetes deployments).
    rss_bridge_url: str = Field(
        default_factory=lambda: os.getenv("AUTOFEED_RSS_BRIDGE_URL", "")
    )

    # Optional shared secret added as `Authorization: Bearer …` to all
    # outbound calls to the four services above. None of the official images
    # require auth by default; this is for users behind their own proxy.
    auth_token: str = Field(
        default_factory=lambda: os.getenv("AUTOFEED_SERVICES_TOKEN", "")
    )

    def normalised(self) -> "ServiceConfig":
        """Return a copy with trailing slashes stripped from every URL field."""
        return self.model_copy(update={
            "playwright_server_url": self.playwright_server_url.rstrip("/"),
            "browserless_url": self.browserless_url.rstrip("/"),
            "scrapling_serve_url": self.scrapling_serve_url.rstrip("/"),
            "rss_bridge_url": self.rss_bridge_url.rstrip("/"),
        })

    def chosen_backend(self) -> FetchBackend:
        """Resolve the effective backend, falling back to bundled if the chosen
        backend has no URL configured."""
        b = self.fetch_backend
        if b == "playwright_server" and not self.playwright_server_url:
            return "bundled"
        if b == "browserless" and not self.browserless_url:
            return "bundled"
        if b == "scrapling_serve" and not self.scrapling_serve_url:
            return "bundled"
        return b
