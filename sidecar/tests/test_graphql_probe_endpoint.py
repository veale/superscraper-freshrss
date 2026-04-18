"""Tests for probe_graphql_endpoint — offline with respx mocks."""

from __future__ import annotations

import sys
import os

import pytest
import respx
import httpx


from app.discovery.graphql_detect import probe_graphql_endpoint
from app.services.config import ServiceConfig

pytestmark = pytest.mark.asyncio

_ENDPOINT = "http://graphql.test/graphql"


@respx.mock
async def test_no_schema_returns_empty():
    respx.post(_ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {}}))
    result = await probe_graphql_endpoint(_ENDPOINT, ServiceConfig())
    assert result == []


@respx.mock
async def test_400_returns_empty():
    respx.post(_ENDPOINT).mock(return_value=httpx.Response(400, json={"errors": []}))
    result = await probe_graphql_endpoint(_ENDPOINT, ServiceConfig())
    assert result == []


@respx.mock
async def test_timeout_returns_empty():
    respx.post(_ENDPOINT).mock(side_effect=httpx.TimeoutException("timed out"))
    result = await probe_graphql_endpoint(_ENDPOINT, ServiceConfig())
    assert result == []


@respx.mock
async def test_auth_token_sent_as_bearer():
    services = ServiceConfig(auth_token="secret123")
    route = respx.post(_ENDPOINT).mock(return_value=httpx.Response(200, json={"data": {}}))
    await probe_graphql_endpoint(_ENDPOINT, services)
    assert route.called
    request = route.calls[0].request
    assert request.headers.get("Authorization") == "Bearer secret123"


async def test_introspect_false_returns_empty_without_http():
    # Should return [] immediately without any HTTP call.
    with respx.mock() as mock:
        result = await probe_graphql_endpoint(_ENDPOINT, ServiceConfig(), introspect=False)
        assert result == []
        assert mock.calls.call_count == 0
