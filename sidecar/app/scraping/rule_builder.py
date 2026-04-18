"""AutoScraper-style rule builder — recover a selector from one example text.

When Phase 4's adaptive scrape returns zero items, the persisted config is
probably stale (site redesigned, class names changed). Given the text of
one known-good item from the previous run, this module walks up from every
matching DOM node building a (tag, valid_attrs, sibling_index) stack, then
picks the stack that yields the most repeatable siblings on the current page.

Port of autoscraper/auto_scraper.py::_build_stack (Alireza Mika).
"""
from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from lxml.html import HtmlElement


# Attributes we keep when building a stack — matches AutoScraper's default.
# `style` is kept because inline styles sometimes are the only discriminator
# on CSS-modules sites (__next-... / css-abc123).
_KEY_ATTRS = frozenset({"class", "style"})


# ── Text matching ────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """NFKD normalisation + strip. Matches autoscraper/utils.py::normalize."""
    return unicodedata.normalize("NFKD", text.strip())


def text_match(a: str, b: str, ratio_limit: float = 1.0) -> bool:
    """Fuzzy string equality.

    ratio_limit=1.0 is exact equality. Lower values use SequenceMatcher's
    ratio — 0.9 tolerates tiny edits (trailing punctuation changes,
    whitespace differences), 0.7 is very loose. Default matches AutoScraper.
    """
    a, b = _normalize(a), _normalize(b)
    if ratio_limit >= 1:
        return a == b
    return SequenceMatcher(None, a, b).ratio() >= ratio_limit


# ── Stack construction ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StackFrame:
    tag: str
    attrs: tuple[tuple[str, str], ...]   # sorted tuple for hashability
    sibling_index: int                    # index among same-tag+attrs siblings
                                         # under the same parent (0 = root)


@dataclass
class SelectorStack:
    frames: tuple[StackFrame, ...]
    hash: str
    xpath: str                            # regenerated from frames for use by Scrapling
    sibling_count: int                    # how many elements this stack matches on the page


def _valid_attrs(el: HtmlElement) -> dict[str, str]:
    """Subset of attrs AutoScraper considers 'distinguishing'. Matches
    autoscraper/auto_scraper.py::_get_valid_attrs exactly."""
    out: dict[str, str] = {}
    for attr in _KEY_ATTRS:
        out[attr] = el.attrib.get(attr, "")
    return out


def _attrs_as_tuple(attrs: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, v) for k, v in attrs.items() if v))


def build_stack(leaf: HtmlElement) -> SelectorStack:
    """Walk up from *leaf* to the root, building a (tag, attrs, index) stack.

    Port of autoscraper/auto_scraper.py::_build_stack. The sibling_index at
    each level lets the stack survive sites that add/remove items between
    runs: we identify "the 3rd <article class='post'> under <main>", not
    "the one at exactly this XPath position".
    """
    frames: list[StackFrame] = []
    current = leaf
    current_attrs = _valid_attrs(current)
    # Leaf has no sibling index — it's the thing we're picking out.
    frames.append(StackFrame(current.tag, _attrs_as_tuple(current_attrs), 0))

    while True:
        parent = current.getparent()
        if parent is None:
            break

        child_attrs = _valid_attrs(current)
        siblings = [
            s for s in parent.iterchildren(current.tag)
            if _valid_attrs(s) == child_attrs
        ]
        try:
            idx = siblings.index(current)
        except ValueError:
            idx = 0   # shouldn't happen, defensive

        parent_attrs = _valid_attrs(parent)
        frames.insert(0, StackFrame(parent.tag, _attrs_as_tuple(parent_attrs), idx))

        if parent.getparent() is None:
            break
        current = parent

    xpath = _frames_to_xpath(frames)
    h = hashlib.sha256(
        "/".join(f"{f.tag}:{f.attrs}:{f.sibling_index}" for f in frames).encode()
    ).hexdigest()[:16]

    return SelectorStack(frames=tuple(frames), hash=h, xpath=xpath, sibling_count=0)


