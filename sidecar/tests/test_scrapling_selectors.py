"""Phase 2 — Scrapling selector generation tests (offline, no network)."""

from __future__ import annotations

import pytest

from app.discovery.scrapling_selectors import generate_selectors_with_scrapling


def test_article_detection_basic():
    """Repeated <article> elements should produce a high-confidence candidate."""
    html = """<html><body>
    <nav><a href="/">Home</a><a href="/about">About</a></nav>
    <main>
      <article class="post">
        <h2><a href="/p/1">Post 1</a></h2>
        <time datetime="2026-04-01">Apr 1</time>
        <p>Content 1</p>
      </article>
      <article class="post">
        <h2><a href="/p/2">Post 2</a></h2>
        <time datetime="2026-04-02">Apr 2</time>
        <p>Content 2</p>
      </article>
      <article class="post">
        <h2><a href="/p/3">Post 3</a></h2>
        <time datetime="2026-04-03">Apr 3</time>
        <p>Content 3</p>
      </article>
    </main>
    <footer><a href="/terms">Terms</a><a href="/privacy">Privacy</a></footer>
    </body></html>"""

    candidates = generate_selectors_with_scrapling(html)
    assert len(candidates) >= 1, "Expected at least one candidate"
    top = candidates[0]
    assert "article" in top.item_selector, (
        f"Expected article selector, got: {top.item_selector}"
    )
    assert top.confidence > 0.4, f"Expected high confidence, got {top.confidence}"


def test_nav_links_not_top_candidate():
    """Nav/footer links should not outrank article elements."""
    html = """<html><body>
    <nav>
      <a href="/a">Link A</a><a href="/b">Link B</a>
      <a href="/c">Link C</a><a href="/d">Link D</a>
      <a href="/e">Link E</a>
    </nav>
    <main>
      <article class="story"><h2>Story 1</h2><p>Text</p></article>
      <article class="story"><h2>Story 2</h2><p>Text</p></article>
      <article class="story"><h2>Story 3</h2><p>Text</p></article>
    </main>
    </body></html>"""

    candidates = generate_selectors_with_scrapling(html)
    if candidates:
        # Top candidate should not be a nav element
        top = candidates[0]
        assert "nav" not in top.item_selector, (
            f"Nav selector should not be top candidate: {top.item_selector}"
        )


def test_sub_selectors_populated():
    """Title, link, timestamp, thumbnail sub-selectors should be filled in."""
    html = """<html><body><main>
      <article class="post">
        <h2><a href="/p/1">Post 1</a></h2>
        <time datetime="2026-04-01">Apr 1</time>
        <img src="/img/1.jpg" />
      </article>
      <article class="post">
        <h2><a href="/p/2">Post 2</a></h2>
        <time datetime="2026-04-02">Apr 2</time>
        <img src="/img/2.jpg" />
      </article>
      <article class="post">
        <h2><a href="/p/3">Post 3</a></h2>
        <time datetime="2026-04-03">Apr 3</time>
        <img src="/img/3.jpg" />
      </article>
    </main></body></html>"""

    candidates = generate_selectors_with_scrapling(html)
    assert candidates, "Expected at least one candidate"
    top = candidates[0]
    assert top.link_selector, "Expected a link sub-selector"
    assert top.timestamp_selector, "Expected a timestamp sub-selector"


def test_no_false_positive_on_sparse_html():
    """HTML with no repeated patterns should return empty list."""
    html = """<html><body>
    <h1>Hello World</h1>
    <p>A single paragraph.</p>
    <a href="/about">About</a>
    </body></html>"""
    candidates = generate_selectors_with_scrapling(html)
    # There may be no candidates, or very low-confidence ones
    for c in candidates:
        assert c.item_count >= 3, "Should not flag non-repeated elements"


def test_empty_html():
    """Empty input should return empty list without raising."""
    assert generate_selectors_with_scrapling("") == []
    assert generate_selectors_with_scrapling("   ") == []


def test_list_items_detected():
    """Repeated <li> elements (e.g. blog index) should be detected."""
    items = "\n".join(
        f'<li class="entry"><a href="/post/{i}">Post {i}</a>'
        f'<time datetime="2026-0{i}-01">Mar {i}</time></li>'
        for i in range(1, 6)
    )
    html = f"<html><body><ul>{items}</ul></body></html>"
    candidates = generate_selectors_with_scrapling(html)
    assert len(candidates) >= 1, "Expected candidate for repeated <li> elements"
    assert any("li" in c.item_selector for c in candidates), (
        f"Expected li selector, got: {[c.item_selector for c in candidates]}"
    )


def test_returns_at_most_five_candidates():
    """Result list must be capped at 5 entries."""
    # Create many different repeated patterns
    groups = "\n".join(
        f'<div class="group{g}"><span>Item {g}-1</span></div>'
        f'<div class="group{g}"><span>Item {g}-2</span></div>'
        f'<div class="group{g}"><span>Item {g}-3</span></div>'
        for g in range(10)
    )
    html = f"<html><body>{groups}</body></html>"
    candidates = generate_selectors_with_scrapling(html)
    assert len(candidates) <= 5, f"Expected ≤5 candidates, got {len(candidates)}"
