from __future__ import annotations

import re
from collections import Counter, defaultdict
from io import StringIO
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

try:
    import pandas as pd
except Exception:  # pragma: no cover - optional runtime guard
    pd = None  # type: ignore


KEEP_CATEGORIES = [
    "Title",
    "NarrativeText",
    "Table",
    "ListItem",
    "UncategorizedText",
]

DISCARD_CATEGORIES = [
    "Header",
    "Footer",
    "Image",
    "Picture",
    "FigureCaption",
    "Caption",
    "PageBreak",
    "PageNumber",
]

MIN_TEXT_LENGTH = 3
HEADER_FOOTER_REPEAT_MIN_PAGES = 3
HEADER_FOOTER_SHORT_TEXT_LIMIT = 140
HEADER_ZONE_MAX_RATIO = 0.16
FOOTER_ZONE_MIN_RATIO = 0.84
TABLE_REPEAT_MAX_ROWS = 4
TABLE_REPEAT_MAX_COLUMNS = 8
FILTER_VERSION = 2

_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MULTISPACE_RE = re.compile(r"\s+")
_MARKDOWN_DECORATION_RE = re.compile(r"[*_`#>\[\]]+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9# ]+")
_DATE_TOKEN_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b")
_REVISION_TOKEN_RE = re.compile(r"\brev(?:ision)?\s*[:.\-]?\s*[a-z0-9._/-]+\b", re.IGNORECASE)
_PAGE_MARKER_RE = re.compile(
    r"\b(?:pag\.?|page|sheet)\s*\d+\s*(?:of|/)\s*\d+\b",
    re.IGNORECASE,
)
_IMAGE_PLACEHOLDER_RE = re.compile(
    r"(?:picture|image)\s*\[\d+\s*x\s*\d+\].*?(?:omitted|placeholder)|"
    r"start of picture text|end of picture text",
    re.IGNORECASE,
)
_SIGNATURE_ARTIFACT_RE = re.compile(
    r"digitally signed|firmato digitalmente|signed by",
    re.IGNORECASE,
)
_LEGAL_BOILERPLATE_RE = re.compile(
    r"document is property of|documento riservato|shall neither be shown to third parties|"
    r"all rights reserved|confidential",
    re.IGNORECASE,
)
_DOCUMENT_CHROME_RE = re.compile(
    r"company document id|sheet of sheets|validity status|revision number|"
    r"drawn by|checked by|approved by|drawing number|title block",
    re.IGNORECASE,
)
_STRONG_DOCUMENT_CHROME_RE = re.compile(
    r"company document id|sheet of sheets|title block",
    re.IGNORECASE,
)


def _normalize_category(raw_category: Any) -> str:
    value = str(raw_category or "").strip()
    if not value:
        return "Unknown"
    return value.rsplit(".", 1)[-1]


def element_category(element: Dict[str, Any]) -> str:
    if not isinstance(element, dict):
        return "Unknown"

    metadata = element.get("metadata")
    if isinstance(metadata, dict):
        for key in ("category", "type"):
            value = metadata.get(key)
            if value:
                return _normalize_category(value)

    return _normalize_category(element.get("type") or element.get("category"))


def element_text(element: Dict[str, Any]) -> str:
    if not isinstance(element, dict):
        return ""
    return str(element.get("text") or element.get("content") or "").strip()


def element_page(element: Dict[str, Any]) -> int:
    metadata = element.get("metadata") if isinstance(element, dict) else None
    if isinstance(metadata, dict):
        try:
            return int(metadata.get("page_number") or 1)
        except Exception:
            return 1
    return 1


def _normalized_text(text: str) -> str:
    value = str(text or "")
    value = _BREAK_RE.sub("\n", value)
    value = _HTML_TAG_RE.sub(" ", value)
    value = value.replace("\x00", " ")
    value = _MARKDOWN_DECORATION_RE.sub(" ", value)
    value = _MULTISPACE_RE.sub(" ", value)
    return value.strip()


def _repeat_key(text: str) -> str:
    normalized = _normalized_text(text).lower()
    normalized = _DATE_TOKEN_RE.sub("date_token", normalized)
    normalized = _REVISION_TOKEN_RE.sub("revision_token", normalized)
    normalized = _PAGE_MARKER_RE.sub("page_marker", normalized)
    normalized = re.sub(r"\b\d+(?:[.,]\d+)*\b", "#", normalized)
    normalized = _NON_ALNUM_RE.sub(" ", normalized)
    normalized = _MULTISPACE_RE.sub(" ", normalized)
    return normalized.strip()


