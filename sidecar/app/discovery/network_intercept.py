"""Step 4 — Playwright-based network interception for XHR/fetch API discovery.

Uses raw Playwright (not Scrapling's DynamicFetcher) so we can register the
response listener BEFORE navigation begins, capturing every XHR/fetch request
the page makes.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from playwright.async_api import async_playwright

_semaphore = asyncio.Semaphore(2)  # Max 2 concurrent browser contexts.

# ── Filtering ─────────────────────────────────────────────────────────────────

_API_PATTERNS = re.compile(
    r"/api/|/v[1-9]/|/graphql|/wp-json/|/_next/data/|/feed/|/json/"
    r"|/rest/|/query|/search|/posts|/articles|/entries",
    re.IGNORECASE,
)

_EXCLUDE_PATTERNS = re.compile(
    r"analytics|tracking|pixel|beacon|/logs?(?:/|$)"
    r"|google-analytics|facebook\.com|doubleclick|/ads/"
    r"|sentry\.io|hotjar\.com|cloudflare\.com/cdn-cgi"
    r"|googleapis\.com/(?!.*(?:sheets|drive|blogger))"
    r"|fonts\.|recaptcha|turnstile|hcaptcha"
    r"|\.(?:css|js|png|jpg|jpeg|gif|svg|woff2?|ttf|ico|webp)(?:\?|$)"
    r"|/auth/|/login|/logout|/oauth|/token",
    re.IGNORECASE,
)

_RESOURCE_BLOCK_RE = re.compile(
    r"\.(png|jpg|jpeg|gif|svg|webp|woff2?|ttf|eot|ico|mp4|mp3|pdf|zip)(\?|$)",
    re.IGNORECASE,
)


def _is_excluded(url: str) -> bool:
    return bool(_EXCLUDE_PATTERNS.search(url))


# ── Main public function ───────────────────────────────────────────────────────


async def intercept_network(
    url: str,
    timeout: int = 30,
    extra_wait: float = 2.5,
) -> tuple[str, list[dict[str, Any]]]:
    """Load *url* in a headless browser and capture all JSON responses.

    Returns ``(page_html, captured_json_responses)``.
    Each captured response dict has keys:
      url, method, status, content_type, body, request_post_data
    """
    captured: list[dict[str, Any]] = []

    async with _semaphore:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            page = await context.new_page()

            # Block binary resources to speed up loading.
            async def _abort_binary(route):
                if _RESOURCE_BLOCK_RE.search(route.request.url):
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", _abort_binary)

            # Register response listener BEFORE navigation.
            async def _on_response(response) -> None:
                try:
                    ct = response.headers.get("content-type", "")
                    resp_url = response.url

                    if "json" not in ct.lower():
                        return
                    if _is_excluded(resp_url):
                        return

                    try:
                        body = await response.json()
                    except Exception:
                        return

                    post_data: str | None = None
                    try:
                        post_data = response.request.post_data
                    except Exception:
                        pass

                    captured.append(
                        {
                            "url": resp_url,
                            "method": response.request.method,
                            "status": response.status,
                            "content_type": ct.split(";")[0].strip(),
                            "body": body,
                            "request_post_data": post_data,
                        }
                    )
                except Exception:
                    pass

            page.on("response", _on_response)

            try:
                await page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=timeout * 1000,
                )
            except Exception:
                pass  # Timeout/nav error — still collect what we have.

            # Extra wait for lazy-loaded XHR that fires after initial render.
            await asyncio.sleep(extra_wait)

            try:
                html = await page.content()
            except Exception:
                html = ""

            await context.close()
            await browser.close()

    return html, captured


async def close_browser() -> None:
    """No-op — kept for API compatibility. Browsers are closed per-call."""
    pass
