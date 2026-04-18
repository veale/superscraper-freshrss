"""Unit tests for the HTML skeleton builder."""
from __future__ import annotations

import sys
import os


from app.utils.skeleton import build_skeleton


def test_scripts_stripped():
    html = "<html><body><script>alert(1)</script><p>hello world</p></body></html>"
    result = build_skeleton(html)
    assert "<script" not in result
    assert "alert" not in result


def test_style_stripped():
    html = "<html><head><style>body{color:red}</style></head><body><p>hi</p></body></html>"
    result = build_skeleton(html)
    assert "<style" not in result
    assert "color:red" not in result


def test_noscript_stripped():
    html = "<html><body><noscript>Enable JS</noscript><p>content</p></body></html>"
    result = build_skeleton(html)
    assert "<noscript" not in result


def test_svg_stripped():
    html = "<html><body><svg><path d='M0 0'/></svg><p>text</p></body></html>"
    result = build_skeleton(html)
    assert "<svg" not in result
    assert "<path" not in result


def test_comments_stripped():
    html = "<html><body><!-- secret --><p>visible</p></body></html>"
    result = build_skeleton(html)
    assert "secret" not in result
    assert "<!--" not in result


def test_id_and_class_preserved():
    html = '<html><body><div id="main" class="container"><p>text</p></div></body></html>'
    result = build_skeleton(html)
    assert 'id="main"' in result
    assert 'class="container"' in result


def test_aria_attrs_preserved():
    html = '<html><body><nav aria-label="primary"><a href="/home">Home</a></nav></body></html>'
    result = build_skeleton(html)
    assert 'aria-label="primary"' in result


def test_href_preserved_and_truncated():
    long_url = "https://example.com/" + "x" * 100
    html = f'<html><body><a href="{long_url}">link</a></body></html>'
    result = build_skeleton(html)
    assert "href=" in result
    href_start = result.index('href="') + 6
    href_end = result.index('"', href_start)
    assert href_end - href_start <= 60


def test_irrelevant_attrs_stripped():
    html = '<html><body><div style="color:red" onclick="bad()" data-v-12345="x" class="keep">text</div></body></html>'
    result = build_skeleton(html)
    assert "style=" not in result
    assert "onclick=" not in result
    assert "data-v-12345" not in result
    assert 'class="keep"' in result


def test_text_collapsed_to_word_count():
    html = "<html><body><p>one two three four five</p></body></html>"
    result = build_skeleton(html)
    assert "[text:5]" in result
    assert "one two three" not in result


def test_max_chars_cap():
    html = "<html><body>" + "<p>hello world</p>" * 5000 + "</body></html>"
    result = build_skeleton(html, max_chars=500)
    assert len(result) <= 500


def test_empty_html_returns_empty():
    assert build_skeleton("") == ""


def test_data_testid_preserved():
    html = '<html><body><div data-testid="article-list"><p>item</p></div></body></html>'
    result = build_skeleton(html)
    assert 'data-testid="article-list"' in result


def test_role_preserved():
    html = '<html><body><main role="main"><p>content</p></main></body></html>'
    result = build_skeleton(html)
    assert 'role="main"' in result


def test_itemprop_preserved():
    html = '<html><body><span itemprop="name">Article Title</span></body></html>'
    result = build_skeleton(html)
    assert 'itemprop="name"' in result


def test_structure_preserved():
    html = """
    <html><body>
      <header><nav><a href="/about">About</a></nav></header>
      <main><article><h1>Title</h1><p>Body text here.</p></article></main>
      <footer><p>Footer</p></footer>
    </body></html>
    """
    result = build_skeleton(html)
    assert "<header>" in result or "<header" in result
    assert "<main>" in result or "<main" in result
    assert "<article>" in result or "<article" in result
    assert "<footer>" in result or "<footer" in result