def _split_text_fragments(text: str) -> List[str]:
    raw = str(text or "")
    if not raw:
        return []

    normalized = _BREAK_RE.sub("\n", raw)
    parts = [part.strip() for part in re.split(r"[\r\n]+", normalized)]
    fragments: List[str] = []

    for part in parts:
        if not part:
            continue
        if part.count("|") >= 2 and len(part) <= 260:
            cells = [cell.strip() for cell in part.split("|") if cell.strip()]
            if len(cells) >= 2:
                fragments.extend(cells)
                continue
        fragments.append(part)

    return fragments


def _extract_points(metadata: Dict[str, Any]) -> List[Tuple[float, float]]:
    if not isinstance(metadata, dict):
        return []

    candidates = []
    coordinates = metadata.get("coordinates")
    if isinstance(coordinates, dict):
        candidates.append(coordinates.get("points"))
    bbox = metadata.get("bbox")
    if isinstance(bbox, dict):
        candidates.append(bbox.get("points"))
        candidates.append(bbox.get("bbox"))
    elif isinstance(bbox, list):
        candidates.append(bbox)

    for candidate in candidates:
        if not isinstance(candidate, list):
            continue
        parsed: List[Tuple[float, float]] = []
        for point in candidate:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                parsed.append((float(point[0]), float(point[1])))
            except Exception:
                continue
        if parsed:
            return parsed

    return []


def _layout_height(metadata: Dict[str, Any], points: List[Tuple[float, float]]) -> Optional[float]:
    if not isinstance(metadata, dict):
        return None

    candidates = [
        metadata.get("layout_height"),
    ]

    coordinates = metadata.get("coordinates")
    if isinstance(coordinates, dict):
        candidates.append(coordinates.get("layout_height"))

    bbox = metadata.get("bbox")
    if isinstance(bbox, dict):
        candidates.append(bbox.get("layout_height"))

    for candidate in candidates:
        try:
            value = float(candidate)
            if value > 0:
                return value
        except Exception:
            continue

    if points:
        max_y = max(point[1] for point in points)
        if max_y > 0:
            return max_y
    return None


def _vertical_ratio(element: Dict[str, Any]) -> Optional[float]:
    metadata = element.get("metadata") if isinstance(element, dict) else None
    if not isinstance(metadata, dict):
        return None

    points = _extract_points(metadata)
    if not points:
        return None

    layout_height = _layout_height(metadata, points)
    if not layout_height:
        return None

    avg_y = sum(point[1] for point in points) / len(points)
    ratio = avg_y / layout_height
    if 0 <= ratio <= 1.5:
        return ratio
    return None


def _is_layout_band(vertical_ratio: Optional[float]) -> bool:
    return bool(
        vertical_ratio is not None
        and (vertical_ratio <= HEADER_ZONE_MAX_RATIO or vertical_ratio >= FOOTER_ZONE_MIN_RATIO)
    )


def _looks_like_page_marker(clean_text: str) -> bool:
    return bool(_PAGE_MARKER_RE.search(clean_text))


def _looks_like_image_placeholder(clean_text: str) -> bool:
    return bool(_IMAGE_PLACEHOLDER_RE.search(clean_text))


def _looks_like_signature_noise(clean_text: str) -> bool:
    return bool(_SIGNATURE_ARTIFACT_RE.search(clean_text))


def _looks_like_legal_boilerplate(clean_text: str) -> bool:
    return bool(_LEGAL_BOILERPLATE_RE.search(clean_text))


def _looks_like_document_chrome(clean_text: str) -> bool:
    return bool(_DOCUMENT_CHROME_RE.search(clean_text))


def _looks_like_strong_document_chrome(clean_text: str) -> bool:
    return bool(_STRONG_DOCUMENT_CHROME_RE.search(clean_text))


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd is not None and pd.isna(value):  # type: ignore[arg-type]
            return ""
    except Exception:
        pass
    return str(value).strip()


