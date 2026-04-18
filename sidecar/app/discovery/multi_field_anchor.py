"""Find item container and field selectors from multiple user examples via LCA.

When the user provides example values for two or more fields of one real item
(title, date, author, link, content, thumbnail), we locate each field's element
in the DOM and compute their lowest common ancestor.  That LCA, possibly expanded
to the nearest repeating-sibling ancestor, IS the item container.  The path from
LCA to each field-bearing element IS that field's relative XPath selector.

Multi-row variant: if the user supplies examples from two structurally distinct
item families on the same page, we detect the split and emit a union selector.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

from lxml import etree
from lxml import html as lxml_html

from app.discovery.selector_generation import _meaningful_classes


@dataclass
class MultiAnchorResult:
    item_selector: str
    field_selectors: dict[str, str]
    item_count: int
    matched_roles: list[str]
    unmatched_roles: list[str]
    item_outer_htmls: list[str]
    confidence: float
    warnings: list[str] = field(default_factory=list)


# ── Public entry points ──────────────────────────────────────────────────────


def find_item_from_examples(
    html_text: str,
    examples: dict[str, str],
    *,
    max_combos: int = 200,
) -> Optional[MultiAnchorResult]:
    """Single-row variant: derive item + field selectors from one set of examples.

    examples: {role: example_text}, e.g.
        {"title": "Remembering HK", "timestamp": "April 10, 2026", "author": "Maya Wang"}

    Returns None if no examples matched the page at all.
    """
    if not html_text or not examples:
        return None

    tree = lxml_html.fromstring(html_text)

    hits_by_role: dict[str, list] = {}
    unmatched: list[str] = []
    for role, text in examples.items():
        if not text:
            continue
        matched = _find_field_matches(tree, role, text)
        if matched:
            hits_by_role[role] = matched[:8]
        else:
            unmatched.append(role)

    if not hits_by_role:
        return None

    matched_roles = list(hits_by_role.keys())

    best = None
    best_score = -1e9
    combo_count = 0
    role_order = sorted(matched_roles, key=lambda r: len(hits_by_role[r]))
    hit_lists = [hits_by_role[r] for r in role_order]

    for combo in itertools.product(*hit_lists):
        combo_count += 1
        if combo_count > max_combos:
            break
        inner_lca = _lca(list(combo))
        if inner_lca is None:
            continue
        container, reps = _expand_to_repeating(inner_lca)
        score = _score(container, combo, reps)
        if score > best_score:
            best_score = score
            best = (container, combo, role_order, reps)

    if best is None:
        return None

    container, combo, order, reps = best

    item_selector = _xpath_for(container)
    field_selectors: dict[str, str] = {}
    for role, field_el in zip(order, combo):
        for_attr = "href" if role == "link" else None
        rel = _relative_xpath(container, field_el, for_attr=for_attr)
        if rel:
            field_selectors[role] = rel

    validation_tree = lxml_html.fromstring(html_text)
    try:
        matching_items = validation_tree.xpath(item_selector)
    except Exception:
        matching_items = []

    warnings: list[str] = []
    if len(matching_items) < 2:
        warnings.append(
            f"item_selector '{item_selector}' matched {len(matching_items)} elements; "
            "expected >=2."
        )

    outer_htmls: list[str] = []
    for item in matching_items[:3]:
        try:
            outer_htmls.append(etree.tostring(item, encoding="unicode", method="html"))
        except Exception:
            pass

    return MultiAnchorResult(
        item_selector=item_selector,
        field_selectors=field_selectors,
        item_count=len(matching_items),
        matched_roles=matched_roles,
        unmatched_roles=unmatched,
        item_outer_htmls=outer_htmls,
        confidence=_confidence(reps, len(matched_roles), len(matching_items)),
        warnings=warnings,
    )


def find_items_from_rows(
    html_text: str,
    rows: list[dict[str, str]],
) -> Optional[MultiAnchorResult]:
    """Multi-row variant.  Each row is a full field-set from ONE item.

    If all rows resolve to the same item_selector → return the best single outcome.
    If rows split across 2+ distinct selectors → emit a union item_selector with
    per-family union field selectors.  Rows that fail entirely are silently dropped
    (a warning is added).

    Falls straight through to find_item_from_examples when only one row is given.
    """
    if not rows:
        return None

    per_row: list[MultiAnchorResult] = []
    failed_rows: list[int] = []
    for i, row in enumerate(rows):
        out = find_item_from_examples(html_text, row)
        if out is None:
            failed_rows.append(i)
        else:
            per_row.append(out)

    if not per_row:
        return None

    # Group by item_selector.
    by_sel: dict[str, list[MultiAnchorResult]] = {}
    for o in per_row:
        by_sel.setdefault(o.item_selector, []).append(o)

    # Single family.
    if len(by_sel) == 1:
        best = max(per_row, key=lambda o: len(o.matched_roles))
        if failed_rows:
            best.warnings.append(
                f"{len(failed_rows)} example row(s) couldn't be located; "
                f"result is based on the remaining {len(per_row)}."
            )
        return best

    # Multi-family: build union selector and union field selectors.
    distinct_selectors = list(by_sel.keys())
    union_item = " | ".join(distinct_selectors)

    all_roles: set[str] = set()
    for o in per_row:
        all_roles.update(o.field_selectors.keys())

    union_fields: dict[str, str] = {}
    for role in sorted(all_roles):
        per_family: list[str] = []
        for sel in distinct_selectors:
            for o in by_sel[sel]:
                if role in o.field_selectors:
                    per_family.append(o.field_selectors[role])
                    break
        uniq = list(dict.fromkeys(per_family))
        if uniq:
            union_fields[role] = uniq[0] if len(uniq) == 1 else " | ".join(uniq)

    tree = lxml_html.fromstring(html_text)
    try:
        matched = tree.xpath(union_item)
    except Exception:
        matched = []

    samples_by_family: dict[str, list[str]] = {s: [] for s in distinct_selectors}
    for item in matched:
        for sel in distinct_selectors:
            try:
                if item in tree.xpath(sel):
                    if len(samples_by_family[sel]) < 2:
                        samples_by_family[sel].append(
                            etree.tostring(item, encoding="unicode", method="html")
                        )
                    break
            except Exception:
                continue
    outer_htmls = [h for fam_list in samples_by_family.values() for h in fam_list][:4]

    mean_conf = sum(o.confidence for o in per_row) / len(per_row)
    warnings = [
        f"Emitted union selector across {len(distinct_selectors)} item families: "
        + ", ".join(distinct_selectors)
    ]
    if failed_rows:
        warnings.append(
            f"{len(failed_rows)} example row(s) couldn't be located and were skipped."
        )

    return MultiAnchorResult(
        item_selector=union_item,
        field_selectors=union_fields,
        item_count=len(matched),
        matched_roles=sorted(all_roles),
        unmatched_roles=[],
        item_outer_htmls=outer_htmls,
        confidence=mean_conf,
        warnings=warnings,
    )


def decode_example_rows(form) -> list[dict[str, str]]:
    """Turn multi-value form fields into per-row dicts.

    Row i uses index i of each field's list.  Rows with zero values are dropped.
    Also honours the singular legacy *_example field when the plural list is absent.
    """
    roles = ("title", "link", "content", "timestamp", "author", "thumbnail")
    lists: dict[str, list[str]] = {
        r: [v.strip() for v in form.getlist(f"{r}_examples") if v.strip()]
        for r in roles
    }
    for r in roles:
        if not lists[r]:
            v = (form.get(f"{r}_example") or "").strip()
            if v:
                lists[r] = [v]
    n_rows = max((len(v) for v in lists.values()), default=0)
    rows: list[dict[str, str]] = []
    for i in range(n_rows):
        row = {r: lists[r][i] for r in roles if i < len(lists[r]) and lists[r][i]}
        if row:
            rows.append(row)
    return rows


# ── Internal helpers ─────────────────────────────────────────────────────────


def _normalise(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _find_field_matches(tree, role: str, text: str) -> list:
    if role == "link":
        needle = text.strip()
        if not needle:
            return []
        return [
            a for a in tree.iter("a")
            if (a.get("href") or "") and (
                needle in (a.get("href") or "") or (a.get("href") or "") in needle
            )
        ]

    needle = _normalise(text)[:80]
    if not needle:
        return []

    hits = []
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        if needle in _normalise(el.text_content() or ""):
            has_containing_child = any(
                isinstance(child.tag, str)
                and needle in _normalise(child.text_content() or "")
                for child in el
            )
            if not has_containing_child:
                hits.append(el)
    return hits


def _lca(elements: list) -> Optional:
    if not elements:
        return None
    if len(elements) == 1:
        return elements[0]
    ancestries = [
        list(reversed(list(e.iterancestors()))) + [e] for e in elements
    ]
    common = None
    for group in zip(*ancestries):
        if all(x is group[0] for x in group):
            common = group[0]
        else:
            break
    return common


def _expand_to_repeating(inner_lca, max_steps: int = 8) -> tuple:
    current = inner_lca
    for _ in range(max_steps):
        reps = _count_same_class_siblings(current)
        if reps >= 2:
            return current, reps
        parent = current.getparent()
        if parent is None:
            return current, 1
        current = parent
    return current, _count_same_class_siblings(current)


def _count_same_class_siblings(el) -> int:
    parent = el.getparent()
    if parent is None:
        return 1
    my_cls = _meaningful_first_class(el)
    return sum(
        1 for sib in parent
        if isinstance(sib.tag, str)
        and sib.tag == el.tag
        and _meaningful_first_class(sib) == my_cls
    )


def _meaningful_first_class(el) -> str:
    cls = (el.get("class") or "").strip()
    if not cls:
        return ""
    meaningful = _meaningful_classes(cls).split()
    return meaningful[0] if meaningful else ""


def _xpath_for(el) -> str:
    cls = _meaningful_first_class(el)
    if cls:
        return f"//{el.tag}[contains(@class, '{cls}')]"
    if el.get("id"):
        return f"//{el.tag}[@id='{el.get('id')}']"
    if el.get("role"):
        return f"//{el.tag}[@role='{el.get('role')}']"
    return f"//{el.tag}"


def _relative_xpath(lca_el, field_el, *, for_attr: str | None = None) -> Optional[str]:
    chain = []
    cur = field_el
    while cur is not None and cur is not lca_el:
        chain.append(cur)
        cur = cur.getparent()
    if cur is None:
        return None

    for node in chain:
        cls = _meaningful_first_class(node)
        if cls:
            base = f".//{node.tag}[contains(@class, '{cls}')]"
            return base + (f"/@{for_attr}" if for_attr else "")

    if for_attr:
        return f".//{chain[0].tag}/@{for_attr}"
    return f".//{chain[0].tag}"


def _score(container, combo, reps: int) -> float:
    size = sum(1 for _ in container.iter())
    return (reps * 10) + (len(combo) * 5) - (size * 0.02)


def _confidence(reps: int, fields_matched: int, items: int) -> float:
    f = min(fields_matched, 5) / 5.0
    r = min(reps, 10) / 10.0
    i = 1.0 if items >= 3 else (0.5 if items >= 2 else 0.1)
    return round(0.5 * f + 0.3 * r + 0.2 * i, 2)
