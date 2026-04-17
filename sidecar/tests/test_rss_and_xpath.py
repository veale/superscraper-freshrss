"""Tests for RSS link parsing and XPath heuristic generation."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# These modules have pydantic imports, so we need a lightweight shim.
# Provide a minimal BaseModel + Field if pydantic isn't installed.
try:
    import pydantic
except ImportError:
    import types

    class _FakeBaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        def __init_subclass__(cls, **kw):
            pass

    def _fake_field(*args, **kw):
        return kw.get("default", kw.get("default_factory", lambda: None)())

    mod = types.ModuleType("pydantic")
    mod.BaseModel = _FakeBaseModel
    mod.Field = _fake_field
    mod.HttpUrl = str
    sys.modules["pydantic"] = mod

# Also shim httpx if not installed.
try:
    import httpx
except ImportError:
    mod2 = types.ModuleType("httpx")
    mod2.AsyncClient = type("AsyncClient", (), {"__init__": lambda *a, **k: None})
    mod2.HTTPError = Exception
    mod2.HTTPStatusError = Exception
    mod2.TimeoutException = Exception
    mod2.Timeout = lambda *a, **k: None
    sys.modules["httpx"] = mod2


from app.discovery.rss_autodiscovery import _LinkParser
from app.discovery.selector_generation import generate_xpath_candidates


# ── RSS link parsing ─────────────────────────────────────────────────────

def test_link_parser_rss():
    html = '''
    <html><head>
        <link rel="alternate" type="application/rss+xml" title="Blog RSS" href="/feed.xml" />
    </head><body></body></html>
    '''
    p = _LinkParser("https://example.com")
    p.feed(html)
    assert len(p.feeds) == 1
    assert p.feeds[0].url == "https://example.com/feed.xml"
    assert p.feeds[0].feed_type == "rss"
    assert p.feeds[0].title == "Blog RSS"


def test_link_parser_atom():
    html = '''
    <html><head>
        <link rel="alternate" type="application/atom+xml" href="https://example.com/atom" />
    </head><body></body></html>
    '''
    p = _LinkParser("https://example.com")
    p.feed(html)
    assert len(p.feeds) == 1
    assert p.feeds[0].feed_type == "atom"


def test_link_parser_multiple():
    html = '''
    <html><head>
        <link rel="alternate" type="application/rss+xml" href="/rss" />
        <link rel="alternate" type="application/atom+xml" href="/atom" />
        <link rel="stylesheet" href="/style.css" />
    </head><body></body></html>
    '''
    p = _LinkParser("https://example.com")
    p.feed(html)
    assert len(p.feeds) == 2


def test_link_parser_no_feeds():
    html = '<html><head><link rel="stylesheet" href="/s.css" /></head><body></body></html>'
    p = _LinkParser("https://example.com")
    p.feed(html)
    assert len(p.feeds) == 0


def test_link_parser_relative_url():
    html = '<html><head><link rel="alternate" type="application/rss+xml" href="blog/feed" /></head></html>'
    p = _LinkParser("https://example.com/pages/")
    p.feed(html)
    assert len(p.feeds) == 1
    assert p.feeds[0].url == "https://example.com/pages/blog/feed"


# ── XPath heuristic generation ───────────────────────────────────────────

def test_xpath_repeated_articles():
    items = ''.join(
        f'<article class="post-card"><h2><a href="/p/{i}">Post {i}</a></h2><p>Text</p></article>'
        for i in range(10)
    )
    html = f'<html><body><div class="feed">{items}</div></body></html>'
    candidates = generate_xpath_candidates(html)
    assert len(candidates) >= 1
    best = candidates[0]
    assert "article" in best.item_selector
    assert best.item_count >= 10
    assert best.confidence > 0.3


def test_xpath_repeated_list_items():
    items = ''.join(
        f'<li class="news-item"><h3><a href="/n/{i}">News {i}</a></h3></li>'
        for i in range(8)
    )
    html = f'<html><body><ul class="news-list">{items}</ul></body></html>'
    candidates = generate_xpath_candidates(html)
    assert len(candidates) >= 1
    assert "li" in candidates[0].item_selector


def test_xpath_no_repeats():
    html = '<html><body><p>Hello</p><div>World</div></body></html>'
    candidates = generate_xpath_candidates(html)
    assert len(candidates) == 0


def test_xpath_respects_min_repeats():
    """Only 2 items — below the threshold of 3."""
    items = '<article class="x"><h2>A</h2></article><article class="x"><h2>B</h2></article>'
    html = f'<html><body>{items}</body></html>'
    candidates = generate_xpath_candidates(html)
    assert len(candidates) == 0


# ── Runner ───────────────────────────────────────────────────────────────

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
