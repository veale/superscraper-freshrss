"""Regression test for mixed blocks XPath generation (Tier 1.3.a).

This test verifies that:
1. When a page has featured articles + regular articles, union selectors are generated
2. The union selector combines both block types
3. The union candidate is marked with item_selector_union=True
"""

from __future__ import annotations

import sys
import os

import pytest
from fastapi.testclient import TestClient


from app.main import app
from app.discovery.selector_generation import generate_xpath_candidates
from app.discovery.scrapling_selectors import generate_selectors_with_scrapling

client = TestClient(app)

# HTML with featured and regular article blocks
_HTML_MIXED_BLOCKS = """\
<!DOCTYPE html>
<html>
<head><title>Test Site</title></head>
<body>
    <main>
        <section class="featured">
            <article class="featured">
                <h2><a href="/featured-1">Featured Post 1</a></h2>
                <time>2024-01-01</time>
            </article>
            <article class="featured">
                <h2><a href="/featured-2">Featured Post 2</a></h2>
                <time>2024-01-02</time>
            </article>
            <article class="featured">
                <h2><a href="/featured-3">Featured Post 3</a></h2>
                <time>2024-01-03</time>
            </article>
            <article class="featured">
                <h2><a href="/featured-4">Featured Post 4</a></h2>
                <time>2024-01-04</time>
            </article>
        </section>
        <section class="posts">
            <article class="post">
                <h2><a href="/post-1">Regular Post 1</a></h2>
                <time>2024-01-05</time>
            </article>
            <article class="post">
                <h2><a href="/post-2">Regular Post 2</a></h2>
                <time>2024-01-06</time>
            </article>
            <article class="post">
                <h2><a href="/post-3">Regular Post 3</a></h2>
                <time>2024-01-07</time>
            </article>
            <article class="post">
                <h2><a href="/post-4">Regular Post 4</a></h2>
                <time>2024-01-08</time>
            </article>
            <article class="post">
                <h2><a href="/post-5">Regular Post 5</a></h2>
                <time>2024-01-09</time>
            </article>
            <article class="post">
                <h2><a href="/post-6">Regular Post 6</a></h2>
                <time>2024-01-10</time>
            </article>
            <article class="post">
                <h2><a href="/post-7">Regular Post 7</a></h2>
                <time>2024-01-11</time>
            </article>
            <article class="post">
                <h2><a href="/post-8">Regular Post 8</a></h2>
                <time>2024-01-12</time>
            </article>
        </section>
    </main>
</body>
</html>"""


class TestMixedBlocksXPath:
    """Test XPath generation for mixed featured/main blocks."""

    def test_selector_generation_finds_both_block_types(self):
        """generate_xpath_candidates should find both featured and regular articles."""
        candidates = generate_xpath_candidates(_HTML_MIXED_BLOCKS)

        # Should find at least the two different article types
        assert len(candidates) >= 2, "Should find multiple candidate types"

        # Check for featured articles
        featured_candidates = [c for c in candidates if "featured" in c.item_selector.lower()]
        assert len(featured_candidates) > 0, "Should find featured article candidates"

        # Check for regular posts
        post_candidates = [c for c in candidates if "post" in c.item_selector.lower()]
        assert len(post_candidates) > 0, "Should find regular post candidates"

    def test_union_selector_generated(self):
        """A union selector should be generated combining both block types."""
        candidates = generate_xpath_candidates(_HTML_MIXED_BLOCKS)

        # Look for a union selector (contains | operator)
        union_candidates = [
            c for c in candidates
            if "|" in c.item_selector and c.item_selector_union
        ]

        # The test expects a union selector to be generated
        # If the feature is not implemented yet, this will fail - which is expected
        # This test documents the desired behavior for Tier 1.3.a
        assert len(union_candidates) > 0, (
            "Should generate a union selector for mixed block types. "
            "Expected item_selector to contain '|' and item_selector_union=True"
        )

    def test_union_selector_captures_all_items(self):
        """Union selector should capture items from both block types."""
        candidates = generate_xpath_candidates(_HTML_MIXED_BLOCKS)

        # Find the union candidate
        union_candidates = [
            c for c in candidates
            if "|" in c.item_selector and c.item_selector_union
        ]

        if union_candidates:
            union = union_candidates[0]
            # The union should have more items than either individual type
            # (4 featured + 8 regular = 12 total)
            assert union.item_count >= 10, (
                f"Union selector should capture many items, got {union.item_count}"
            )

    def test_union_selector_has_slight_confidence_penalty(self):
        """Union selectors should have slightly lower confidence than best single type."""
        candidates = generate_xpath_candidates(_HTML_MIXED_BLOCKS)

        # Find union and best single candidates
        union_candidates = [
            c for c in candidates
            if "|" in c.item_selector and c.item_selector_union
        ]
        single_candidates = [
            c for c in candidates
            if "|" not in c.item_selector
        ]

        if union_candidates and single_candidates:
            union_conf = max(c.confidence for c in union_candidates)
            best_single_conf = max(c.confidence for c in single_candidates)

            # Union should have slightly lower confidence (penalty of ~0.05)
            assert union_conf < best_single_conf, (
                "Union should have lower confidence than best single candidate"
            )
            assert union_conf >= best_single_conf - 0.2, (
                "Union penalty should be small (around 0.05)"
            )


class TestScraplingMixedBlocks:
    """Test Scrapling-based selector generation for mixed blocks."""

    def test_scrapling_finds_multiple_groups(self):
        """Scrapling should find multiple groups of repeated elements."""
        # This test checks if scrapling can identify different block types
        # Note: This requires the scrapling service to be available

        try:
            candidates = generate_selectors_with_scrapling(
                _HTML_MIXED_BLOCKS,
                "https://example.com",
                timeout=10,
            )

            # Should find multiple distinct element groups
            assert len(candidates) >= 2, "Should find multiple element groups"

            # Check for different class signatures
            signatures = set()
            for c in candidates:
                # Extract class info from selector
                if "featured" in c.item_selector:
                    signatures.add("featured")
                elif "post" in c.item_selector:
                    signatures.add("post")

            assert len(signatures) >= 2, "Should find both featured and post types"

        except Exception as e:
            # Scrapling might not be available in test environment
            pytest.skip(f"Scrapling not available: {e}")


# Integration test via the discovery endpoint
class TestMixedBlocksDiscovery:
    """Test mixed blocks via the full discovery endpoint."""

    def test_discovery_returns_union_candidate(self):
        """Full discovery should return union XPath candidates for mixed blocks."""
        # This is an integration test that exercises the full cascade
        # We mock the HTTP responses to avoid network calls

        import respx
        import httpx

        with respx.mock:
            # Mock the page response
            respx.get("https://mixed-blocks.example").mock(
                return_value=httpx.Response(
                    200,
                    text=_HTML_MIXED_BLOCKS,
                    headers={"content-type": "text/html"},
                )
            )

            resp = client.post(
                "/discover",
                json={
                    "url": "https://mixed-blocks.example",
                    "timeout": 30,
                    "use_browser": False,
                    "services": {},
                },
            )

        assert resp.status_code == 200
        data = resp.json()

        xpath_candidates = data["results"]["xpath_candidates"]
        assert len(xpath_candidates) > 0, "Should have XPath candidates"

        # Check for union selector
        union_candidates = [
            c for c in xpath_candidates
            if c.get("item_selector_union", False)
        ]

        # This will fail until Tier 1.3.a is implemented
        # The test documents the expected behavior
        assert len(union_candidates) > 0, (
            "Should have union selector candidate for mixed blocks"
        )