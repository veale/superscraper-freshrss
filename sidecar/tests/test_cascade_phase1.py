"""Offline unit tests for the Phase 1 discovery cascade.

Verifies that a failure in one sub-step does not prevent other steps from
producing results — the cascade must be resilient to individual step exceptions.
"""

from __future__ import annotations

import sys
import os


import pytest
import respx
import httpx

pytestmark = pytest.mark.asyncio

_SIMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
  <ul>
    <li class="post"><a href="/1">Post One</a></li>
    <li class="post"><a href="/2">Post Two</a></li>
    <li class="post"><a href="/3">Post Three</a></li>
  </ul>
</body>
</html>"""


@respx.mock
async def test_embedded_json_exception_leaves_other_steps_intact(monkeypatch):
    """If embedded_json.detect_embedded_json raises, cascade should still
    populate xpath_candidates from the other steps and record the error."""
    import app.discovery.cascade as cascade_mod

    def _raise(html):
        raise RuntimeError("boom")

    monkeypatch.setattr(cascade_mod, "detect_embedded_json", _raise)

    # Catch the initial page fetch (GET).
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(200, text=_SIMPLE_HTML, headers={"content-type": "text/html"})
    )
    # Absorb all other requests (RSS probes use HEAD + GET; JS analysis uses GET).
    respx.route(url__regex=r"https://example\.com/.*").mock(
        return_value=httpx.Response(404, text="not found")
    )

    from app.discovery.cascade import run_discovery
    from app.models.schemas import DiscoverRequest

    req = DiscoverRequest(url="https://example.com/", timeout=10, use_browser=False)
    resp = await run_discovery(req)

    assert any("Embedded JSON detection error: boom" in e for e in resp.errors), (
        f"Expected embedded JSON error in errors list, got: {resp.errors}"
    )
    # XPath candidates should still be generated from the static HTML.
    assert resp.results.xpath_candidates is not None
    assert resp.results is not None
    assert isinstance(resp.errors, list)
