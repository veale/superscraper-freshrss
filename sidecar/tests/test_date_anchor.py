"""Tests for app.discovery.date_anchor — heuristic item anchoring via dates."""

from app.discovery.date_anchor import anchor_via_dates


def _make_list(item_tag: str, item_cls: str, n: int, dates: list[str]) -> str:
    parts = [f"<ul><!-- wrapper -->"]
    for i, d in enumerate(dates[:n]):
        parts.append(
            f'<{item_tag} class="{item_cls}">'
            f'<a href="/p/{i}"><h3>Article {i} title long enough</h3></a>'
            f"<p>Prose body {i} with substantial descriptive text.</p>"
            f'<span class="entry-date">{d}</span>'
            f"</{item_tag}>"
        )
    parts.append("</ul>")
    return "<html><body>" + "".join(parts) + "</body></html>"


def test_anchor_picks_repeating_dated_items():
    html = _make_list(
        "li", "post", 4,
        ["January 8, 2026", "2025-12-01", "Nov 15, 2025", "3 days ago"],
    )
    c = anchor_via_dates(html)
    assert c is not None
    assert "post" in c.item_selector
    assert c.item_count == 4
    assert c.timestamp_selector != ""
    assert c.link_selector == ".//a/@href"


def test_anchor_rejects_archive_sidebar_no_prose():
    html = """<html><body>
    <aside><ul>
      <li class="arc"><a href="/y/2024">2024</a></li>
      <li class="arc"><a href="/y/2023">2023</a></li>
      <li class="arc"><a href="/y/2022">2022</a></li>
      <li class="arc"><a href="/y/2021">2021</a></li>
    </ul></aside>
    </body></html>"""
    assert anchor_via_dates(html) is None


def test_anchor_prefers_articles_over_archive():
    html = """<html><body>
    <aside><ul>
      <li class="arc"><a href="/y/2024">2024</a></li>
      <li class="arc"><a href="/y/2023">2023</a></li>
      <li class="arc"><a href="/y/2022">2022</a></li>
    </ul></aside>
    <main>
      <article class="post"><a href="/p/1"><h2>Post one substantial title</h2></a>
        <p>Body of post one — meaningful prose content.</p>
        <time datetime="2026-01-08">Jan 8, 2026</time></article>
      <article class="post"><a href="/p/2"><h2>Post two substantial title</h2></a>
        <p>Body of post two — meaningful prose content.</p>
        <time datetime="2025-12-01">Dec 1, 2025</time></article>
      <article class="post"><a href="/p/3"><h2>Post three substantial title</h2></a>
        <p>Body of post three — meaningful prose content.</p>
        <time datetime="2025-11-15">Nov 15, 2025</time></article>
    </main></body></html>"""
    c = anchor_via_dates(html)
    assert c is not None
    assert "article" in c.item_selector
    assert c.timestamp_selector == ".//time/@datetime"


def test_anchor_returns_none_when_no_dates():
    html = "<html><body><div>hello world</div><div>another</div></body></html>"
    assert anchor_via_dates(html) is None


def test_anchor_recovers_hrw_shape():
    """Mimics the HRW media-list shape that motivated this heuristic."""
    items = "".join(
        f'<li class="media-list__item">'
        f'<a href="/report/2026/0{i}/slug-{i}"><span>Report {i} title</span></a>'
        f'<p class="media-block__subtitle">Subtitle with real prose {i} here.</p>'
        f'<span class="media-block__date">March {i}, 2026</span>'
        f"</li>"
        for i in range(1, 6)
    )
    html = f"<html><body><ul>{items}</ul></body></html>"
    c = anchor_via_dates(html)
    assert c is not None
    assert "media-list__item" in c.item_selector
    assert c.item_count == 5
    assert "media-block__date" in c.timestamp_selector
