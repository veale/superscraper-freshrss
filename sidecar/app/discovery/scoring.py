"""Score how likely a JSON structure represents a feed of items."""

from __future__ import annotations

from typing import Any


# Keys commonly found in feed-like items, grouped by role.
TITLE_KEYS = frozenset({
    "title", "name", "headline", "subject", "heading", "label",
    "post_title", "article_title",
})
URL_KEYS = frozenset({
    "url", "uri", "href", "link", "permalink", "canonical_url",
    "slug", "path", "web_url", "source_url",
})
DATE_KEYS = frozenset({
    "date", "created", "published", "timestamp", "created_at",
    "published_at", "publishedat", "updated_at", "time", "datetime",
    "pub_date", "pubdate", "modified", "posted_at",
})
CONTENT_KEYS = frozenset({
    "content", "body", "text", "description", "summary", "excerpt",
    "abstract", "html", "full_text",
})
AUTHOR_KEYS = frozenset({
    "author", "creator", "writer", "byline", "author_name",
})
IMAGE_KEYS = frozenset({
    "image", "thumbnail", "thumb", "img", "photo", "cover",
    "image_url", "thumbnail_url", "featured_image", "og_image",
})


def _normalise(key: str) -> str:
    """Lower-case and strip common separators for fuzzy matching."""
    return key.lower().replace("-", "_").replace(" ", "_")


def _keys_overlap(item_keys: set[str], reference: frozenset[str]) -> bool:
    return bool(item_keys & reference)


def score_feed_likeness(data: Any) -> float:
    """Return a 0.0–1.0 score for how feed-like *data* is.

    *data* can be:
      - a list of dicts  (direct array of items)
      - a dict with a single key whose value is a list of dicts
        (common wrapper pattern like ``{"data": [...]}`` )
    """
    items = _extract_items(data)
    if not items:
        return 0.0

    score = 0.0
    count = len(items)

    # ── Item count ────────────────────────────────────────────────────────
    if 5 <= count <= 100:
        score += 0.15
    elif count > 0:
        score += 0.05

    # ── Collect normalised keys from a sample of items ────────────────────
    sample = items[: min(10, count)]
    all_keys: set[str] = set()
    for item in sample:
        all_keys.update(_normalise(k) for k in item.keys())

    # ── Key-role checks ──────────────────────────────────────────────────
    if _keys_overlap(all_keys, TITLE_KEYS):
        score += 0.25
    if _keys_overlap(all_keys, URL_KEYS):
        score += 0.20
    if _keys_overlap(all_keys, DATE_KEYS):
        score += 0.15
    if _keys_overlap(all_keys, CONTENT_KEYS):
        score += 0.10
    if _keys_overlap(all_keys, AUTHOR_KEYS):
        score += 0.05
    if _keys_overlap(all_keys, IMAGE_KEYS):
        score += 0.03

    # ── Structural consistency ────────────────────────────────────────────
    if count >= 3:
        key_sets = [set(item.keys()) for item in sample]
        common = set.intersection(*key_sets)
        if len(common) >= 3:
            score += 0.07

    return round(min(score, 1.0), 3)


def _extract_items(data: Any) -> list[dict]:
    """Try to pull a list-of-dicts from *data*."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data
        return []

    if isinstance(data, dict):
        # Unwrap one level: look for the largest list-of-dicts value.
        best: list[dict] = []
        for v in data.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                if len(v) > len(best):
                    best = v
        return best

    return []


def find_best_array_path(obj: Any, prefix: str = "") -> list[tuple[str, list[dict], float]]:
    """Walk a nested JSON structure and return (dot_path, items, score)
    for every array-of-objects found, sorted by score descending."""
    results: list[tuple[str, list[dict], float]] = []
    _walk(obj, prefix, results)
    results.sort(key=lambda t: t[2], reverse=True)
    return results


def _walk(
    obj: Any,
    path: str,
    acc: list[tuple[str, list[dict], float]],
    depth: int = 0,
) -> None:
    if depth > 8:
        return
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            sc = score_feed_likeness(obj)
            if sc > 0.1:
                acc.append((path, obj, sc))
            # Also recurse into list items to find nested arrays.
            for i, item in enumerate(obj[:3]):  # Sample first few items.
                if isinstance(item, dict):
                    child_path = f"{path}[{i}]" if path else f"[{i}]"
                    _walk(item, child_path, acc, depth + 1)
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            child_path = f"{path}.{k}" if path else k
            _walk(v, child_path, acc, depth + 1)
