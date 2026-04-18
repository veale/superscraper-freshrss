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