def _flatten_column_label(label: Any, index: int) -> str:
    if isinstance(label, tuple):
        parts = [
            _normalized_text(str(part))
            for part in label
            if _normalized_text(str(part)) and not str(part).startswith("Unnamed")
        ]
        flattened = " / ".join(parts)
    else:
        flattened = _normalized_text(str(label))

    if not flattened or flattened.lower().startswith("unnamed"):
        return f"column_{index + 1}"
    return flattened


def _table_dataframe_from_html(html: str) -> Optional[Any]:
    if pd is None:
        return None

    payload = str(html or "").strip()
    if not payload:
        return None

    try:
        tables = pd.read_html(StringIO(payload), keep_default_na=False)
    except Exception:
        return None

    if not tables:
        return None

    frame = tables[0].copy()
    try:
        frame = frame.fillna("")
    except Exception:
        pass
    frame.columns = [
        _flatten_column_label(label, index)
        for index, label in enumerate(list(frame.columns))
    ]
    return frame


def _table_dimensions(text: str, metadata: Dict[str, Any]) -> Tuple[int, int]:
    html = str(metadata.get("text_as_html") or "").strip() if isinstance(metadata, dict) else ""
    frame = _table_dataframe_from_html(html)
    if frame is not None:
        return (int(len(frame.index)), int(len(frame.columns)))

    rows: List[List[str]] = []
    for line in _split_text_fragments(text):
        stripped = line.strip()
        if not stripped:
            continue
        if set(stripped.replace("|", "").strip()) <= {"-", ":"}:
            continue
        cells = [cell.strip() for cell in stripped.split("|") if cell.strip()] if "|" in stripped else [stripped]
        rows.append(cells)

    return (len(rows), max((len(row) for row in rows), default=0))


def _looks_like_repeated_header_footer(
    *,
    category: str,
    clean_text: str,
    repeat_page_count: int,
    vertical_ratio: Optional[float],
    table_row_count: int = 0,
    table_column_count: int = 0,
) -> bool:
    in_layout_band = _is_layout_band(vertical_ratio)

    if category == "Table":
        compact_table = (
            table_row_count > 0
            and table_row_count <= TABLE_REPEAT_MAX_ROWS
            and table_column_count <= TABLE_REPEAT_MAX_COLUMNS
        )
        if compact_table and _looks_like_strong_document_chrome(clean_text):
            return True
        if compact_table and repeat_page_count >= 2 and _looks_like_document_chrome(clean_text):
            return True
        if in_layout_band and compact_table:
            return True
        if in_layout_band and _looks_like_document_chrome(clean_text):
            return True
        if repeat_page_count >= HEADER_FOOTER_REPEAT_MIN_PAGES and _looks_like_document_chrome(clean_text):
            return True
        return False

    if repeat_page_count < 2:
        return False

    if in_layout_band:
        return True

    short_repeat = len(clean_text) <= HEADER_FOOTER_SHORT_TEXT_LIMIT
    if repeat_page_count >= HEADER_FOOTER_REPEAT_MIN_PAGES and short_repeat:
        return True

    if repeat_page_count >= HEADER_FOOTER_REPEAT_MIN_PAGES and _looks_like_legal_boilerplate(clean_text):
        return True

    return False


def _build_fragment_indexes(
    elements: Iterable[Dict[str, Any]],
) -> Tuple[Dict[str, Set[int]], Dict[str, Set[int]]]:
    fragment_pages: Dict[str, Set[int]] = defaultdict(set)
    band_fragment_pages: Dict[str, Set[int]] = defaultdict(set)

    for raw_element in elements or []:
        if not isinstance(raw_element, dict):
            continue

        category = element_category(raw_element)
        if category == "Table":
            continue

        page_number = element_page(raw_element)
        vertical_ratio = _vertical_ratio(raw_element)

        for fragment in _split_text_fragments(element_text(raw_element)):
            key = _repeat_key(fragment)
            if not key:
                continue
            fragment_pages[key].add(page_number)

            clean_fragment = _normalized_text(fragment)
            if (
                category in DISCARD_CATEGORIES
                or _is_layout_band(vertical_ratio)
                or _looks_like_page_marker(clean_fragment)
                or _looks_like_legal_boilerplate(clean_fragment)
                or _looks_like_signature_noise(clean_fragment)
                or _looks_like_document_chrome(clean_fragment)
            ):
                band_fragment_pages[key].add(page_number)

    return fragment_pages, band_fragment_pages


