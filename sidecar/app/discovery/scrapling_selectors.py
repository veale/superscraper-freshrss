"""Step 5 (Phase 2) — Scrapling-powered selector generation.

Replaces/augments the heuristic selector_generation.py with Scrapling's
proper HTML parser.  Uses Selector(html) only — no browser, sync, safe for
use inside an async FastAPI handler.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from scrapling import Selector

from app.models.schemas import XPathCandidate

# Tags we treat as potential feed-item containers.
_ITEM_TAGS = ["article", "li", "div", "section", "tr"]

# Minimum repetitions before we consider a pattern.
_MIN_REPEATS = 3

# Tags/roles we want to score down (nav / footer / sidebar).
_LOW_VALUE_ANCESTORS = {"nav", "footer", "aside", "header"}

# Signals that a repeated element is a feed item.
_FEED_ITEM_CLASSES = re.compile(
    r"post|article|item|entry|card|story|result|listing|news|blog|feed",
    re.IGNORECASE,
)


def _has_low_value_ancestor(element) -> bool:
    """Return True if the element lives inside nav/footer/aside."""
    try:
        for ancestor in element.iterancestors():
            if ancestor.tag in _LOW_VALUE_ANCESTORS:
                return True
    except Exception:
        pass
    return False


def _item_confidence(element, count: int) -> float:
    """Compute a confidence score for *element* as a feed-item container."""
    score = min(0.3 + (count - _MIN_REPEATS) * 0.03, 0.70)

    cls = element.attrib.get("class", "")
    if _FEED_ITEM_CLASSES.search(cls):
        score = min(score + 0.15, 0.90)

    # Bonus for semantic tags.
    if element.tag == "article":
        score = min(score + 0.10, 0.95)

    # Penalty for living in nav/footer/aside.
    if _has_low_value_ancestor(element):
        score = max(score - 0.25, 0.05)

    return round(score, 2)


def _guess_sub_selectors(element) -> dict[str, str]:
    """Return XPath sub-selectors for title/link/content/time/img."""
    out: dict[str, str] = {
        "title": "",
        "link": "descendant::a[string-length(@href)>0]/@href",
        "content": ".",
        "timestamp": "descendant::time/@datetime",
        "thumbnail": "descendant::img/@src",
    }

    # Find the best heading inside this element.
    for heading in ("h2", "h3", "h4", "h1"):
        try:
            found = element.css(heading)
            if found:
                out["title"] = f"descendant::{heading}"
                break
        except Exception:
            pass

    if not out["title"]:
        # Fall back to first anchor text.
        out["title"] = "descendant::a"

    return out


def generate_selectors_with_scrapling(html: str) -> list[XPathCandidate]:
    """Use Scrapling's parser to identify feed-like elements and generate
    XPath selectors for them."""

    if not html or len(html) < 100:
        return []

    try:
        sel = Selector(html)
    except Exception:
        return []

    candidates: list[XPathCandidate] = []
    seen_xpaths: set[str] = set()

    for tag in _ITEM_TAGS:
        try:
            elements = sel.find_all(tag)
        except Exception:
            continue

        if not elements:
            continue

        # Group by CSS class signature to find repeated patterns.
        class_groups: dict[str, list[Any]] = defaultdict(list)
        for el in elements:
            cls = " ".join(sorted(el.attrib.get("class", "").split()))
            key = f"{tag}|{cls}"
            class_groups[key].append(el)

        for key, group in class_groups.items():
            if len(group) < _MIN_REPEATS:
                continue

            first = group[0]
            if _has_low_value_ancestor(first):
                continue

            try:
                # Use Scrapling's generated XPath as the selector.
                item_xpath = first.generate_full_xpath_selector
            except Exception:
                continue

            if not item_xpath or item_xpath in seen_xpaths:
                continue

            # Strip the positional [N] suffix so the XPath matches all items.
            # e.g. //body/main/article[1] → //body/main/article
            item_xpath_clean = re.sub(r"\[\d+\]", "", item_xpath)
            if item_xpath_clean in seen_xpaths:
                continue
            seen_xpaths.add(item_xpath_clean)

            sub = _guess_sub_selectors(first)
            confidence = _item_confidence(first, len(group))

            candidates.append(
                XPathCandidate(
                    item_selector=item_xpath_clean,
                    title_selector=sub["title"],
                    link_selector=sub["link"],
                    content_selector=sub["content"],
                    timestamp_selector=sub["timestamp"],
                    thumbnail_selector=sub["thumbnail"],
                    confidence=confidence,
                    item_count=len(group),
                )
            )

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates[:5]
