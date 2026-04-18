"""Step 5 (Phase 1) — Heuristic XPath / CSS selector generation.

In Phase 1 this is a pure-Python heuristic that looks for repeated
structural patterns in the HTML.  Phase 2 replaces this with Scrapling's
adaptive auto-selector generation.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser
from typing import Optional

from app.discovery.node_scoring import node_score
from app.models.schemas import XPathCandidate

# Tags that typically wrap individual feed items.
_ITEM_TAGS = {"article", "li", "div", "section", "tr"}

# Attributes whose values commonly distinguish item containers.
_ROLE_ATTRS = {"class", "role", "data-testid", "itemtype"}

# Tags/selectors that typically hold a title inside an item.
_TITLE_TAGS = {"h1", "h2", "h3", "h4", "a"}

# Minimum repetitions to consider a pattern.
_MIN_REPEATS = 3

# ── Utility-class stripping ───────────────────────────────────────────────────

_UTILITY_PREFIXES = (
    "w-", "h-", "m-", "p-", "mx-", "my-", "px-", "py-", "mt-", "mb-", "ml-", "mr-",
    "pt-", "pb-", "pl-", "pr-", "text-", "bg-", "border-", "flex-",
    "grid-cols-", "grid-rows-", "grid-flow-",  # specific Tailwind grid utilities only
    "items-", "justify-", "gap-", "space-", "rounded-", "shadow-", "opacity-",
    "z-", "top-", "left-", "right-", "bottom-", "max-", "min-", "overflow-",
    "leading-", "tracking-", "font-", "col-span-", "col-start-", "row-span-",
    "order-", "grow-", "shrink-",
    "aspect-", "object-", "inline-",
)

_UTILITY_VARIANT_RE = re.compile(
    r"^(sm|md|lg|xl|2xl|3xl|hover|focus|first|last|active|disabled|dark|group-hover):"
)


def _is_utility_class(cls: str) -> bool:
    if not cls:
        return True
    m = _UTILITY_VARIANT_RE.match(cls)
    if m:
        return _is_utility_class(cls[m.end():])
    if cls in {"flex", "grid", "hidden", "block", "inline", "relative", "absolute",
               "fixed", "sticky", "static"}:
        return True
    return any(cls.startswith(p) for p in _UTILITY_PREFIXES)


def _meaningful_classes(class_attr: str) -> str:
    """Return only non-utility class tokens, preserving order."""
    if not class_attr:
        return ""
    kept = [c for c in class_attr.split() if not _is_utility_class(c)]
    return " ".join(kept)


class _StructureParser(HTMLParser):
    """Build a simplified picture of repeated DOM patterns."""

    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        # (parent_signature, child_tag, child_attrs_key) → count
        self.child_counts: Counter[tuple[str, str, str]] = Counter()
        # Track tags at current depth for parent context.
        self._stack: list[str] = []
        # Full list of (tag, attrs_key, depth) for later XPath generation.
        self.elements: list[tuple[str, dict[str, str], int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_dict = {k: (v or "") for k, v in attrs}
        sig = _signature(tag, attr_dict)
        parent_sig = self._stack[-1] if self._stack else ""
        self.child_counts[(parent_sig, tag, sig)] += 1
        self._stack.append(sig)
        self.elements.append((tag, attr_dict, len(self._stack)))

    def handle_endtag(self, tag: str) -> None:
        if self._stack:
            self._stack.pop()


def _signature(tag: str, attrs: dict[str, str]) -> str:
    """Create a hashable signature for a tag + its key attributes."""
    parts = [tag]
    for a in sorted(_ROLE_ATTRS):
        v = attrs.get(a, "")
        if a == "class":
            v = _meaningful_classes(v)
        if v:
            parts.append(f"{a}={v}")
    return "|".join(parts)


def _attrs_to_xpath_predicate(attrs: dict[str, str]) -> str:
    """Turn relevant attrs into an XPath predicate like [@class='foo']."""
    cls = attrs.get("class", "").strip()
    role = attrs.get("role", "").strip()
    testid = attrs.get("data-testid", "").strip()

    if cls:
        meaningful = _meaningful_classes(cls)
        first_cls = (meaningful.split()[0] if meaningful else None) or cls.split()[0]
        return f"[contains(@class, '{first_cls}')]"
    if role:
        return f"[@role='{role}']"
    if testid:
        return f"[@data-testid='{testid}']"
    return ""


def generate_xpath_candidates(html: str) -> list[XPathCandidate]:
    """Heuristically find repeated item-like DOM patterns and propose
    XPath selectors for them."""

    parser = _StructureParser()
    try:
        parser.feed(html)
    except Exception:
        return []

    candidates: list[XPathCandidate] = []

    # Find repeated children of the same parent.
    for (parent_sig, child_tag, child_sig), count in parser.child_counts.most_common(50):
        if count < _MIN_REPEATS:
            break
        if child_tag not in _ITEM_TAGS:
            continue

        # Build the XPath for the item.
        # We need the attrs from the child_sig.
        child_attrs = _parse_sig(child_sig)
        pred = _attrs_to_xpath_predicate(child_attrs)
        item_xpath = f"//{child_tag}{pred}"

        # Guess sub-selectors for common fields.
        title_sel = _guess_title_selector(html, item_xpath, child_tag, child_attrs)
        link_sel = _guess_link_selector()

        raw_score, unlikely = node_score(
            child_tag,
            child_attrs.get("class", ""),
            child_attrs.get("id", ""),
            child_attrs.get("role", ""),
        )
        if unlikely:
            confidence = 0.05
        else:
            base = min(0.3 + (count - _MIN_REPEATS) * 0.03, 0.85)
            normalised = max(-0.5, min(0.5, raw_score / 100.0))
            confidence = round(max(0.05, min(0.95, base + normalised * 0.5)), 2)

        candidates.append(XPathCandidate(
            item_selector=item_xpath,
            title_selector=title_sel,
            link_selector=link_sel,
            content_selector=".",
            timestamp_selector="descendant::time/@datetime",
            thumbnail_selector="descendant::img/@src",
            confidence=round(confidence, 2),
            item_count=count,
        ))

    # Deduplicate by item_selector.
    seen: set[str] = set()
    deduped: list[XPathCandidate] = []
    for c in candidates:
        if c.item_selector not in seen:
            seen.add(c.item_selector)
            deduped.append(c)

    # ── Tier 1.3.a: union-selector pass ──────────────────────────────────────
    # Emit union candidates for pairs whose individual class predicates differ.
    # Cross-tag unions (li | article) are allowed when both tags are item tags.
    import re as _re
    _PAT = _re.compile(r"^//(\w+)\[contains\(@class, '([^']+)'\)\]$")
    union_candidates: list[XPathCandidate] = []
    for i in range(len(deduped)):
        for j in range(i + 1, len(deduped)):
            a, b = deduped[i], deduped[j]
            ma = _PAT.match(a.item_selector)
            mb = _PAT.match(b.item_selector)
            if not (ma and mb):
                continue
            if ma.group(1) not in _ITEM_TAGS or mb.group(1) not in _ITEM_TAGS:
                continue
            if ma.group(2) == mb.group(2):
                continue
            combined = a.item_count + b.item_count
            if not (5 <= combined <= 50):
                continue
            best = a if a.confidence >= b.confidence else b
            union_candidates.append(XPathCandidate(
                item_selector=f"{a.item_selector} | {b.item_selector}",
                title_selector=best.title_selector,
                link_selector=best.link_selector,
                content_selector=best.content_selector,
                timestamp_selector=best.timestamp_selector,
                thumbnail_selector=best.thumbnail_selector,
                confidence=round(max(0.05, best.confidence - 0.05), 2),
                item_count=combined,
                item_selector_union=True,
            ))
    deduped.extend(union_candidates)

    deduped.sort(key=lambda c: c.confidence, reverse=True)
    return deduped[:7]  # top 7 — leaves room for union pass output


def _parse_sig(sig: str) -> dict[str, str]:
    """Reverse a signature string back into an attrs dict."""
    parts = sig.split("|")
    attrs: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            attrs[k] = v
    return attrs


def _guess_title_selector(
    html: str, item_xpath: str, item_tag: str, item_attrs: dict[str, str]
) -> str:
    """Guess a descendant XPath for the title within an item."""
    # Common patterns: h2 > a, h3, h2, a with title-like class.
    for heading in ("h2", "h3", "h4", "h1"):
        if f"<{heading}" in html.lower():
            return f"descendant::{heading}"
    return "descendant::a"


def _guess_link_selector() -> str:
    return "descendant::a[string-length(@href)>0]/@href"
