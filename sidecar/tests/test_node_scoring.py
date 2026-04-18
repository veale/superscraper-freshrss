"""Tests for node_scoring — verifying the relaxed Readability regex."""
import pytest
from app.discovery.node_scoring import node_score, is_unlikely_candidate


def test_media_block_scores_positive():
    score, unlikely = node_score("article", "media-block flex w-full", "", "")
    assert score > 0
    assert not unlikely


def test_media_list_item_scores_positive():
    score, unlikely = node_score("li", "media-list__item py-4", "", "")
    assert score > 0
    assert not unlikely


def test_grid_item_scores_positive():
    score, unlikely = node_score("li", "grid-item", "", "")
    assert score > 0
    assert not unlikely


def test_footer_is_unlikely():
    score, unlikely = node_score("div", "footer-copyright", "footer", "")
    assert unlikely


def test_ad_banner_is_unlikely():
    score, unlikely = node_score("div", "ad-banner", "", "")
    assert unlikely


def test_card_scores_positive():
    score, unlikely = node_score("div", "card news-item", "", "")
    assert score > 0
    assert not unlikely


def test_media_no_longer_penalised():
    # Previously `media` in class would score -25 (NEGATIVE_RE matched).
    # After the fix it should be neutral or positive.
    score_before_fix_would_have_been_negative = False  # just documentation
    score, unlikely = node_score("div", "media-object", "", "")
    assert score >= 0
    assert not unlikely


def test_related_still_penalised():
    # "related-block" has no positive-RE match, so NEGATIVE_RE's -25 dominates.
    # It's also in UNLIKELY_CANDIDATES_RE, so unlikely=True.
    score, unlikely = node_score("div", "related-block", "", "")
    assert unlikely


def test_nav_role_unlikely():
    score, unlikely = node_score("nav", "", "", "navigation")
    assert unlikely
