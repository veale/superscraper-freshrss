"""Tests for GraphQL detection — offline, no network."""

from __future__ import annotations

import sys
import os

import pytest


from app.discovery.graphql_detect import (
    _extract_op_name,
    _extract_op_type,
    _is_graphql_request,
    detect_graphql_in_capture,
)

# ── _is_graphql_request ───────────────────────────────────────────────────────

def test_is_graphql_url_path():
    assert _is_graphql_request({"url": "https://example.com/graphql", "content_type": "", "request_post_data": ""})


def test_is_graphql_url_with_query_string():
    assert _is_graphql_request({"url": "https://example.com/graphql?op=Foo", "content_type": "", "request_post_data": ""})


def test_is_graphql_content_type():
    assert _is_graphql_request({"url": "https://example.com/api", "content_type": "application/graphql", "request_post_data": ""})


def test_is_graphql_body_with_query_key():
    body = '{"query": "{ posts { title } }", "variables": {}}'
    assert _is_graphql_request({"url": "https://example.com/api", "content_type": "application/json", "request_post_data": body})


def test_not_graphql_plain_api():
    assert not _is_graphql_request({"url": "https://example.com/api/posts", "content_type": "application/json", "request_post_data": ""})


def test_not_graphql_url_with_graphql_in_name():
    # /graphql-foo should NOT match (the regex requires /graphql at end or before ?)
    assert not _is_graphql_request({"url": "https://example.com/graphql-foo", "content_type": "", "request_post_data": ""})


def test_not_graphql_body_missing_query():
    body = '{"data": {"posts": []}}'
    assert not _is_graphql_request({"url": "https://example.com/api", "content_type": "application/json", "request_post_data": body})


# ── _extract_op_name / _extract_op_type ─────────────────────────────────────

def test_extract_op_name_named():
    assert _extract_op_name("query MyQuery { posts { title } }") == "MyQuery"


def test_extract_op_name_anonymous():
    assert _extract_op_name("{ posts { title } }") == ""


def test_extract_op_type_query():
    assert _extract_op_type("query GetPosts { posts { title } }") == "query"


def test_extract_op_type_mutation():
    assert _extract_op_type("mutation CreatePost { createPost { id } }") == "mutation"


def test_extract_op_type_subscription():
    assert _extract_op_type("subscription OnPost { postAdded { id } }") == "subscription"


def test_extract_op_type_default_anonymous():
    assert _extract_op_type("{ posts { title } }") == "query"


# ── detect_graphql_in_capture ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detect_empty_capture():
    result = await detect_graphql_in_capture([])
    assert result == []


@pytest.mark.asyncio
async def test_detect_deduplicates_by_endpoint_and_op_name():
    body = '{"query": "query GetPosts { posts { title url } }", "operationName": "GetPosts"}'
    resp = {
        "url": "https://example.com/graphql",
        "content_type": "application/json",
        "request_post_data": body,
        "method": "POST",
        "status": 200,
        "body": {"posts": [{"title": "A", "url": "https://a.com"}, {"title": "B", "url": "https://b.com"}]},
    }
    result = await detect_graphql_in_capture([resp, resp])
    assert len(result) <= 1


@pytest.mark.asyncio
async def test_detect_filters_low_score():
    body = '{"query": "{ junk { foo } }"}'
    resp = {
        "url": "https://example.com/graphql",
        "content_type": "application/json",
        "request_post_data": body,
        "method": "POST",
        "status": 200,
        "body": {"junk": [{"foo": 1}]},  # no feed-like keys -> low score
    }
    result = await detect_graphql_in_capture([resp])
    assert result == []


@pytest.mark.asyncio
async def test_detect_populates_response_path():
    body = '{"query": "query GetPosts { posts { title url date } }", "operationName": "GetPosts"}'
    items = [{"title": f"Post {i}", "url": f"https://example.com/{i}", "date": "2024-01-01"} for i in range(5)]
    resp = {
        "url": "https://example.com/graphql",
        "content_type": "application/json",
        "request_post_data": body,
        "method": "POST",
        "status": 200,
        "body": {"data": {"posts": items}},
    }
    result = await detect_graphql_in_capture([resp])
    assert len(result) == 1
    op = result[0]
    assert op.endpoint == "https://example.com/graphql"
    assert op.operation_name == "GetPosts"
    assert op.response_path != ""
    assert op.detected_via == "network_capture"
