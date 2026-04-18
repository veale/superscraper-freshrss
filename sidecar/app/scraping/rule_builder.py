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