def _frames_to_xpath(frames: tuple[StackFrame, ...] | list[StackFrame]) -> str:
    """Convert a stack into an XPath that selects all matching siblings.

    The leaf frame's sibling_index is intentionally dropped — we want to
    match *every* sibling that fits the pattern, which is how the stack
    generalises from one known item to a feed.
    """
    if not frames:
        return ""
    parts: list[str] = []
    for f in frames:
        predicates: list[str] = []
        for attr, val in f.attrs:
            if attr == "class" and val:
                first = val.split()[0]
                predicates.append(f"contains(@class, {first!r})")
            elif val:
                predicates.append(f"@{attr}={val!r}")
        pred = "[" + " and ".join(predicates) + "]" if predicates else ""
        parts.append(f"{f.tag}{pred}")
    return "//" + "/".join(parts)


# ── High-level recovery entry point ──────────────────────────────────────────

def recover_selector(
    html: str,
    example_text: str,
    ratio_limit: float = 0.9,
    max_stacks: int = 10,
) -> SelectorStack | None:
    """Given an HTML page and the text of one known-good item from a past
    successful scrape, return the best fresh SelectorStack.

    Returns None if no element in the page matches *example_text*.
    """
    from lxml.html import document_fromstring

    try:
        doc = document_fromstring(html)
    except Exception:
        return None

    # Find every text-bearing leaf whose text matches.
    candidates: list[HtmlElement] = []
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        # Use direct child text first: element's own immediate text.
        own_text = "".join(el.xpath("./text()")).strip()
        if not own_text:
            # Fallback: concatenated text_content() for <a>wrapped<span>text</span></a>.
            own_text = el.text_content().strip()
            if len(own_text) > 500:
                continue   # too big to be an item title
        if text_match(example_text, own_text, ratio_limit):
            candidates.append(el)

    if not candidates:
        return None

    # Build a stack from each candidate, keep unique hashes, score by how
    # many siblings on the page match each stack's XPath.
    stacks: dict[str, SelectorStack] = {}
    for leaf in candidates[:max_stacks]:
        s = build_stack(leaf)
        if s.hash in stacks:
            continue
        try:
            matches = doc.xpath(s.xpath)
            s = SelectorStack(
                frames=s.frames, hash=s.hash, xpath=s.xpath,
                sibling_count=len(matches),
            )
        except Exception:
            continue
        stacks[s.hash] = s

    if not stacks:
        return None

    # Best stack = most siblings, tie-break on shorter XPath (more general).
    return max(stacks.values(), key=lambda s: (s.sibling_count, -len(s.xpath)))


# ── Per-field recovery within an item ─────────────────────────────────────────

_SEMANTIC_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "time", "a", "img"})


def recover_field_selector(
    item_html: str,
    example_text: str,
    full_page_html: str,
    item_xpath: str,
    ratio_limit: float = 0.85,
) -> str | None:
    """Find a relative XPath for a field within *item_html*.

    *item_html*:     HTML of one representative item (as a string fragment).
    *example_text*:  The expected text or URL substring for this field.
    *full_page_html*: The full rendered page HTML for hit-rate verification.
    *item_xpath*:    The item-level selector (used to count non-empty hits).

    Returns a relative XPath string (e.g. ``.//h2[@class='title']``) or None.
    """
    from lxml.html import fragment_fromstring, document_fromstring
    from lxml import etree

    try:
        item_el = fragment_fromstring(item_html, create_parent="div")
    except Exception:
        return None

    # Find leaf nodes whose text fuzzy-matches example_text.
    candidates: list[HtmlElement] = []
    for el in item_el.iter():
        if not isinstance(el.tag, str):
            continue
        own_text = "".join(el.xpath("./text()")).strip()
        if not own_text:
            own_text = el.text_content().strip()
        # Also check href/src for link/thumbnail fields
        for attr in ("href", "src", "datetime", "content"):
            own_text = own_text or el.attrib.get(attr, "").strip()
        if own_text and text_match(example_text, own_text, ratio_limit):
            candidates.append(el)

    if not candidates:
        return None

    # For each candidate build a relative xpath and score it on the full page.
    try:
        doc = document_fromstring(full_page_html)
        items = doc.xpath(item_xpath)
    except Exception:
        items = []

    best_xpath: str | None = None
    best_hits = -1

    for leaf in candidates[:8]:
        rel_xpath = _relative_xpath_within_item(leaf, item_el)
        if not rel_xpath:
            continue

        # Count how many items produce non-empty text for this relative xpath.
        hit_count = 0
        for item in items[:20]:
            try:
                r = item.xpath(rel_xpath)
                if r:
                    v = r[0]
                    text = v.text_content().strip() if hasattr(v, "text_content") else str(v).strip()
                    if text:
                        hit_count += 1
            except Exception:
                pass

        if hit_count > best_hits:
            best_hits = hit_count
            best_xpath = rel_xpath

    return best_xpath


