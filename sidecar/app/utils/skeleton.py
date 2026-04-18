"""HTML skeleton builder — strips noise, preserves structure for LLM prompts."""
from __future__ import annotations

from lxml import etree
from lxml import html as lxml_html

from app.utils.tree_pruning import prune_tree

_KEEP_ATTRS = frozenset({"class", "id", "role", "itemprop", "data-testid"})


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


def _collapse_text(el) -> None:
    if el.text and el.text.strip():
        words = len(el.text.split())
        el.text = f"[text:{words}]"
    elif el.text:
        el.text = None
    if el.tail and el.tail.strip():
        words = len(el.tail.split())
        el.tail = f"[text:{words}]"
    elif el.tail:
        el.tail = None


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

    needle = " ".join(anchor_text.split())[:60].lower()
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
        t = " ".join((el.text_content() or "").split()).lower()
        if needle in t:
            target = el
            changed = True
            while changed:
                changed = False
                for child in target:
                    tc = " ".join((child.text_content() or "").split()).lower()
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
