"""Trafilatura-inspired tree pruning — drop obvious noise before extraction.

Ported from trafilatura/xpaths.py (Adbar Barbaresi's library). The XPath
expressions are copied deliberately; refining them requires exhaustive
testing across dozens of sites and we don't have that test corpus.

Usage:
    tree = lxml.html.document_fromstring(raw_html)
    cleaned = prune_tree(tree)
    # cleaned is the SAME tree with noise subtrees removed. No copy is made
    # unless keep_original=True.
"""
from __future__ import annotations

from copy import deepcopy

from lxml.etree import XPath, strip_tags
from lxml.html import HtmlElement


# ── Tag lists ────────────────────────────────────────────────────────────────
# Trafilatura's MANUALLY_CLEANED and MANUALLY_STRIPPED from settings.py.
# MANUALLY_CLEANED: tags whose content is noise — drop entire subtree.
# MANUALLY_STRIPPED: tags that wrap meaningful content — strip the tag,
#                    keep the children (e.g. <font>text</font> → text).

# Tags that are noise everywhere — scripts, styles, interactive embeds.
_ALWAYS_CLEANED: list[str] = [
    "script", "style",
    # interactive/embed noise
    "button", "dialog", "embed", "form", "input", "map", "menu",
    "noscript", "object", "output", "select", "svg", "textarea",
    # frames
    "applet", "iframe",
    # deprecated but still seen
    "frame", "frameset", "noframes",
]

# Tags that are noise for *item-extraction* but carry context for LLMs.
_STRUCTURAL_NOISE: list[str] = ["aside", "footer"]

# Back-compat: the old constant is the union, so existing callers
# that don't pass the new flag get identical behaviour.
MANUALLY_CLEANED: list[str] = _ALWAYS_CLEANED + _STRUCTURAL_NOISE

MANUALLY_STRIPPED: list[str] = [
    "area", "base", "basefont", "bdi", "bdo", "blink",
    "canvas", "col", "colgroup", "data", "datalist",
    "figcaption", "fieldset", "link", "meta",
    "optgroup", "option", "param", "progress",
    "rp", "rt", "rtc", "ruby", "slot", "source",
    "template", "track", "use", "wbr",
]

CUT_EMPTY_ELEMS = frozenset({
    "article", "b", "blockquote", "dd", "div", "dt",
    "em", "h1", "h2", "h3", "h4", "h5", "h6", "i",
    "li", "main", "p", "pre", "q", "section", "span", "strong",
})


# ── XPath lists (copied verbatim from trafilatura/xpaths.py) ─────────────────
# IMPORTANT: These are direct ports, not paraphrases. Do not reformat or
# simplify — each clause is present for a specific real-world site
# observed in Trafilatura's test corpus. Changing them = regressions.

OVERALL_DISCARD_XPATH = [XPath(x) for x in (
    # navigation + footers + related-post widgets + sharing buttons + ads
    ''' .//*[self::div or self::item or self::list
              or self::p or self::section or self::span][
        contains(translate(@id, "F","f"), "footer")
        or contains(translate(@class, "F","f"), "footer")
        or contains(@id, "related") or contains(@class, "elated")
        or contains(@id|@class, "viral")
        or starts-with(@id|@class, "shar")
        or contains(@class, "share-")
        or contains(translate(@id, "S", "s"), "share")
        or contains(@id|@class, "social") or contains(@class, "sociable")
        or contains(@id|@class, "syndication")
        or starts-with(@id, "jp-") or starts-with(@id, "dpsp-content")
        or contains(@class, "embedded") or contains(@class, "embed")
        or contains(@id|@class, "newsletter") or contains(@class, "subnav")
        or contains(@id|@class, "cookie")
        or contains(@id|@class, "tags") or contains(@class, "tag-list")
        or contains(@id|@class, "sidebar") or contains(@id|@class, "banner")
        or contains(@class, "bar") or contains(@class, "meta")
        or contains(@id, "menu") or contains(@class, "menu")
        or contains(translate(@id, "N", "n"), "nav")
        or contains(translate(@role, "N", "n"), "nav")
        or starts-with(@class, "nav") or contains(@class, "avigation")
        or contains(@class, "navbar") or contains(@class, "navbox")
        or starts-with(@class, "post-nav")
        or contains(@id|@class, "breadcrumb")
        or contains(@id|@class, "bread-crumb")
        or contains(@id|@class, "author")
        or contains(@id|@class, "button")
        or contains(translate(@class, "B", "b"), "byline")
        or contains(@class, "rating") or contains(@class, "widget")
        or contains(@class, "attachment") or contains(@class, "timestamp")
        or contains(@class, "user-info") or contains(@class, "user-profile")
        or contains(@class, "-ad-") or contains(@class, "-icon")
        or contains(@class, "article-infos") or contains(@class, "nfoline")
        or contains(@data-component, "MostPopularStories")
        or contains(@class, "outbrain") or contains(@class, "taboola")
        or contains(@class, "criteo")
        or contains(@class, "options") or contains(@class, "expand")
        or contains(@class, "consent") or contains(@class, "modal-content")
        or contains(@class, " ad ") or contains(@class, "permission")
        or contains(@class, "next-") or contains(@class, "-stories")
        or contains(@class, "most-popular")
        or contains(@class, "mol-factbox")
        or starts-with(@class, "ZendeskForm")
        or contains(@id|@class, "message-container")
        or contains(@class, "yin") or contains(@class, "zlylin")
        or contains(@class, "xg1") or contains(@id, "bmdh")
        or contains(@class, "slide") or contains(@class, "viewport")
        or @data-lp-replacement-content
        or contains(@id, "premium") or contains(@class, "overlay")
        or contains(@class, "paid-content") or contains(@class, "paidcontent")
        or contains(@class, "obfuscated") or contains(@class, "blurred")]''',
    # comment debris + hidden parts
    ''' .//*[@class="comments-title" or contains(@class, "comments-title")
        or contains(@class, "nocomments")
        or starts-with(@id|@class, "reply-") or contains(@class, "-reply-")
        or contains(@class, "message") or contains(@id, "reader-comments")
        or contains(@id, "akismet") or contains(@class, "akismet")
        or contains(@class, "suggest-links")
        or starts-with(@class, "hide-") or contains(@class, "-hide-")
        or contains(@class, "hide-print")
        or contains(@id|@style, "hidden")
        or contains(@class, " hidden") or contains(@class, " hide")
        or contains(@class, "noprint")
        or contains(@style, "display:none") or contains(@style, "display: none")
        or @aria-hidden="true" or contains(@class, "notloaded")]''',
)]

