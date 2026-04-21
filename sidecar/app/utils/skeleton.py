"""HTML skeleton builder — strips noise, preserves structure for LLM prompts."""
from __future__ import annotations

from lxml import etree
from lxml import html as lxml_html

from collections import Counter

from app.scraping.rule_builder import normalize_for_match
from app.utils.tree_pruning import prune_tree

_KEEP_ATTRS = frozenset({"class", "id", "role", "itemprop", "data-testid"})

# Preserve text inside these tags — they're usually titles / links and let the
# LLM map class names to actual content. Without this, the skeleton collapses
# every string to [text:N] and the model has to guess from structure alone.
_KEEP_TEXT_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "a", "time", "title"})
# Inline wrappers that should inherit "keep text" from a nearby keep-tag ancestor.
# Real-world markup often buries title text: <a><span>Title</span></a> or
# <h3><a><span>Title</span></a></h3>. Without this, the skeleton collapses the
# title to [text:N] and the LLM has nothing to match the user's example against.
_INLINE_WRAPPER_TAGS = frozenset({"span", "strong", "em", "b", "i"})
_KEEP_TEXT_MAX_CHARS = 140


def build_skeleton(raw_html: str, max_chars: int = 12_000) -> str:
    """Return a stripped DOM skeleton suitable for LLM consumption."""
    if not raw_html:
        return ""
    try:
        doc = lxml_html.document_fromstring(raw_html)
    except Exception:
        return raw_html[:max_chars]

    prune_tree(
        doc,
        drop_comments=True,
        drop_precision=False,
        drop_structural_noise=False,  # keep <header>/<footer>/<aside> for LLM context
    )
    for comment in doc.xpath('//comment()'):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)

    _process_tree(doc)

    result = etree.tostring(doc, encoding="unicode", method="html")
    return result[:max_chars]


def _process_tree(root) -> None:
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        _strip_attrs(el)
        _collapse_text(el)


def _strip_attrs(el) -> None:
    keep: dict[str, str] = {}
    for attr, val in list(el.attrib.items()):
        local = attr.split("}")[-1] if "}" in attr else attr
        if local == "href":
            keep[local] = val[:60]
        elif local in _KEEP_ATTRS or local.startswith("aria-"):
            keep[local] = val
    el.attrib.clear()
    el.attrib.update(keep)


def _should_keep_text(el) -> bool:
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in _KEEP_TEXT_TAGS:
        return True
    if tag not in _INLINE_WRAPPER_TAGS:
        return False
    parent = el.getparent()
    hops = 0
    while parent is not None and hops < 3:
        ptag = parent.tag if isinstance(parent.tag, str) else ""
        if ptag in _KEEP_TEXT_TAGS:
            return True
        if ptag not in _INLINE_WRAPPER_TAGS:
            return False
        parent = parent.getparent()
        hops += 1
    return False


def _collapse_text(el) -> None:
    keep = _should_keep_text(el)
    if el.text and el.text.strip():
        text = el.text.strip()
        if keep and len(text) <= _KEEP_TEXT_MAX_CHARS:
            el.text = text
        else:
            words = len(el.text.split())
            el.text = f"[text:{words}]"
    elif el.text:
        el.text = None
    if el.tail and el.tail.strip():
        words = len(el.tail.split())
        el.tail = f"[text:{words}]"
    elif el.tail:
        el.tail = None


def build_class_inventory(
    raw_html: str,
    *,
    min_count: int = 3,
    max_entries: int = 30,
) -> str:
    """Produce a compact `tag.class × count` listing of repeating elements.

    Given only a collapsed skeleton, an LLM can't tell whether `c-listing__item`
    is articles or taxonomy chips. A tag.class histogram sorted by count, with
    semantic tags (article/section/li) highlighted, lets the model pick real
    item containers without seeing body text. Cheap to compute (~5ms on a
    100KB page) and small in the prompt (<1KB).
    """
    if not raw_html:
        return ""
    try:
        doc = lxml_html.document_fromstring(raw_html)
    except Exception:
        return ""

    # Drop scripts/styles so their class attrs don't pollute.
    for tag in ("script", "style", "noscript", "svg"):
        for el in list(doc.iter(tag)):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    # Suppress utility-class noise (Tailwind etc.) using the same rule as
    # selector generation.
    try:
        from app.discovery.selector_generation import _meaningful_classes
    except Exception:
        def _meaningful_classes(c: str) -> str:  # type: ignore[misc]
            return c

    counter: Counter[tuple[str, str]] = Counter()
    for el in doc.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if not tag:
            continue
        cls = (el.get("class") or "").strip()
        if not cls:
            continue
        kept = _meaningful_classes(cls).split()
        if not kept:
            continue
        for c in kept[:3]:
            counter[(tag, c)] += 1

    semantic_tags = {"article", "section", "li", "div", "a", "h1", "h2", "h3"}
    entries: list[tuple[str, str, int]] = [
        (tag, cls, n)
        for (tag, cls), n in counter.items()
        if n >= min_count
    ]
    # Rank semantic containers first, then by count.
    entries.sort(key=lambda e: (0 if e[0] in semantic_tags else 1, -e[2]))
    entries = entries[:max_entries]

    lines = [f"  {tag}.{cls} × {n}" for tag, cls, n in entries]
    return "\n".join(lines)


def build_anchored_snippet(
    raw_html: str,
    anchor_text: str,
    *,
    max_chars: int = 4_000,
    context_ancestors: int = 4,
) -> str:
    """Return an HTML snippet containing *anchor_text* with text preserved.

    Used for LLM refinement prompts — the model needs to see where a specific
    piece of text lives in the DOM, which the collapsed skeleton can't show.
    Attributes are pruned the same way as build_skeleton, but text is kept.
    Returns "" if the anchor can't be found.
    """
    if not raw_html or not anchor_text:
        return ""

    needle = normalize_for_match(anchor_text)
    if not needle:
        return ""

    try:
        doc = lxml_html.document_fromstring(raw_html)
    except Exception:
        return ""

    for tag in ("script", "style", "noscript", "svg"):
        for el in doc.iter(tag):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    target = None
    for el in doc.iter():
        t = normalize_for_match(el.text_content() or "")
        if needle in t:
            target = el
            changed = True
            while changed:
                changed = False
                for child in target:
                    tc = normalize_for_match(child.text_content() or "")
                    if needle in tc:
                        target = child
                        changed = True
                        break
    if target is None:
        return ""

    root = target
    for _ in range(context_ancestors):
        parent = root.getparent()
        if parent is None:
            break
        root = parent

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        _strip_attrs(el)

    snippet = etree.tostring(root, encoding="unicode", method="html")
    if len(snippet) <= max_chars:
        return snippet

    lower_snip = snippet.lower()
    idx = lower_snip.find(needle)
    if idx < 0:
        return snippet[:max_chars]
    half = max_chars // 2
    start = max(0, idx - half)
    return snippet[start : start + max_chars]
