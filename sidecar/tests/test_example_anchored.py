"""Tests for example-anchored item-container finder."""
import pytest
from lxml import html as lxml_html
from app.discovery.example_anchored import find_item_selectors_from_example


_HRW_LIKE_HTML = """
<html><body>
<ul class="media-list__items">
  <li class="media-list__item py-4">
    <article class="media-block">
      <h2 class="media-block__title">Remembering a Steadfast Hong Kong Democracy Activist</h2>
      <a href="/news/2024/01/01/hong-kong">Read more</a>
    </article>
  </li>
  <li class="media-list__item py-4">
    <article class="media-block">
      <h2 class="media-block__title">Myanmar Junta Airstrikes Kill Civilians</h2>
      <a href="/news/2024/01/02/myanmar">Read more</a>
    </article>
  </li>
  <li class="media-list__item py-4">
    <article class="media-block">
      <h2 class="media-block__title">Iran: Protesters Face Execution</h2>
      <a href="/news/2024/01/03/iran">Read more</a>
    </article>
  </li>
</ul>
</body></html>
"""


def test_finds_container_from_title_example():
    results = find_item_selectors_from_example(
        _HRW_LIKE_HTML,
        "Remembering a Steadfast Hong Kong Democracy Activist",
    )
    assert results, "Expected at least one candidate"
    # The first result should reference a repeating container class
    first = results[0]
    assert any(cls in first for cls in ("media-list__item", "media-block", "li", "article")), \
        f"Unexpected selector: {first}"


def test_returned_selector_matches_multiple_elements():
    results = find_item_selectors_from_example(
        _HRW_LIKE_HTML,
        "Remembering a Steadfast Hong Kong Democracy Activist",
    )
    assert results
    tree = lxml_html.fromstring(_HRW_LIKE_HTML)
    hits = tree.xpath(results[0])
    assert len(hits) >= 2, f"Selector {results[0]!r} matched only {len(hits)} element(s)"


def test_empty_example_returns_empty():
    result = find_item_selectors_from_example(_HRW_LIKE_HTML, "")
    assert result == []


def test_empty_html_returns_empty():
    result = find_item_selectors_from_example("", "Remembering a Steadfast Hong Kong")
    assert result == []


def test_nonrepeating_container_returns_empty_or_useless():
    html = "<html><body><h1>My Page Title</h1></body></html>"
    result = find_item_selectors_from_example(html, "My Page Title")
    # Either returns empty (title is not in a repeating container) or no crash.
    assert isinstance(result, list)


def test_example_not_found_returns_empty():
    result = find_item_selectors_from_example(_HRW_LIKE_HTML, "This text does not exist on the page")
    assert result == []