def _fragment_discard_reason(
    *,
    clean_text: str,
    repeat_page_count: int,
    band_repeat_page_count: int,
    vertical_ratio: Optional[float],
) -> Optional[str]:
    if not clean_text:
        return None

    if _looks_like_image_placeholder(clean_text):
        return "Image Placeholder Fragment"
    if _looks_like_page_marker(clean_text):
        return "Page Marker Fragment"
    if _looks_like_signature_noise(clean_text):
        return "Signature / Stamp Fragment"
    if _looks_like_legal_boilerplate(clean_text):
        return "Legal Boilerplate Fragment"
    if band_repeat_page_count >= 2 and len(clean_text) <= HEADER_FOOTER_SHORT_TEXT_LIMIT * 2:
        return "Repeated Header/Footer Fragment"
    if _is_layout_band(vertical_ratio) and repeat_page_count >= 2 and len(clean_text) <= HEADER_FOOTER_SHORT_TEXT_LIMIT:
        return "Repeated Header/Footer Fragment"
    if repeat_page_count >= HEADER_FOOTER_REPEAT_MIN_PAGES and _looks_like_document_chrome(clean_text):
        return "Repeated Header/Footer Fragment"
    return None


def _table_row_noise_reason(clean_text: str) -> Optional[str]:
    if not clean_text:
        return "Empty Table Row"
    if _looks_like_page_marker(clean_text):
        return "Page Marker Row"
    if _looks_like_signature_noise(clean_text):
        return "Signature / Stamp Row"
    if _looks_like_legal_boilerplate(clean_text):
        return "Legal Boilerplate Row"
    if _looks_like_document_chrome(clean_text) and len(clean_text) <= HEADER_FOOTER_SHORT_TEXT_LIMIT * 2:
        return "Document Chrome Row"
    return None


def _table_text_from_frame(frame: Any) -> str:
    if frame is None:
        return ""

    try:
        return str(frame.to_markdown(index=False)).strip()
    except Exception:
        try:
            return str(frame.to_string(index=False)).strip()
        except Exception:
            return ""


def _clean_table_html(
    *,
    html: str,
    cleanup_stats: Counter,
) -> Tuple[str, str, Dict[str, Any]]:
    frame = _table_dataframe_from_html(html)
    if frame is None:
        return (str(html or "").strip(), "", {"rows_removed": 0, "changed": False})

    kept_rows: List[List[str]] = []
    seen_keys: Set[str] = set()
    rows_removed = 0

    for _, row in frame.iterrows():
        row_values = [_safe_text(value) for value in list(row)]
        combined = " | ".join(value for value in row_values if value).strip()
        row_key = _repeat_key(combined)

        reason = _table_row_noise_reason(_normalized_text(combined))
        if reason:
            rows_removed += 1
            cleanup_stats["table_rows_removed"] += 1
            continue
        if row_key and row_key in seen_keys:
            rows_removed += 1
            cleanup_stats["table_rows_removed"] += 1
            continue

        if row_key:
            seen_keys.add(row_key)
        kept_rows.append(row_values)

    cleaned_frame = frame.iloc[0:0].copy()
    if kept_rows and pd is not None:
        cleaned_frame = pd.DataFrame(kept_rows, columns=list(frame.columns))

    cleaned_html = str(html or "").strip()
    if pd is not None:
        try:
            cleaned_html = cleaned_frame.to_html(index=False, border=0)
        except Exception:
            cleaned_html = str(html or "").strip()

    cleaned_text = _table_text_from_frame(cleaned_frame)
    return (
        cleaned_html,
        cleaned_text,
        {"rows_removed": rows_removed, "changed": rows_removed > 0},
    )