# Applied only when favoring precision (fewer false positives, may miss some signal).
PRECISION_DISCARD_XPATH = [XPath(x) for x in (
    './/header',
    ''' .//*[self::div or self::item or self::list
              or self::p or self::section or self::span][
        contains(@id|@class, "bottom") or contains(@id|@class, "link")
        or contains(@style, "border")]''',
)]

REMOVE_COMMENTS_XPATH = [XPath(
    ''' .//*[self::div or self::list or self::section][
        starts-with(translate(@id, "C","c"), 'comment')
        or starts-with(translate(@class, "C","c"), 'comment')
        or contains(@class, 'article-comments')
        or contains(@class, 'post-comments')
        or starts-with(@id, 'comol')
        or starts-with(@id, 'disqus_thread')
        or starts-with(@id, 'dsq-comments')]'''
)]


# ── Public API ────────────────────────────────────────────────────────────────

def prune_tree(
    tree: HtmlElement,
    *,
    drop_comments: bool = True,
    drop_precision: bool = False,
    drop_structural_noise: bool = True,
    keep_original: bool = False,
) -> HtmlElement:
    """Remove navigation, footers, sharing widgets, and comment sections.

    Mutates `tree` in place unless `keep_original=True`, in which case a
    deepcopy is returned. The in-place default matches lxml convention and
    avoids doubling memory on big pages.

    `drop_precision=True` is aggressive — removes <header>, link-heavy spans,
    and anything with inline borders. Useful for feed-item extraction, where
    we care about recall of *items* not recall of *prose*.

    `drop_structural_noise=True` removes <aside> and <footer> tags, which are
    noise for item-extraction but carry useful context for LLM skeleton generation.
    """
    target = deepcopy(tree) if keep_original else tree

    # Step 1: strip tags that carry no meaningful structure.
    strip_tags(target, *MANUALLY_STRIPPED)

    # Step 2: delete subtrees of tags that carry only noise.
    # Split into always-cleaned (scripts, styles, interactive embeds) and
    # structural noise (aside, footer) which can be optionally preserved.
    cleaned_tags = _ALWAYS_CLEANED + (_STRUCTURAL_NOISE if drop_structural_noise else [])
    for tag in cleaned_tags:
        for el in target.iter(tag):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    # Step 3: apply the overall XPath discard list.
    for expr in OVERALL_DISCARD_XPATH:
        for node in expr(target):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

    if drop_comments:
        for expr in REMOVE_COMMENTS_XPATH:
            for node in expr(target):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)

    if drop_precision:
        for expr in PRECISION_DISCARD_XPATH:
            for node in expr(target):
                parent = node.getparent()
                if parent is not None:
                    parent.remove(node)

    # Step 4: cut empty structural wrappers left behind.
    for el in target.xpath(".//*[not(node())]"):
        if el.tag in CUT_EMPTY_ELEMS:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    return target


def build_pruned_html(raw_html: str, *, drop_precision: bool = False) -> str:
    """Parse → prune → serialise. Returns cleaned HTML as a string.

    Used by the cascade to feed pruned HTML into selector generators that
    still take string input. Falls back to the raw input on parse errors.
    """
    from lxml import html as lxml_html
    from lxml.etree import tostring

    if not raw_html:
        return ""
    try:
        doc = lxml_html.document_fromstring(raw_html)
    except Exception:
        return raw_html
    prune_tree(doc, drop_comments=True, drop_precision=drop_precision)
    return tostring(doc, encoding="unicode", method="html")
