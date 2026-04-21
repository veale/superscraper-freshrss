"""Auto-anchor listing items by scanning for dates.

Dates are the least ambiguous cue for "this is a feed item" because titles,
links, and descriptions have too many false-positive homes (nav, related-posts,
author bios, footer credits). A page's item container is almost always the
smallest repeating DOM shape that holds a date *and* a link *and* some prose.

The strategy here:

1. Find every node that carries a date — either a text match against a battery
   of date formats, or a ``<time datetime=…>`` / ``[datetime]`` attribute.
2. For each dated node, walk up its ancestors until we find an ancestor whose
   parent has ≥3 siblings sharing the same (tag, primary-class) signature that
   also each contain a date. That ancestor is an item candidate.
3. Aggregate across all dated nodes; the most-repeated signature wins.
4. Validate surviving siblings: each must contain an ``<a href>`` AND ≥20
   characters of non-date text (guards against archive-date sidebars).
5. Emit an ``XPathCandidate`` with item/title/link/timestamp selectors.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from lxml import html as lxml_html

from app.discovery.selector_generation import _meaningful_classes
from app.models.schemas import XPathCandidate


_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b"),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"[a-z]*\.?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"),
    re.compile(r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b", re.IGNORECASE),
    re.compile(r"\b(?:yesterday|today)\b", re.IGNORECASE),
)


def _text_has_date(text: Optional[str]) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _DATE_PATTERNS)


def _element_is_dated(el) -> bool:
    """True if *el* itself (not descendants) carries a date signal."""
    if el.tag == "time" and el.get("datetime"):
        return True
    if el.get("datetime"):
        return True
    if _text_has_date(el.text):
        return True
    for child in el:
        if _text_has_date(child.tail):
            return True
    return False


def _subtree_contains_date(el) -> bool:
    for node in el.iter():
        if _element_is_dated(node):
            return True
    return False


def _sig(el) -> tuple[str, str]:
    """(tag, primary-meaningful-class). Empty class is allowed but weaker."""
    tag = el.tag if isinstance(el.tag, str) else ""
    cls = _meaningful_classes(el.get("class", "") or "")
    first = cls.split()[0] if cls else ""
    return (tag, first)


_DATE_WORDS_RE = re.compile(
    r"(?:"
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b|"
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b|"
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b|"
    r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b|"
    r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b|"
    r"\b(?:yesterday|today)\b"
    r")",
    re.IGNORECASE,
)


def _non_date_text(el) -> str:
    """All text content with date-like substrings stripped out."""
    text = " ".join(el.itertext())
    return _DATE_WORDS_RE.sub(" ", text)


def _is_valid_item(el) -> bool:
    has_link = bool(el.xpath(".//a[@href]"))
    if not has_link:
        return False
    prose = _non_date_text(el)
    return len(prose.strip()) >= 20


def _pick_item_ancestor(dated_el):
    """Walk up from *dated_el* until an ancestor has ≥3 same-sig dated siblings."""
    cur = dated_el
    while cur is not None and cur.getparent() is not None:
        parent = cur.getparent()
        cur_sig = _sig(cur)
        if cur_sig[0]:
            dated_siblings = sum(
                1 for sib in parent if _sig(sib) == cur_sig and _subtree_contains_date(sib)
            )
            if dated_siblings >= 3:
                return cur
        cur = parent
    return None


def _derive_timestamp_selector(exemplar) -> str:
    """Build a relative XPath pointing at the dated node inside *exemplar*."""
    time_nodes = exemplar.xpath(".//time[@datetime]")
    if time_nodes:
        return ".//time/@datetime"
    any_datetime = exemplar.xpath(".//*[@datetime]")
    if any_datetime:
        return ".//*/@datetime"

    for node in exemplar.iter():
        if node is exemplar:
            continue
        if not _element_is_dated(node):
            continue
        cls = _meaningful_classes(node.get("class", "") or "")
        first = cls.split()[0] if cls else ""
        tag = node.tag if isinstance(node.tag, str) else "*"
        if first:
            return f".//{tag}[contains(@class, '{first}')]"
        return f".//{tag}"
    return ""


def _derive_title_selector(exemplar) -> str:
    for tag in ("h1", "h2", "h3", "h4"):
        if exemplar.xpath(f".//{tag}"):
            return f".//{tag}"
    if exemplar.xpath(".//a[@href]"):
        return ".//a"
    return ""


def anchor_via_dates(html: str) -> Optional[XPathCandidate]:
    """Return an ``XPathCandidate`` anchored on repeated dated items, or None."""
    if not html:
        return None
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return None

    dated: list = []
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        if _element_is_dated(el):
            dated.append(el)

    if len(dated) < 3:
        return None

    item_candidates = []
    seen: set[int] = set()
    for d in dated:
        item = _pick_item_ancestor(d)
        if item is None:
            continue
        key = id(item)
        if key in seen:
            continue
        seen.add(key)
        item_candidates.append(item)

    if len(item_candidates) < 3:
        return None

    sig_counter: Counter[tuple[str, str]] = Counter()
    exemplars: dict[tuple[str, str], object] = {}
    for it in item_candidates:
        s = _sig(it)
        if not s[0]:
            continue
        sig_counter[s] += 1
        exemplars.setdefault(s, it)

    if not sig_counter:
        return None

    best_sig, _ = sig_counter.most_common(1)[0]
    exemplar = exemplars[best_sig]
    parent = exemplar.getparent()
    if parent is None:
        return None

    validated = [sib for sib in parent if _sig(sib) == best_sig and _is_valid_item(sib)]
    if len(validated) < 3:
        return None

    tag, first_cls = best_sig
    if first_cls:
        item_selector = f"//{tag}[contains(@class, '{first_cls}')]"
    else:
        item_selector = f"//{tag}"

    timestamp_selector = _derive_timestamp_selector(exemplar)
    title_selector = _derive_title_selector(exemplar)
    link_selector = ".//a/@href" if exemplar.xpath(".//a[@href]") else ""

    confidence = min(0.9, 0.55 + 0.05 * len(validated))
    return XPathCandidate(
        item_selector=item_selector,
        title_selector=title_selector,
        link_selector=link_selector,
        timestamp_selector=timestamp_selector,
        confidence=confidence,
        item_count=len(validated),
    )