def _clean_table_text(
    *,
    text: str,
    cleanup_stats: Counter,
) -> Tuple[str, Dict[str, Any]]:
    lines = [line.rstrip() for line in _BREAK_RE.sub("\n", str(text or "")).splitlines()]
    if not lines:
        return ("", {"rows_removed": 0, "changed": False})

    kept_lines: List[str] = []
    seen_keys: Set[str] = set()
    rows_removed = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if set(stripped.replace("|", "").strip()) <= {"-", ":"}:
            if kept_lines:
                kept_lines.append(stripped)
            continue

        row_key = _repeat_key(stripped)
        reason = _table_row_noise_reason(_normalized_text(stripped))
        if reason:
            rows_removed += 1
            cleanup_stats["table_rows_removed"] += 1
            continue
        if row_key and row_key in seen_keys:
            rows_removed += 1
            cleanup_stats["table_rows_removed"] += 1
            continue

        if row_key:
            seen_keys.add(row_key)
        kept_lines.append(stripped)

    return ("\n".join(kept_lines).strip(), {"rows_removed": rows_removed, "changed": rows_removed > 0})


def _clean_element_content(
    *,
    element: Dict[str, Any],
    category: str,
    fragment_repeat_index: Dict[str, Set[int]],
    band_fragment_index: Dict[str, Set[int]],
    vertical_ratio: Optional[float],
    cleanup_stats: Counter,
) -> Dict[str, Any]:
    cleaned = dict(element)
    metadata = cleaned.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}

    text = element_text(cleaned)
    if category == "Table":
        html = str(metadata.get("text_as_html") or "").strip()
        if html:
            cleaned_html, cleaned_text, table_cleanup = _clean_table_html(
                html=html,
                cleanup_stats=cleanup_stats,
            )
            if table_cleanup.get("changed"):
                metadata["text_as_html"] = cleaned_html
                cleaned["text"] = cleaned_text
                metadata["cleanup"] = {
                    **dict(metadata.get("cleanup") or {}),
                    "table_rows_removed": int(table_cleanup.get("rows_removed") or 0),
                }
                cleaned["metadata"] = metadata
                cleanup_stats["elements_cleaned"] += 1
            return cleaned

        cleaned_text, table_cleanup = _clean_table_text(
            text=text,
            cleanup_stats=cleanup_stats,
        )
        if table_cleanup.get("changed"):
            cleaned["text"] = cleaned_text
            metadata["cleanup"] = {
                **dict(metadata.get("cleanup") or {}),
                "table_rows_removed": int(table_cleanup.get("rows_removed") or 0),
            }
            cleaned["metadata"] = metadata
            cleanup_stats["elements_cleaned"] += 1
        return cleaned

    fragments = _split_text_fragments(text)
    if not fragments:
        return cleaned

    kept_fragments: List[str] = []
    removed_fragments = 0

    for fragment in fragments:
        clean_fragment = _normalized_text(fragment)
        key = _repeat_key(fragment)
        repeat_page_count = len(fragment_repeat_index.get(key, set()))
        band_repeat_page_count = len(band_fragment_index.get(key, set()))

        reason = _fragment_discard_reason(
            clean_text=clean_fragment,
            repeat_page_count=repeat_page_count,
            band_repeat_page_count=band_repeat_page_count,
            vertical_ratio=vertical_ratio,
        )
        if reason is not None:
            removed_fragments += 1
            cleanup_stats["text_fragments_removed"] += 1
            continue

        kept_fragments.append(fragment.strip())

    if removed_fragments:
        cleaned["text"] = "\n".join(fragment for fragment in kept_fragments if fragment).strip()
        metadata["cleanup"] = {
            **dict(metadata.get("cleanup") or {}),
            "text_fragments_removed": removed_fragments,
        }
        cleaned["metadata"] = metadata
        cleanup_stats["elements_cleaned"] += 1

    return cleaned


def _build_repeat_index(elements: Iterable[Dict[str, Any]]) -> Dict[str, Set[int]]:
    repeat_pages: Dict[str, Set[int]] = defaultdict(set)
    for raw_element in elements or []:
        if not isinstance(raw_element, dict):
            continue
        text = element_text(raw_element)
        key = _repeat_key(text)
        if not key:
            continue
        repeat_pages[key].add(element_page(raw_element))
    return repeat_pages


