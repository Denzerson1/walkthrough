"""
Catalog loading and search.

The catalog is small (tens of items), so everything is read from disk into
memory and searched with simple tag/keyword scoring. The assistant (M5)
uses the same scoring through search_catalog.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

CatalogKind = Literal["floors", "furniture", "styles"]
KINDS: tuple[CatalogKind, ...] = ("floors", "furniture", "styles")


@dataclass
class Catalog:
    floors: list[dict[str, Any]] = field(default_factory=list)
    furniture: list[dict[str, Any]] = field(default_factory=list)
    styles: list[dict[str, Any]] = field(default_factory=list)

    def items(self, kind: CatalogKind) -> list[dict[str, Any]]:
        return getattr(self, kind)

    def by_id(self, kind: CatalogKind, item_id: str) -> dict[str, Any] | None:
        return next((i for i in self.items(kind) if i.get("id") == item_id), None)

    def exists(self, kind: CatalogKind, item_id: str) -> bool:
        return self.by_id(kind, item_id) is not None


def load_catalog(catalog_root: Path) -> Catalog:
    catalog = Catalog()
    for kind in KINDS:
        directory = catalog_root / kind
        if not directory.is_dir():
            continue
        entries: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:  # pragma: no cover - author error
                raise ValueError(f"invalid catalog JSON: {path}: {exc}") from exc
            if isinstance(data, list):
                entries.extend(data)
            else:
                entries.append(data)
        setattr(catalog, kind, entries)
    return catalog


def _text_of(item: dict[str, Any]) -> str:
    """Flatten the searchable fields of a catalog entry into one lowercase blob."""
    parts: list[str] = []
    for key in ("id", "name", "category", "description"):
        value = item.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("tags", "styles", "materials", "colors"):
        value = item.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
    return " ".join(parts).lower()


def score_item(item: dict[str, Any], query: str) -> float:
    """
    Tag/keyword scoring. An exact id or tag match outranks a substring hit,
    so "oak" prefers an item tagged oak over one that merely mentions it.
    """
    q = query.strip().lower()
    if not q:
        return 0.0
    terms = [t for t in q.replace(",", " ").split() if t]
    if not terms:
        return 0.0

    text = _text_of(item)
    tags = {str(t).lower() for t in item.get("tags", [])}
    tags |= {str(t).lower() for t in item.get("styles", [])}
    tags |= {str(t).lower() for t in item.get("materials", [])}
    item_id = str(item.get("id", "")).lower()
    name = str(item.get("name", "")).lower()
    category = str(item.get("category", "")).lower()

    score = 0.0
    for term in terms:
        if term == item_id:
            score += 10.0
        elif term in tags:
            score += 4.0
        elif term == category:
            score += 3.5
        elif term in name:
            score += 2.0
        elif term in text:
            score += 1.0
    # Normalise by term count so long queries do not automatically win.
    return score / len(terms)


def search(
    catalog: Catalog,
    kind: CatalogKind,
    query: str = "",
    filters: dict[str, Any] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    items = catalog.items(kind)
    if filters:
        items = [i for i in items if _matches_filters(i, filters)]
    if not query:
        return items[:limit]
    scored = [(score_item(i, query), i) for i in items]
    scored = [(s, i) for s, i in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
    return [i for _, i in scored[:limit]]


def _matches_filters(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, wanted in filters.items():
        if wanted in (None, "", []):
            continue
        actual = item.get(key)
        if isinstance(actual, list):
            wanted_list = wanted if isinstance(wanted, list) else [wanted]
            if not any(str(w).lower() in {str(a).lower() for a in actual} for w in wanted_list):
                return False
        elif isinstance(wanted, list):
            if str(actual).lower() not in {str(w).lower() for w in wanted}:
                return False
        elif str(actual).lower() != str(wanted).lower():
            return False
    return True


def summarise(item: dict[str, Any], kind: CatalogKind) -> dict[str, Any]:
    """
    Compact form sent to the model. Keeps the token cost of search results low
    (the brief's cost guard) while carrying enough to choose sensibly.
    """
    base = {
        "id": item.get("id"),
        "name": item.get("name"),
    }
    if kind == "floors":
        base |= {
            "category": item.get("category"),
            "tags": item.get("tags", []),
            "tileSizeM": item.get("tileSizeM"),
        }
    elif kind == "furniture":
        base |= {
            "category": item.get("category"),
            "styles": item.get("styles", []),
            "colors": item.get("colors", []),
            "dimensionsM": item.get("dimensionsM"),
        }
    else:
        base |= {
            "description": item.get("description"),
            "palette": item.get("palette", []),
            "floorIds": item.get("floorIds", []),
        }
    return base
