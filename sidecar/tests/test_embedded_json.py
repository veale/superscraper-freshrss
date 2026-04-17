"""Tests for the embedded JSON detection module."""

import sys
import os
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Shim pydantic if not installed.
try:
    import pydantic
except ImportError:
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

from app.discovery.embedded_json import detect_embedded_json


def test_next_data():
    html = '''
    <html><head></head><body>
    <script id="__NEXT_DATA__" type="application/json">
    {
        "props": {
            "pageProps": {
                "articles": [
                    {"title": "First Post", "slug": "first", "date": "2026-04-01"},
                    {"title": "Second Post", "slug": "second", "date": "2026-04-02"},
                    {"title": "Third Post", "slug": "third", "date": "2026-04-03"},
                    {"title": "Fourth Post", "slug": "fourth", "date": "2026-04-04"},
                    {"title": "Fifth Post", "slug": "fifth", "date": "2026-04-05"}
                ]
            }
        }
    }
    </script>
    </body></html>
    '''
    results = detect_embedded_json(html)
    assert len(results) >= 1
    best = results[0]
    assert best.source == "script#__NEXT_DATA__"
    assert "articles" in best.path
    assert best.item_count == 5
    assert best.feed_score > 0.3


def test_nuxt_data():
    html = '''
    <html><body>
    <script>
    window.__NUXT__ = {
        "data": [{
            "posts": [
                {"title": "A", "url": "/a", "published": "2026-01-01"},
                {"title": "B", "url": "/b", "published": "2026-01-02"},
                {"title": "C", "url": "/c", "published": "2026-01-03"}
            ]
        }]
    };
    </script>
    </body></html>
    '''
    results = detect_embedded_json(html)
    assert len(results) >= 1


def test_no_json():
    html = '<html><body><p>Hello world</p></body></html>'
    results = detect_embedded_json(html)
    assert results == []


def test_application_json_script():
    html = '''
    <html><body>
    <script type="application/json">
    [
        {"title": "Event A", "date": "2026-05-01", "link": "/events/a"},
        {"title": "Event B", "date": "2026-05-02", "link": "/events/b"},
        {"title": "Event C", "date": "2026-05-03", "link": "/events/c"},
        {"title": "Event D", "date": "2026-05-04", "link": "/events/d"},
        {"title": "Event E", "date": "2026-05-05", "link": "/events/e"}
    ]
    </script>
    </body></html>
    '''
    results = detect_embedded_json(html)
    assert len(results) >= 1
    assert results[0].item_count == 5


def test_ld_json_ignored_if_no_feed():
    """LD+JSON with schema.org markup shouldn't score highly as a feed."""
    html = '''
    <html><body>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Organization", "name": "Acme"}
    </script>
    </body></html>
    '''
    results = detect_embedded_json(html)
    # A single object (not an array) should not produce results.
    assert len(results) == 0


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
