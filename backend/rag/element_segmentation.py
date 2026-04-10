from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List


TABLE_CATEGORIES = {"Table"}
IMAGE_CATEGORIES = {"Image", "Picture", "FigureCaption"}
TEXT_CATEGORIES = {
    "Title",
    "NarrativeText",
    "ListItem",
    "UncategorizedText",
    "Header",
    "Footer",
    "Caption",
    "PageBreak",
    "PageNumber",
}
ELEMENT_FAMILIES = ("table", "text", "image", "other")


def normalize_element_category(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown"
    return text.rsplit(".", 1)[-1]


def element_family_from_category(category: Any) -> str:
    normalized = normalize_element_category(category)
    if normalized in TABLE_CATEGORIES:
        return "table"
    if normalized in IMAGE_CATEGORIES:
        return "image"
    if normalized in TEXT_CATEGORIES:
        return "text"
    return "other"


def element_family_from_dict(element: Dict[str, Any]) -> str:
    if not isinstance(element, dict):
        return "other"

    metadata = element.get("metadata")
    if isinstance(metadata, dict):
        for key in ("category", "type"):
            value = metadata.get(key)
            if value:
                return element_family_from_category(value)

    return element_family_from_category(element.get("type") or element.get("category"))


def segregate_elements_by_type(elements: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {family: [] for family in ELEMENT_FAMILIES}
    for item in elements or []:
        if not isinstance(item, dict):
            continue
        grouped[element_family_from_dict(item)].append(item)
    return grouped


def summarize_elements_by_type(elements: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    grouped = segregate_elements_by_type(elements)
    category_counts = Counter(
        normalize_element_category(
            (item.get("metadata") or {}).get("category")
            if isinstance(item.get("metadata"), dict) and (item.get("metadata") or {}).get("category")
            else (
                (item.get("metadata") or {}).get("type")
                if isinstance(item.get("metadata"), dict) and (item.get("metadata") or {}).get("type")
                else item.get("type") or item.get("category")
            )
        )
        for item in elements or []
        if isinstance(item, dict)
    )

    return {
        "total": sum(len(grouped[family]) for family in ELEMENT_FAMILIES),
        "table": len(grouped["table"]),
        "text": len(grouped["text"]),
        "image": len(grouped["image"]),
        "other": len(grouped["other"]),
        "category_counts": dict(category_counts),
    }


def build_stage_segregation_summary(
    *,
    raw_elements: Iterable[Dict[str, Any]],
    filtered_elements: Iterable[Dict[str, Any]],
    removed_elements: Iterable[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    return {
        "raw": summarize_elements_by_type(raw_elements),
        "filtered": summarize_elements_by_type(filtered_elements),
        "removed": summarize_elements_by_type(removed_elements),
    }