def _discard_reason(
    *,
    category: str,
    text: str,
    repeat_page_count: int,
    vertical_ratio: Optional[float],
    table_row_count: int = 0,
    table_column_count: int = 0,
) -> Optional[str]:
    clean_text = _normalized_text(text)
    if category in DISCARD_CATEGORIES:
        return category or "Unknown"

    if category in KEEP_CATEGORIES and len(clean_text) < MIN_TEXT_LENGTH:
        return f"Too Short (<{MIN_TEXT_LENGTH} chars)"

    if _looks_like_image_placeholder(clean_text):
        return "Image Placeholder"

    if _looks_like_page_marker(clean_text):
        return "Page Marker"

    if _looks_like_signature_noise(clean_text) and category != "Table":
        return "Signature / Stamp Noise"

    if _looks_like_repeated_header_footer(
        category=category,
        clean_text=clean_text,
        repeat_page_count=repeat_page_count,
        vertical_ratio=vertical_ratio,
        table_row_count=table_row_count,
        table_column_count=table_column_count,
    ):
        return "Repeated Header/Footer Boilerplate"

    return None


def filter_element_dicts(elements: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    source_elements = [item for item in elements or [] if isinstance(item, dict)]
    repeat_index = _build_repeat_index(source_elements)
    fragment_repeat_index, band_fragment_index = _build_fragment_indexes(source_elements)

    filtered_elements: List[Dict[str, Any]] = []
    removed_elements: List[Dict[str, Any]] = []
    discard_stats = Counter()
    kept_stats = Counter()
    cleanup_stats = Counter()
    page_stats: Dict[int, Dict[str, int]] = {}

    for raw_element in source_elements:
        category = element_category(raw_element)
        page_number = element_page(raw_element)
        vertical_ratio = _vertical_ratio(raw_element)

        page_entry = page_stats.setdefault(
            page_number,
            {"raw": 0, "kept": 0, "removed": 0},
        )
        page_entry["raw"] += 1

        element = _clean_element_content(
            element=dict(raw_element),
            category=category,
            fragment_repeat_index=fragment_repeat_index,
            band_fragment_index=band_fragment_index,
            vertical_ratio=vertical_ratio,
            cleanup_stats=cleanup_stats,
        )

        text = element_text(element)
        repeat_page_count = len(repeat_index.get(_repeat_key(text), set()))
        metadata = element.get("metadata") if isinstance(element, dict) else None
        if not isinstance(metadata, dict):
            metadata = {}
        table_row_count, table_column_count = _table_dimensions(text, metadata) if category == "Table" else (0, 0)

        reason = _discard_reason(
            category=category,
            text=text,
            repeat_page_count=repeat_page_count,
            vertical_ratio=vertical_ratio,
            table_row_count=table_row_count,
            table_column_count=table_column_count,
        )
        if reason is None and category in KEEP_CATEGORIES:
            filtered_elements.append(element)
            kept_stats[category] += 1
            page_entry["kept"] += 1
            continue

        if reason is None:
            reason = category or "Unknown"

        discard_stats[reason] += 1
        page_entry["removed"] += 1
        removed_elements.append(
            {
                **element,
                "_preview_category": category,
                "_discard_reason": reason,
                "_repeat_page_count": repeat_page_count,
                "_vertical_ratio": round(vertical_ratio, 4) if vertical_ratio is not None else None,
                "_table_row_count": table_row_count or None,
                "_table_column_count": table_column_count or None,
            }
        )

    summary = {
        "filter_version": FILTER_VERSION,
        "raw_count": sum(item["raw"] for item in page_stats.values()),
        "kept_count": len(filtered_elements),
        "removed_count": len(removed_elements),
        "kept_breakdown": dict(kept_stats),
        "removed_breakdown": dict(discard_stats),
        "cleanup_breakdown": {
            "elements_cleaned": int(cleanup_stats.get("elements_cleaned", 0)),
            "text_fragments_removed": int(cleanup_stats.get("text_fragments_removed", 0)),
            "table_rows_removed": int(cleanup_stats.get("table_rows_removed", 0)),
        },
        "page_stats": {
            str(page): stats
            for page, stats in sorted(page_stats.items(), key=lambda item: item[0])
        },
        "keep_categories": list(KEEP_CATEGORIES),
        "discard_categories": list(DISCARD_CATEGORIES),
        "min_text_length": MIN_TEXT_LENGTH,
        "repeat_header_footer_min_pages": HEADER_FOOTER_REPEAT_MIN_PAGES,
    }

    return {
        "filtered_elements": filtered_elements,
        "removed_elements": removed_elements,
        "summary": summary,
    }
