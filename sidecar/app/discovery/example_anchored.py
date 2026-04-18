"""Find an item container XPath by anchoring to a user-supplied example.

When a user pastes the text of one real item on a page, we can:
  1. locate that text in the rendered HTML,
  2. walk up the DOM looking for the lowest ancestor that has >= 2 siblings
     with the same tag (i.e. the repeating container),
  3. emit a contains()-based XPath selector for that ancestor.
"""
from __future__ import annotations

import re

from lxml import html as lxml_html


def _first_meaningful_class(el) -> str:
    from app.discovery.selector_generation import _meaningful_classes
    cls = (el.get("class") or "").strip()
    meaningful = _meaningful_classes(cls).split()
    return meaningful[0] if meaningful else (cls.split()[0] if cls else "")


def _xpath_for(el) -> str:
    cls = _first_meaningful_class(el)
    if cls:
        return f"//{el.tag}[contains(@class, '{cls}')]"
    if el.get("id"):
        return f"//{el.tag}[@id='{el.get('id')}']"
    if el.get("role"):
        return f"//{el.tag}[@role='{el.get('role')}']"
    return f"//{el.tag}"


def find_item_selectors_from_example(
    html_text: str,
    example: str,
    *,
    max_walk: int = 8,
) -> list[str]:
    """Return up to 3 candidate item-container XPaths for the given example.

    Strategy: find all elements whose descendant text contains *example*,
    pick the narrowest containing element per match, then walk up to an
    ancestor with >= 2 same-tag siblings. Dedup by selector; return in
    order of 'closest to the example first'.

    Matching is case-insensitive on whitespace-normalised text and uses
    the first 60 characters of the example.
    """
    if not html_text or not example:
        return []

    needle = " ".join(example.split()).lower()[:60]
    if not needle:
        return []

    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        return []

    matches = []
    for el in tree.iter():
        text = " ".join((el.text_content() or "").split()).lower()
        if needle in text:
            matches.append(el)
    if not matches:
        return []

    # For each match, prefer the deepest element that still contains the needle.
    deepest = []
    for m in matches:
        current = m
        changed = True
        while changed:
            changed = False
            for child in current:
                t = " ".join((child.text_content() or "").split()).lower()
                if needle in t:
                    current = child
                    changed = True
                    break
        deepest.append(current)

    # Walk upward looking for a repeating-sibling ancestor.
    candidates: list[str] = []
    seen: set[str] = set()
    for leaf in deepest:
        node = leaf
        for _ in range(max_walk):
            parent = node.getparent()
            if parent is None:
                break
            same_tag_siblings = [s for s in parent if s.tag == node.tag]
            if len(same_tag_siblings) >= 2:
                xp = _xpath_for(node)
                if xp not in seen:
                    seen.add(xp)
                    candidates.append(xp)
                break
            node = parent

    return candidates[:3]
