"""Single chokepoint for browser-using fetches.

Dispatches to one of four backends based on `ServiceConfig.chosen_backend()`:
    • bundled            — in-process Playwright via `intercept_network`
    • playwright_server  — connect over WebSocket to a remote Playwright server
    • browserless        — connect over CDP to a Browserless instance
    • scrapling_serve    — HTTP POST to a remote Scrapling-serve cluster

All backends return ``(rendered_html, captured_json_responses)``. The shape of
each captured dict is identical:
    {"url": str, "method": str, "status": int,
     "content_type": str, "body": dict|list,
     "request_post_data": str | None}

`scrapling_serve` does not expose XHR capture, so its `captured` list is always
empty. Playwright is imported lazily inside the branches that need it so a
deployment that only uses Scrapling-serve has zero Playwright on the import path.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.services.config import ServiceConfig


async def fetch_with_capture(
    url: str,
    services: ServiceConfig,
    *,
    timeout: int = 30,
    extra_wait: float = 2.5,
    capture_responses: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Load *url* in whichever backend `services.chosen_backend()` selects."""
    services = services.normalised()
    backend = services.chosen_backend()

    if backend == "bundled":
        from app.discovery.network_intercept import intercept_network
        return await intercept_network(url, timeout=timeout, extra_wait=extra_wait)

    if backend == "playwright_server":
        return await _fetch_via_playwright_server(
            url, services, timeout=timeout, extra_wait=extra_wait
        )

    if backend == "browserless":
        return await _fetch_via_browserless(
            url, services, timeout=timeout, extra_wait=extra_wait
        )

    if backend == "scrapling_serve":
        return await _fetch_via_scrapling_serve(
            url, services, timeout=timeout, extra_wait=extra_wait
        )

    # Unreachable — chosen_backend() returns one of the four literals above.
    from app.discovery.network_intercept import intercept_network
    return await intercept_network(url, timeout=timeout, extra_wait=extra_wait)


async def _fetch_via_playwright_server(
    url: str,
    services: ServiceConfig,
    *,
    timeout: int,
    extra_wait: float,
) -> tuple[str, list[dict[str, Any]]]:
    from playwright.async_api import async_playwright  # local import
    from app.discovery.network_intercept import _run_capture, _get_semaphore

    async with _get_semaphore():
        async with async_playwright() as pw:
            browser = await pw.chromium.connect(
                ws_endpoint=services.playwright_server_url
            )
            try:
                return await _run_capture(browser, url, timeout, extra_wait)
            finally:
                await browser.close()


async def _fetch_via_browserless(
    url: str,
    services: ServiceConfig,
    *,
    timeout: int,
    extra_wait: float,
) -> tuple[str, list[dict[str, Any]]]:
    from playwright.async_api import async_playwright  # local import
    from app.discovery.network_intercept import _run_capture, _get_semaphore

    endpoint = services.browserless_url
    if services.auth_token and "?token=" not in endpoint:
        sep = "&" if "?" in endpoint else "?"
        endpoint = f"{endpoint}{sep}token={services.auth_token}"

    async with _get_semaphore():
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(endpoint)
            try:
                return await _run_capture(browser, url, timeout, extra_wait)
            finally:
                await browser.close()


async def _fetch_via_scrapling_serve(
    url: str,
    services: ServiceConfig,
    *,
    timeout: int,
    extra_wait: float,
) -> tuple[str, list[dict[str, Any]]]:
    headers = {"Accept": "application/json"}
    if services.auth_token:
        headers["Authorization"] = f"Bearer {services.auth_token}"

    payload = {
        "url": url,
        "stealth": True,
        "render_js": True,
        "wait": int(extra_wait * 1000),
        "timeout": timeout * 1000,
    }

    endpoint = f"{services.scrapling_serve_url}/fetch"

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 10, connect=10)) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)

    if 200 <= resp.status_code < 300:
        try:
            data = resp.json()
        except Exception:
            return "", []
        html = data.get("html", "") if isinstance(data, dict) else ""
        # scrapling_serve doesn't expose XHR capture — callers that need network
        # interception should pick a playwright-backed backend.
        return html, []

    return "", []
