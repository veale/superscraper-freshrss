"""Readability-inspired element scoring for feed-item candidates.

Ports the class/id regex patterns and weighting from Mozilla's Readability
algorithm (github.com/mozilla/readability) and adapts them to the narrower
question AutoFeed asks: "does this repeated element look like a feed item?"
"""
from __future__ import annotations

import re


# ── Patterns ──────────────────────────────────────────────────────────────────
# These three regexes are the heart of Readability's scoring. Do not narrow
# or reformulate them — they are the product of years of tuning against real
# websites. Source: Readability.js lines 140-148.

POSITIVE_RE = re.compile(
    r"article|body|content|entry|hentry|h-entry|main|page|pagination|post|text|blog|story",
    re.IGNORECASE,
)

NEGATIVE_RE = re.compile(
    r"-ad-|hidden|^hid$| hid$| hid |^hid |banner|combx|comment|com-|contact|footer|"
    r"gdpr|masthead|media|meta|outbrain|promo|related|scroll|share|shoutbox|sidebar|"
    r"skyscraper|sponsor|shopping|tags|widget",
    re.IGNORECASE,
)

UNLIKELY_CANDIDATES_RE = re.compile(
    r"-ad-|ai2html|banner|breadcrumbs|combx|comment|community|cover-wrap|disqus|extra|"
    r"footer|gdpr|header|legends|menu|related|remark|replies|rss|shoutbox|sidebar|"
    r"skyscraper|social|sponsor|supplemental|ad-break|agegate|pagination|pager|popup|"
    r"yom-remote",
    re.IGNORECASE,
)

OK_MAYBE_CANDIDATE_RE = re.compile(
    r"and|article|body|column|content|main|mathjax|shadow",
    re.IGNORECASE,
)

UNLIKELY_ROLES = frozenset({
    "menu", "menubar", "complementary", "navigation",
    "alert", "alertdialog", "dialog",
})

# Semantic-tag baseline scores (analogous to Readability._initializeNode).
# We keep the magnitudes modest because the scores are combined with
# Scrapling's repetition-count heuristics which are already in the 0-1 range.
TAG_BASELINE: dict[str, int] = {
    "article": 10,
    "section": 5,
    "div":     5,
    "li":      2,
    "tr":      2,
    "nav":    -15,
    "footer": -15,
    "aside":  -15,
    "header": -10,
    "form":   -10,
}


# ── Scoring ───────────────────────────────────────────────────────────────────

def class_id_weight(class_attr: str, id_attr: str) -> int:
    """Return a weight in roughly [-50, +50] for an element's class and id.

    The ±25 magnitude matches Readability's `_getClassWeight` (see source
    lines 2168-2198). Both class and id are tested independently — a div
    with `class="sidebar"` and `id="article"` scores 0, which is correct:
    the positive and negative signals cancel.
    """
    weight = 0
    if class_attr:
        if NEGATIVE_RE.search(class_attr):
            weight -= 25
        if POSITIVE_RE.search(class_attr):
            weight += 25
    if id_attr:
        if NEGATIVE_RE.search(id_attr):
            weight -= 25
        if POSITIVE_RE.search(id_attr):
            weight += 25
    return weight


def is_unlikely_candidate(class_attr: str, id_attr: str, role: str = "") -> bool:
    """Return True if the element is almost certainly boilerplate.

    This is Readability's `unlikelyCandidates` gate. It's intentionally
    separate from the scoring weights so callers can use it to *exclude*
    rather than merely *penalise* an element, which matters for Phase 4
    where a single bad selector poisons the whole refresh.
    """
    if role and role.lower() in UNLIKELY_ROLES:
        return True
    combined = f"{class_attr} {id_attr}"
    if UNLIKELY_CANDIDATES_RE.search(combined):
        if not OK_MAYBE_CANDIDATE_RE.search(combined):
            return True
    return False


def tag_baseline(tag: str) -> int:
    """Baseline score by tag name. Unknown tags return 0."""
    return TAG_BASELINE.get(tag.lower(), 0)


def node_score(tag: str, class_attr: str, id_attr: str, role: str = "") -> tuple[int, bool]:
    """Compute a (score, unlikely) tuple for a candidate element.

    Returns:
        score: integer weight. Higher is more feed-item-like. Typical range
            runs from about -50 (clearly navigation) to about +40 (an
            <article class="post">).
        unlikely: True if the element should be excluded outright, regardless
            of its score. Callers MUST honour this flag.
    """
    if is_unlikely_candidate(class_attr, id_attr, role):
        return 0, True
    return tag_baseline(tag) + class_id_weight(class_attr, id_attr), False
