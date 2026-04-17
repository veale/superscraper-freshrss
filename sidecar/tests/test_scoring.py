"""Tests for the scoring module."""

import sys
import os

# Allow imports from the sidecar app package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.discovery.scoring import score_feed_likeness, find_best_array_path


# ── score_feed_likeness ──────────────────────────────────────────────────

def test_empty_data():
    assert score_feed_likeness(None) == 0.0
    assert score_feed_likeness([]) == 0.0
    assert score_feed_likeness({}) == 0.0
    assert score_feed_likeness("hello") == 0.0


def test_non_dict_list():
    assert score_feed_likeness([1, 2, 3]) == 0.0
    assert score_feed_likeness(["a", "b"]) == 0.0


def test_perfect_feed():
    """A list of items with title, url, date, content should score highly."""
    items = [
        {
            "title": f"Post {i}",
            "url": f"https://example.com/post/{i}",
            "date": "2026-04-17",
            "content": "Lorem ipsum dolor sit amet.",
            "author": "Alice",
        }
        for i in range(20)
    ]
    sc = score_feed_likeness(items)
    assert sc >= 0.8, f"Expected >= 0.8, got {sc}"


def test_minimal_feed():
    """Items with only a title should still score above zero."""
    items = [{"title": f"Item {i}"} for i in range(5)]
    sc = score_feed_likeness(items)
    assert 0.2 < sc < 0.6, f"Expected 0.2–0.6, got {sc}"


def test_no_feed_keys():
    """Items with random keys should score very low."""
    items = [{"foo": i, "bar": "baz", "qux": True} for i in range(10)]
    sc = score_feed_likeness(items)
    assert sc < 0.3, f"Expected < 0.3, got {sc}"


def test_wrapped_dict():
    """A dict with a single array-of-dicts value should be unwrapped."""
    data = {
        "status": "ok",
        "data": [
            {"title": f"Art {i}", "link": f"/art/{i}", "published": "2026-01-01"}
            for i in range(10)
        ],
    }
    sc = score_feed_likeness(data)
    assert sc >= 0.5, f"Expected >= 0.5, got {sc}"


def test_case_insensitive_keys():
    """Keys like 'Title', 'URL', 'Date' should be recognised."""
    items = [
        {"Title": "Post", "URL": "https://x.com/1", "Date": "2026-01-01"}
        for _ in range(5)
    ]
    sc = score_feed_likeness(items)
    assert sc >= 0.5, f"Expected >= 0.5, got {sc}"


# ── find_best_array_path ─────────────────────────────────────────────────

def test_find_best_array_path_simple():
    data = {
        "meta": {"version": 1},
        "results": {
            "posts": [
                {"title": f"P{i}", "url": f"/p/{i}", "date": "2026-01-01"}
                for i in range(10)
            ]
        },
    }
    paths = find_best_array_path(data)
    assert len(paths) >= 1
    best_path, best_items, best_score = paths[0]
    assert best_path == "results.posts"
    assert len(best_items) == 10
    assert best_score >= 0.4


def test_find_best_array_path_multiple():
    """When there are multiple arrays, the best-scoring one comes first."""
    data = {
        "tags": [{"id": 1, "name": "tech"}, {"id": 2, "name": "news"}],
        "articles": [
            {"title": f"A{i}", "url": f"/a/{i}", "published_at": "2026-04-01"}
            for i in range(15)
        ],
    }
    paths = find_best_array_path(data)
    assert len(paths) >= 2
    assert paths[0][0] == "articles"
    assert paths[0][2] > paths[1][2]


# ── Run ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
