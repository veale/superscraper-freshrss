"""Tests for the _dot_get path-walker utility in scrape.py."""

from __future__ import annotations

import sys, os

import pytest
from app.scraping.scrape import _dot_get


def test_empty_path_returns_object():
    obj = {"a": 1}
    assert _dot_get(obj, "") is obj


def test_single_key():
    assert _dot_get({"a": 1}, "a") == 1


def test_nested_path():
    obj = {"a": {"b": [10, 20]}}
    assert _dot_get(obj, "a.b.1") == 20


def test_missing_key_returns_none():
    assert _dot_get({"a": 1}, "b") is None


def test_numeric_index_out_of_range():
    assert _dot_get({"a": [1, 2]}, "a.5") is None


def test_deep_nested():
    obj = {"x": {"y": {"z": "found"}}}
    assert _dot_get(obj, "x.y.z") == "found"


def test_list_index_zero():
    assert _dot_get([10, 20, 30], "0") == 10


def test_none_object_returns_none():
    assert _dot_get(None, "a") is None
