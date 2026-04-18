"""Tests for selector_generation — utility class stripping and cross-tag unions."""
import pytest
from app.discovery.selector_generation import (
    _is_utility_class,
    _meaningful_classes,
    _signature,
    _attrs_to_xpath_predicate,
    generate_xpath_candidates,
)


def test_utility_class_plain():
    assert _is_utility_class("w-full")
    assert _is_utility_class("py-4")
    assert _is_utility_class("md:pr-4")
    assert _is_utility_class("hover:bg-blue-500")
    assert _is_utility_class("flex")
    assert _is_utility_class("hidden")


def test_component_class_not_utility():
    assert not _is_utility_class("media-list__item")
    assert not _is_utility_class("grid-item")
    assert not _is_utility_class("media-block")
    assert not _is_utility_class("card")
    assert not _is_utility_class("news-item")


def test_meaningful_classes_strips_utilities():
    result = _meaningful_classes("media-list__item py-4 w-full md:pr-4 md:pl-0 md:w-1/2")
    assert result == "media-list__item"


def test_meaningful_classes_preserves_multiple_component_classes():
    result = _meaningful_classes("card teaser flex items-center gap-4")
    assert result == "card teaser"


def test_signature_deduplicates_utility_variants():
    sig1 = _signature("li", {"class": "media-list__item py-4 w-full"})
    sig2 = _signature("li", {"class": "media-list__item py-4 border-t border-gray-200 first:border-0"})
    assert sig1 == sig2


def test_attrs_to_xpath_predicate_uses_component_class():
    pred = _attrs_to_xpath_predicate({"class": "media-list__item py-4 w-full"})
    assert "media-list__item" in pred
    assert "py-4" not in pred
    assert "w-full" not in pred


def test_generate_xpath_finds_media_block():
    html = """
    <html><body>
    <ul>
      <li class="media-list__item py-4"><h2>Item 1</h2><a href="/1">Link</a></li>
      <li class="media-list__item py-4 border-t"><h2>Item 2</h2><a href="/2">Link</a></li>
      <li class="media-list__item py-4 border-t first:border-0"><h2>Item 3</h2><a href="/3">Link</a></li>
    </ul>
    </body></html>
    """
    candidates = generate_xpath_candidates(html)
    selectors = [c.item_selector for c in candidates]
    assert any("media-list__item" in s for s in selectors), f"Expected media-list__item in {selectors}"


def test_cross_tag_union_generated():
    """li and article items on the same page should produce a union candidate."""
    html = """
    <html><body>
    <ul>
      <li class="grid-item"><h2>A</h2><a href="/a">a</a></li>
      <li class="grid-item"><h2>B</h2><a href="/b">b</a></li>
      <li class="grid-item"><h2>C</h2><a href="/c">c</a></li>
    </ul>
    <section>
      <article class="media-block"><h2>D</h2><a href="/d">d</a></article>
      <article class="media-block"><h2>E</h2><a href="/e">e</a></article>
      <article class="media-block"><h2>F</h2><a href="/f">f</a></article>
    </section>
    </body></html>
    """
    candidates = generate_xpath_candidates(html)
    union_selectors = [c.item_selector for c in candidates if c.item_selector_union]
    assert any("grid-item" in s and "media-block" in s for s in union_selectors), \
        f"Expected cross-tag union in {union_selectors}"
