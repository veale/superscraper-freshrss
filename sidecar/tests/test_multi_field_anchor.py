"""Tests for multi_field_anchor — single-row LCA and multi-row union."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.discovery.multi_field_anchor import (
    MultiAnchorResult,
    find_item_from_examples,
    find_items_from_rows,
)
from app.discovery.selector_generation import _is_utility_class

FIXTURE = Path(__file__).parent / "fixtures" / "hrw_free_speech.html"


@pytest.fixture(scope="module")
def hrw_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# ── utility-class regression (A.3 acceptance) ────────────────────────────────


def test_grid_item_not_utility():
    assert _is_utility_class("grid-item") is False


def test_flex_row_is_utility():
    assert _is_utility_class("flex-row") is True


def test_card_content_not_utility():
    assert _is_utility_class("card__content") is False


def test_w_full_is_utility():
    assert _is_utility_class("w-full") is True


def test_flex_container_not_utility():
    assert _is_utility_class("flex-container") is False


def test_flex_bare_is_utility():
    assert _is_utility_class("flex") is True


# ── single-row LCA ────────────────────────────────────────────────────────────


def test_single_title_grid_item(hrw_html):
    result = find_item_from_examples(hrw_html, {"title": "Remembering a Steadfast Hong Kong"})
    assert result is not None
    assert "grid-item" in result.item_selector
    assert result.item_count >= 3


def test_single_title_media_list(hrw_html):
    result = find_item_from_examples(hrw_html, {"title": "Japan's Flag Desecration Law"})
    assert result is not None
    assert "media-list__item" in result.item_selector
    assert result.item_count >= 4


def test_multi_field_grid_item(hrw_html):
    result = find_item_from_examples(
        hrw_html,
        {"title": "Remembering a Steadfast Hong Kong", "timestamp": "April 10, 2026", "author": "Maya Wang"},
    )
    assert result is not None
    assert "grid-item" in result.item_selector
    assert result.item_count >= 3
    assert "title" in result.field_selectors
    assert "card__title" in result.field_selectors["title"]
    assert result.confidence >= 0.5
    assert len(result.item_outer_htmls) >= 2
    assert result.warnings == []


def test_multi_field_media_list(hrw_html):
    result = find_item_from_examples(
        hrw_html,
        {"title": "Japan's Flag Desecration Law", "timestamp": "March 25, 2026"},
    )
    assert result is not None
    assert "media-list__item" in result.item_selector
    assert result.item_count >= 4
    assert "title" in result.field_selectors
    assert result.confidence >= 0.5
    assert result.warnings == []


def test_link_example_grid_item(hrw_html):
    result = find_item_from_examples(hrw_html, {"link": "/news/2026/04/09/thailand-journalists"})
    assert result is not None
    assert "grid-item" in result.item_selector
    assert result.item_count >= 3


def test_nonexistent_returns_none(hrw_html):
    result = find_item_from_examples(hrw_html, {"title": "text that does not exist on the page"})
    assert result is None


def test_empty_examples_returns_none(hrw_html):
    result = find_item_from_examples(hrw_html, {})
    assert result is None


# ── multi-row union (addendum) ────────────────────────────────────────────────


def test_two_rows_union(hrw_html):
    rows = [
        {"title": "Remembering a Steadfast Hong Kong", "timestamp": "April 10, 2026"},
        {"title": "Japan's Flag Desecration Law", "timestamp": "March 25, 2026"},
    ]
    result = find_items_from_rows(hrw_html, rows)
    assert result is not None
    assert "grid-item" in result.item_selector
    assert "media-list__item" in result.item_selector
    assert "|" in result.item_selector
    assert result.item_count >= 7  # 3 grid + 4 media
    assert "title" in result.field_selectors
    assert "|" in result.field_selectors["title"]
    assert "timestamp" in result.field_selectors
    assert any("union selector" in w for w in result.warnings)


def test_single_row_unchanged(hrw_html):
    rows = [{"title": "Remembering a Steadfast Hong Kong", "timestamp": "April 10, 2026"}]
    result = find_items_from_rows(hrw_html, rows)
    assert result is not None
    assert "|" not in result.item_selector
    assert "grid-item" in result.item_selector


def test_empty_rows_returns_none(hrw_html):
    assert find_items_from_rows(hrw_html, []) is None


def test_all_rows_fail_returns_none(hrw_html):
    rows = [
        {"title": "not on page at all"},
        {"title": "also not here"},
    ]
    assert find_items_from_rows(hrw_html, rows) is None


def test_one_row_fails_warns(hrw_html):
    rows = [
        {"title": "Remembering a Steadfast Hong Kong", "timestamp": "April 10, 2026"},
        {"title": "not on page at all"},
    ]
    result = find_items_from_rows(hrw_html, rows)
    assert result is not None
    assert any("row(s) couldn't be located" in w for w in result.warnings)