def recover_field_selectors(
    item_html: str,
    examples: list[str],
    full_page_html: str,
    item_xpath: str,
    ratio_limit: float = 0.85,
) -> list[str]:
    """Find relative XPaths for a field matching any of the provided examples.

    *item_html*:     HTML of one representative item (as a string fragment).
    *examples*:      List of expected text or URL substrings for this field.
    *full_page_html*: The full rendered page HTML for hit-rate verification.
    *item_xpath*:    The item-level selector (used to count non-empty hits).

    Returns a list of relative XPath strings, deduplicated and sorted by
    hit-rate descending. When multiple examples are provided, returns XPaths
    for all of them; the caller can combine with XPath union operator.
    """
    if not examples:
        return []

    # Collect all candidate XPaths from all examples
    all_candidates: dict[str, int] = {}  # xpath -> hit_count

    for example_text in examples:
        if not example_text:
            continue

        result = recover_field_selector(
            item_html, example_text, full_page_html, item_xpath, ratio_limit
        )
        if result:
            # Count hits for this XPath
            try:
                from lxml.html import document_fromstring
                doc = document_fromstring(full_page_html)
                items = doc.xpath(item_xpath)
                hit_count = 0
                for item in items[:20]:
                    try:
                        r = item.xpath(result)
                        if r:
                            v = r[0]
                            text = v.text_content().strip() if hasattr(v, "text_content") else str(v).strip()
                            if text:
                                hit_count += 1
                    except Exception:
                        pass
                all_candidates[result] = hit_count
            except Exception:
                all_candidates[result] = 0

    # Sort by hit count descending
    sorted_xpaths = sorted(all_candidates.keys(), key=lambda x: all_candidates[x], reverse=True)
    return sorted_xpaths


def _relative_xpath_within_item(
    leaf: "HtmlElement", item_root: "HtmlElement"
) -> str | None:
    """Build the shortest relative xpath from item_root to leaf."""
    # Prefer semantic tag shortcuts.
    if leaf.tag in _SEMANTIC_TAGS:
        # Is it unique under the item?
        siblings = item_root.xpath(f".//{leaf.tag}")
        if len(siblings) == 1:
            return f".//{leaf.tag}"
        # With class discriminator
        cls = leaf.attrib.get("class", "").split()
        if cls:
            first_cls = cls[0]
            matches = item_root.xpath(f".//{leaf.tag}[contains(@class,{first_cls!r})]")
            if len(matches) == 1:
                return f".//{leaf.tag}[contains(@class,{first_cls!r})]"

    # Generic: walk up from leaf building path components until we hit item_root.
    path_parts: list[str] = []
    current = leaf
    while current is not None and current is not item_root:
        parent = current.getparent()
        if parent is None:
            break
        tag = current.tag if isinstance(current.tag, str) else "*"
        cls = current.attrib.get("class", "").split()
        if cls:
            first = cls[0]
            part = f"{tag}[contains(@class,{first!r})]"
        else:
            part = tag
        path_parts.insert(0, part)
        current = parent

    if not path_parts:
        return None
    return ".//" + "/".join(path_parts)
