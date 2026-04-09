from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from backend.rag.filtering import element_category, element_page, element_text


GROUPING_VERSION = 3

TEXT_CATEGORIES = {"Title", "NarrativeText", "ListItem", "UncategorizedText"}
HEADING_FRAGMENT_CATEGORIES = {"Title", "NarrativeText", "UncategorizedText"}
INLINE_HEADING_PHASE_RE = re.compile(r"^\s*phase\s+\d+\b", re.IGNORECASE)
PAGE_MARKER_RE = re.compile(
    r"^\s*(?:page|sheet)\s+\d+(?:\s*(?:of|/)\s*\d+)?\s*$",
    re.IGNORECASE,
)
LEGAL_FOOTER_RE = re.compile(
    r"document is property of|shall neither be shown to third parties|all rights reserved|confidential",
    re.IGNORECASE,
)
NOISE_LINE_RE = re.compile(
    r"company document id|sheet of sheets|revision(?:\s+number)?|status|validity",
    re.IGNORECASE,
)
LABEL_LEAK_RE = re.compile(
    r"^\s*(?:narrativetext|listitem|uncategorizedtext|title|figurecaption|caption|image|picture|table|text)\s*:?\s*$",
    re.IGNORECASE,
)
FIGURE_LABEL_ONLY_RE = re.compile(
    r"^\s*(?:figure|fig\.?)\s*\d+[A-Za-z]?\s*[:.\-]?\s*$",
    re.IGNORECASE,
)
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+•◦▪‣∙]|\d+[.)]|[A-Za-z][.)])\s+")
TABLE_ROW_RE = re.compile(r"\|")
MULTISPACE_RE = re.compile(r"\s+")
HEADING_SENTENCE_PUNCT_RE = re.compile(r"[.!?]\s*$")
BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+•◦▪‣∙])\s*")
BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
HEADING_CONTINUATION_START_RE = re.compile(r"^[,.;:)\]%]")


def _normalized_text(value: Any) -> str:
    text = str(value or "")
    text = BREAK_RE.sub("\n", text)
    text = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    return text


def _normalize_line(line: Any) -> str:
    text = _normalized_text(line)
    text = text.replace("Ã¢â‚¬Â¢", "- ")
    text = BULLET_PREFIX_RE.sub("- ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _word_count(text: Any) -> int:
    return len(re.findall(r"\S+", str(text or "")))


def _looks_like_table_row(text: str) -> bool:
    value = str(text or "").strip()
    return value.count("|") >= 2 or bool(TABLE_ROW_RE.search(value) and len(value.split("|")) >= 3)


def _looks_like_list_item(text: str) -> bool:
    return bool(LIST_ITEM_RE.match(str(text or "")))


def _line_cleanup_reason(text: str) -> Optional[str]:
    value = _normalize_line(text)
    if not value:
        return None
    if LABEL_LEAK_RE.match(value):
        return "label_leak"
    if PAGE_MARKER_RE.match(value):
        return "noise"
    if NOISE_LINE_RE.search(value):
        return "noise"
    if LEGAL_FOOTER_RE.search(value):
        return "noise"
    return None


def _merge_bbox_strings(bbox_values: Sequence[str]) -> str:
    points: List[Tuple[float, float]] = []
    for raw_bbox in bbox_values:
        candidate = str(raw_bbox or "").strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                points.append((float(item[0]), float(item[1])))
            except Exception:
                continue

    if not points:
        return ""

    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    merged = [
        [min_x, min_y],
        [max_x, min_y],
        [max_x, max_y],
        [min_x, max_y],
    ]
    return json.dumps(merged)


def _extract_bbox(element: Dict[str, Any]) -> str:
    metadata = element.get("metadata") if isinstance(element, dict) else None
    if not isinstance(metadata, dict):
        return ""

    bbox = metadata.get("bbox")
    if isinstance(bbox, str) and bbox.strip():
        return bbox
    if bbox:
        try:
            return json.dumps(bbox)
        except Exception:
            return ""

    coordinates = metadata.get("coordinates")
    if isinstance(coordinates, dict):
        try:
            return json.dumps(coordinates.get("points") or coordinates)
        except Exception:
            return ""

    return ""


def _element_identifier(element: Dict[str, Any], index: int) -> str:
    element_id = str(element.get("element_id") or "").strip()
    return element_id or f"idx:{index}"


def _should_merge_ocr_lines(previous_line: str, current_line: str) -> bool:
    previous = str(previous_line or "").rstrip()
    current = str(current_line or "").lstrip()

    if not previous or not current:
        return False
    if _looks_like_list_item(current) or _looks_like_table_row(current):
        return False
    if previous.endswith(":"):
        return False
    if previous.endswith((".", "!", "?", ";")):
        return False
    if previous.endswith("-") and re.search(r"[A-Za-z]-$", previous) and re.match(r"^[A-Za-z]", current):
        return True
    if current[:1].islower():
        return True
    if current.startswith(("(", "[", "%", ",", ".", ";", ":", ")")):
        return True
    return len(previous) < 100


def _merge_text_fragments(previous_text: str, current_text: str) -> str:
    previous = str(previous_text or "").rstrip()
    current = str(current_text or "").lstrip()
    if previous.endswith("-") and re.search(r"[A-Za-z]-$", previous) and re.match(r"^[A-Za-z]", current):
        return f"{previous[:-1]}{current}"
    return f"{previous} {current}".strip()


def _split_clean_lines(
    raw_text: str,
    *,
    category: str,
    stats: Optional[Dict[str, int]] = None,
) -> Tuple[List[str], int]:
    normalized = _normalized_text(raw_text)
    cleaned_lines: List[str] = []
    noise_removed = 0

    for raw_line in normalized.split("\n"):
        line = _normalize_line(raw_line)
        if not line:
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        cleanup_reason = _line_cleanup_reason(line)
        if cleanup_reason is not None:
            noise_removed += 1
            if stats is not None:
                if cleanup_reason == "label_leak":
                    stats["label_lines_removed"] = int(stats.get("label_lines_removed", 0)) + 1
                else:
                    stats["noise_lines_removed"] = int(stats.get("noise_lines_removed", 0)) + 1
            continue
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[0] == "":
        cleaned_lines.pop(0)
    while cleaned_lines and cleaned_lines[-1] == "":
        cleaned_lines.pop()

    if category == "ListItem":
        normalized_list_lines = []
        for line in cleaned_lines:
            if not line:
                continue
            if _looks_like_list_item(line):
                text = LIST_ITEM_RE.sub("", line).strip()
            else:
                text = line.strip()
            normalized_list_lines.append(f"- {text}".strip())
        return normalized_list_lines, noise_removed

    return cleaned_lines, noise_removed


def _lines_to_units(
    lines: Sequence[str],
    *,
    category: str,
    stats: Optional[Dict[str, int]] = None,
) -> List[Dict[str, str]]:
    if not lines:
        return []

    if category == "ListItem":
        items: List[Dict[str, str]] = []
        current_item = ""
        for line in lines:
            if not line:
                if current_item:
                    items.append({"kind": "list", "text": current_item})
                    current_item = ""
                continue
            normalized_line = line
            if _looks_like_list_item(normalized_line):
                normalized_line = LIST_ITEM_RE.sub("", normalized_line).strip()
                if current_item:
                    items.append({"kind": "list", "text": current_item})
                current_item = f"- {normalized_line}".strip()
                continue

            if not current_item:
                current_item = f"- {normalized_line}".strip()
                continue

            merged_item = _merge_text_fragments(current_item, normalized_line)
            if merged_item != current_item and stats is not None:
                stats["merged_text_fragments"] = int(stats.get("merged_text_fragments", 0)) + 1
            current_item = merged_item

        if current_item:
            items.append({"kind": "list", "text": current_item})
        return items

    blocks: List[Dict[str, str]] = []
    current_paragraph = ""

    for line in lines:
        if not line:
            if current_paragraph:
                blocks.append({"kind": "paragraph", "text": current_paragraph})
                current_paragraph = ""
            continue

        if _looks_like_list_item(line):
            if current_paragraph:
                blocks.append({"kind": "paragraph", "text": current_paragraph})
                current_paragraph = ""
            list_text = line if line.startswith("- ") else f"- {LIST_ITEM_RE.sub('', line).strip()}"
            blocks.append({"kind": "list", "text": list_text.strip()})
            continue

        if _looks_like_table_row(line):
            if current_paragraph:
                blocks.append({"kind": "paragraph", "text": current_paragraph})
                current_paragraph = ""
            blocks.append({"kind": "paragraph", "text": line})
            continue

        if current_paragraph and _should_merge_ocr_lines(current_paragraph, line):
            if stats is not None:
                stats["merged_text_fragments"] = int(stats.get("merged_text_fragments", 0)) + 1
            current_paragraph = _merge_text_fragments(current_paragraph, line)
        else:
            if current_paragraph:
                blocks.append({"kind": "paragraph", "text": current_paragraph})
            current_paragraph = line

    if current_paragraph:
        blocks.append({"kind": "paragraph", "text": current_paragraph})

    return [block for block in blocks if str(block.get("text") or "").strip()]


def _render_units(units: Sequence[Dict[str, str]]) -> str:
    rendered: List[str] = []
    pending_list: List[str] = []

    def flush_list() -> None:
        nonlocal pending_list
        if pending_list:
            rendered.append("\n".join(pending_list).strip())
            pending_list = []

    for unit in units:
        kind = str(unit.get("kind") or "paragraph")
        text = str(unit.get("text") or "").strip()
        if not text:
            continue
        if kind == "list":
            pending_list.append(text)
            continue
        flush_list()
        rendered.append(text)

    flush_list()
    return "\n\n".join(item for item in rendered if item).strip()


def _append_units(
    target_units: List[Dict[str, str]],
    incoming_units: Sequence[Dict[str, str]],
    *,
    stats: Optional[Dict[str, int]] = None,
) -> None:
    for unit in incoming_units:
        kind = str(unit.get("kind") or "paragraph")
        text = str(unit.get("text") or "").strip()
        if not text:
            continue
        if (
            target_units
            and kind == "paragraph"
            and str(target_units[-1].get("kind") or "") == "paragraph"
            and _should_merge_ocr_lines(str(target_units[-1].get("text") or ""), text)
        ):
            if stats is not None:
                stats["merged_text_fragments"] = int(stats.get("merged_text_fragments", 0)) + 1
            target_units[-1]["text"] = _merge_text_fragments(
                str(target_units[-1].get("text") or ""),
                text,
            )
            continue
        target_units.append({"kind": kind, "text": text})


def _valid_section_label(value: Any) -> str:
    label = MULTISPACE_RE.sub(" ", str(value or "")).strip()
    if not label:
        return ""
    lowered = label.lower()
    if lowered in {"unknown", "n/a", "na", "text"}:
        return ""
    if PAGE_MARKER_RE.match(label):
        return ""
    return label


def _starts_with_lowercase(text: str) -> bool:
    value = str(text or "").strip()
    for char in value:
        if char.isalpha():
            return char.islower()
        if char.isdigit():
            return False
        if char in {'"', "'", "(", "[", "{", "/"}:
            continue
        return False
    return False


def _looks_like_sentence_continuation(text: str) -> bool:
    value = _normalize_line(text)
    if not value:
        return False
    if _starts_with_lowercase(value):
        return True
    if HEADING_CONTINUATION_START_RE.match(value):
        return True
    return False


def _heading_fragment_candidate(text: str) -> bool:
    value = _normalize_line(text)
    if not value:
        return False
    if len(value) > 80:
        return False
    if _looks_like_list_item(value) or _looks_like_table_row(value):
        return False
    if _line_cleanup_reason(value) is not None:
        return False
    if HEADING_SENTENCE_PUNCT_RE.search(value):
        return False
    if _looks_like_sentence_continuation(value):
        return False
    word_count = _word_count(value)
    if word_count < 1 or word_count > 10:
        return False
    return True


def _heading_merge_candidate(text: str) -> bool:
    value = _normalize_line(text)
    if not value:
        return False
    if len(value) > 120:
        return False
    if _looks_like_list_item(value) or _looks_like_table_row(value):
        return False
    if _line_cleanup_reason(value) is not None:
        return False
    if HEADING_SENTENCE_PUNCT_RE.search(value):
        return False
    if _looks_like_sentence_continuation(value):
        return False
    return 1 <= _word_count(value) <= 14


def _is_valid_complete_heading(text: str, *, source_categories: Sequence[str]) -> bool:
    value = _normalize_line(text)
    if not _heading_merge_candidate(value):
        return False
    if INLINE_HEADING_PHASE_RE.match(value):
        return True
    if "Title" in {str(item or "") for item in source_categories} and 1 <= _word_count(value) <= 14:
        return True
    return _title_like_heading(value)


def _title_like_heading(text: str) -> bool:
    value = _normalize_line(text)
    if not value:
        return False
    if len(value) > 80:
        return False
    if _looks_like_list_item(value) or _looks_like_table_row(value) or _line_cleanup_reason(value) is not None:
        return False
    if _looks_like_sentence_continuation(value):
        return False
    if HEADING_SENTENCE_PUNCT_RE.search(value):
        return False
    word_count = _word_count(value)
    if word_count < 2 or word_count > 10:
        return False
    if value.endswith(":"):
        return True
    letters = re.findall(r"[A-Za-z]+", value)
    if not letters:
        return False
    capitalized_tokens = sum(1 for token in letters if token[:1].isupper())
    if capitalized_tokens < 2:
        return False
    return capitalized_tokens >= max(2, len(letters) // 2)


def _text_has_same_page_continuation(filtered_elements: Sequence[Dict[str, Any]], start_index: int, page_number: int) -> bool:
    for next_index in range(start_index + 1, len(filtered_elements)):
        next_item = filtered_elements[next_index]
        next_page = element_page(next_item)
        if next_page != page_number:
            break
        next_category = element_category(next_item)
        if next_category in {"Table", "Image", "Picture"}:
            return False
        if next_category in {"NarrativeText", "ListItem", "UncategorizedText"}:
            return bool(element_text(next_item).strip())
        if next_category == "Title":
            return False
    return False


def _extract_inline_heading(
    filtered_elements: Sequence[Dict[str, Any]],
    element_index: int,
    page_number: int,
    category: str,
    units: Sequence[Dict[str, str]],
) -> Tuple[Optional[str], List[Dict[str, str]], bool]:
    if category == "ListItem" or not units:
        return None, list(units), False

    first_unit = dict(units[0] or {})
    if str(first_unit.get("kind") or "") != "paragraph":
        return None, list(units), False

    first_text = str(first_unit.get("text") or "").strip()
    if not first_text:
        return None, list(units), False

    continuation_exists = len(units) > 1 or _text_has_same_page_continuation(filtered_elements, element_index, page_number)
    if not continuation_exists:
        return None, list(units), False

    if INLINE_HEADING_PHASE_RE.match(first_text):
        return first_text, list(units[1:]), True

    if _title_like_heading(first_text):
        return first_text.rstrip(":"), list(units[1:]), True

    return None, list(units), False


def _raw_signature(element: Dict[str, Any]) -> Tuple[int, str, str]:
    return (
        element_page(element),
        element_category(element),
        MULTISPACE_RE.sub(" ", element_text(element).strip().lower()),
    )


def _map_filtered_to_raw(
    filtered_elements: Sequence[Dict[str, Any]],
    raw_elements: Sequence[Dict[str, Any]],
) -> Dict[int, int]:
    raw_by_id: Dict[str, int] = {}
    for raw_index, raw_element in enumerate(raw_elements):
        raw_id = str(raw_element.get("element_id") or "").strip()
        if raw_id and raw_id not in raw_by_id:
            raw_by_id[raw_id] = raw_index

    mapping: Dict[int, int] = {}
    raw_cursor = 0

    for filtered_index, filtered_element in enumerate(filtered_elements):
        filtered_id = str(filtered_element.get("element_id") or "").strip()
        if filtered_id and filtered_id in raw_by_id:
            mapping[filtered_index] = raw_by_id[filtered_id]
            raw_cursor = max(raw_cursor, raw_by_id[filtered_id] + 1)
            continue

        target_page, target_category, target_text = _raw_signature(filtered_element)
        chosen_index: Optional[int] = None
        for raw_index in range(raw_cursor, len(raw_elements)):
            raw_element = raw_elements[raw_index]
            raw_page, raw_category, raw_text = _raw_signature(raw_element)
            if raw_page != target_page:
                if chosen_index is not None:
                    break
                continue
            if raw_category != target_category:
                continue
            if target_text and (target_text == raw_text or target_text in raw_text or raw_text in target_text):
                chosen_index = raw_index
                break

        if chosen_index is None:
            for raw_index, raw_element in enumerate(raw_elements):
                raw_page, raw_category, raw_text = _raw_signature(raw_element)
                if raw_page != target_page or raw_category != target_category:
                    continue
                if target_text and (target_text == raw_text or target_text in raw_text or raw_text in target_text):
                    chosen_index = raw_index
                    break

        if chosen_index is not None:
            mapping[filtered_index] = chosen_index
            raw_cursor = max(raw_cursor, chosen_index + 1)

    return mapping


def _select_useful_figure_caption(
    *,
    raw_elements: Sequence[Dict[str, Any]],
    raw_indexes: Sequence[int],
    attached_caption_indexes: set[int],
) -> Optional[Tuple[int, str]]:
    if not raw_elements or not raw_indexes:
        return None

    page_number = element_page(raw_elements[raw_indexes[0]])
    left = max(0, min(raw_indexes) - 2)
    right = min(len(raw_elements) - 1, max(raw_indexes) + 2)
    nearest: Optional[Tuple[int, int, str]] = None

    for raw_index in range(left, right + 1):
        if raw_index in attached_caption_indexes:
            continue
        raw_element = raw_elements[raw_index]
        if element_page(raw_element) != page_number:
            continue
        if element_category(raw_element) != "FigureCaption":
            continue
        caption_text = MULTISPACE_RE.sub(" ", element_text(raw_element)).strip()
        if not caption_text or FIGURE_LABEL_ONLY_RE.match(caption_text):
            continue
        distance = min(abs(raw_index - source_index) for source_index in raw_indexes)
        if nearest is None or distance < nearest[0]:
            nearest = (distance, raw_index, caption_text)

    if nearest is None:
        return None
    return nearest[1], nearest[2]


def _leading_heading_fragment_lines(
    lines: Sequence[str],
    *,
    merged_so_far: str = "",
) -> Tuple[List[str], List[str]]:
    heading_lines: List[str] = []
    merged = _normalize_line(merged_so_far)

    for raw_line in lines:
        line = _normalize_line(raw_line)
        if not line or not _heading_fragment_candidate(line):
            break
        candidate = _normalize_line(f"{merged} {line}".strip()) if merged else line
        if not _heading_merge_candidate(candidate):
            break
        heading_lines.append(line)
        merged = candidate

    return heading_lines, list(lines[len(heading_lines):])


def _entry_starts_heading_candidate(entry: Dict[str, Any]) -> bool:
    category = str(entry.get("category") or "")
    if category not in HEADING_FRAGMENT_CATEGORIES:
        return False
    lines = list(entry.get("cleaned_lines") or [])
    if not lines:
        return False
    heading_lines, _ = _leading_heading_fragment_lines(lines)
    return bool(heading_lines)


def _has_same_page_body_after_sequence(
    prepared_entries: Sequence[Dict[str, Any]],
    *,
    start_index: int,
    page_number: int,
) -> bool:
    for cursor in range(start_index, len(prepared_entries)):
        entry = prepared_entries[cursor]
        if int(entry.get("page_number") or 1) != page_number:
            break

        category = str(entry.get("category") or "")
        if category in {"Image", "Picture", "FigureCaption"}:
            continue
        if category == "Table":
            return True
        if category == "ListItem":
            return bool(entry.get("units"))
        if category in HEADING_FRAGMENT_CATEGORIES:
            if _entry_starts_heading_candidate(entry):
                return False
            return bool(entry.get("units"))

    return False


def _assemble_heading_candidate(
    prepared_entries: Sequence[Dict[str, Any]],
    *,
    start_index: int,
) -> Optional[Dict[str, Any]]:
    if start_index < 0 or start_index >= len(prepared_entries):
        return None

    entry = prepared_entries[start_index]
    category = str(entry.get("category") or "")
    if category not in HEADING_FRAGMENT_CATEGORIES:
        return None

    page_number = int(entry.get("page_number") or 1)
    source_categories: List[str] = [category]
    current_lines = list(entry.get("cleaned_lines") or [])
    if not current_lines:
        return None

    heading_lines, remaining_lines = _leading_heading_fragment_lines(current_lines)
    if not heading_lines:
        return None

    consumed_indexes = [start_index]
    merged_heading = " ".join(heading_lines).strip()
    body_entry_index: Optional[int] = start_index if remaining_lines else None
    cursor = start_index + 1
    total_fragment_count = len(heading_lines)

    while not remaining_lines and cursor < len(prepared_entries):
        next_entry = prepared_entries[cursor]
        if int(next_entry.get("page_number") or 1) != page_number:
            break
        next_category = str(next_entry.get("category") or "")
        if next_category not in HEADING_FRAGMENT_CATEGORIES:
            break

        next_lines = list(next_entry.get("cleaned_lines") or [])
        if not next_lines:
            break

        next_heading_lines, next_remaining_lines = _leading_heading_fragment_lines(
            next_lines,
            merged_so_far=merged_heading,
        )
        if not next_heading_lines:
            break

        merged_heading = _normalize_line(f"{merged_heading} {' '.join(next_heading_lines)}".strip())
        source_categories.append(next_category)
        consumed_indexes.append(cursor)
        total_fragment_count += len(next_heading_lines)
        remaining_lines = next_remaining_lines
        if remaining_lines:
            body_entry_index = cursor
            break
        cursor += 1

    merged_heading = _normalize_line(merged_heading)
    body_exists = bool(remaining_lines) or _has_same_page_body_after_sequence(
        prepared_entries,
        start_index=consumed_indexes[-1] + 1,
        page_number=page_number,
    )
    merged_fragment_count = max(0, total_fragment_count - 1)

    if not _is_valid_complete_heading(merged_heading, source_categories=source_categories):
        downgraded_units = [{"kind": "paragraph", "text": merged_heading}]
        if remaining_lines:
            downgraded_units.extend(
                _lines_to_units(
                    remaining_lines,
                    category=str(prepared_entries[body_entry_index or start_index].get("category") or "NarrativeText"),
                )
            )
        return {
            "candidate": True,
            "accepted": False,
            "heading": merged_heading,
            "consumed_indexes": consumed_indexes,
            "body_entry_index": body_entry_index,
            "body_units": [],
            "downgraded_units": downgraded_units,
            "rejected_reason": "invalid",
            "merged_fragment_count": merged_fragment_count,
            "heading_kind": "title" if "Title" in source_categories else "inline",
        }

    if not body_exists:
        return {
            "candidate": True,
            "accepted": False,
            "heading": merged_heading,
            "consumed_indexes": consumed_indexes,
            "body_entry_index": None,
            "body_units": [],
            "downgraded_units": [{"kind": "paragraph", "text": merged_heading}],
            "rejected_reason": "orphan",
            "merged_fragment_count": merged_fragment_count,
            "heading_kind": "title" if "Title" in source_categories else "inline",
        }

    body_units = []
    if remaining_lines:
        body_units = _lines_to_units(
            remaining_lines,
            category=str(prepared_entries[body_entry_index or start_index].get("category") or "NarrativeText"),
        )

    return {
        "candidate": True,
        "accepted": True,
        "heading": merged_heading,
        "consumed_indexes": consumed_indexes,
        "body_entry_index": body_entry_index,
        "body_units": body_units,
        "downgraded_units": [],
        "rejected_reason": None,
        "merged_fragment_count": merged_fragment_count,
        "heading_kind": "title" if "Title" in source_categories else "inline",
    }


def _make_block_id(page_number: int, section: str, source_element_ids: Sequence[str], content: str) -> str:
    seed = "::".join(
        [
            str(page_number or 0),
            str(section or ""),
            "|".join(str(item or "") for item in source_element_ids),
            str(content or ""),
        ]
    )
    return hashlib.md5(seed.encode("utf-8")).hexdigest()


def build_grouped_blocks(
    filtered_elements: Iterable[Dict[str, Any]],
    *,
    raw_elements: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    filtered_list = [dict(item) for item in filtered_elements or [] if isinstance(item, dict)]
    raw_list = [dict(item) for item in raw_elements or [] if isinstance(item, dict)]
    filtered_to_raw = _map_filtered_to_raw(filtered_list, raw_list) if raw_list else {}

    blocks: List[Dict[str, Any]] = []
    attached_caption_indexes: set[int] = set()
    pending_heading = ""
    pending_section_seed = ""
    page_stats: Dict[str, Dict[str, int]] = {}
    inline_headings_detected = 0
    figure_captions_attached = 0
    merged_heading_fragments = 0
    heading_candidates_rejected = 0
    orphan_heading_candidates_downgraded = 0
    cleanup_stats: Dict[str, int] = {
        "noise_lines_removed": 0,
        "label_lines_removed": 0,
        "merged_text_fragments": 0,
    }
    prepared_entries: List[Dict[str, Any]] = []

    current_block: Optional[Dict[str, Any]] = None

    def flush_block() -> None:
        nonlocal current_block, figure_captions_attached
        if not current_block:
            return

        units = current_block.get("units") or []
        body = _render_units(units)
        heading = _valid_section_label(current_block.get("heading"))
        if not body:
            current_block = None
            return

        raw_indexes = current_block.get("raw_indexes") or []
        if raw_indexes:
            caption_match = _select_useful_figure_caption(
                raw_elements=raw_list,
                raw_indexes=raw_indexes,
                attached_caption_indexes=attached_caption_indexes,
            )
            if caption_match is not None:
                caption_index, caption_text = caption_match
                body = f"{body}\n\n{caption_text}".strip()
                attached_caption_indexes.add(caption_index)
                figure_captions_attached += 1

        content = body
        if heading:
            content = f"### {heading}\n\n{body}".strip()

        source_element_ids = list(current_block.get("source_element_ids") or [])
        page_number = int(current_block.get("page_number") or 1)
        section = _valid_section_label(current_block.get("section")) or heading or "General / Introduction"
        bbox = _merge_bbox_strings(current_block.get("bbox_values") or [])
        block = {
            "block_id": _make_block_id(page_number, section, source_element_ids, content),
            "page_number": page_number,
            "section": section,
            "heading": heading or None,
            "content": content,
            "source_element_ids": source_element_ids,
            "source_categories": list(current_block.get("source_categories") or []),
            "bbox": bbox,
        }
        blocks.append(block)

        page_key = str(page_number)
        page_entry = page_stats.setdefault(page_key, {"input_text_elements": 0, "output_blocks": 0})
        page_entry["output_blocks"] += 1
        current_block = None

    for filtered_index, element in enumerate(filtered_list):
        category = element_category(element)
        page_number = int(element_page(element) or 1)
        page_key = str(page_number)
        if category in TEXT_CATEGORIES:
            page_stats.setdefault(page_key, {"input_text_elements": 0, "output_blocks": 0})
            page_stats[page_key]["input_text_elements"] += 1

        cleaned_lines: List[str] = []
        units: List[Dict[str, str]] = []
        if category in TEXT_CATEGORIES:
            cleaned_lines, _ = _split_clean_lines(
                element_text(element),
                category=category,
                stats=cleanup_stats,
            )
            units = _lines_to_units(cleaned_lines, category=category, stats=cleanup_stats)

        prepared_entries.append(
            {
                "index": filtered_index,
                "element": element,
                "category": category,
                "page_number": page_number,
                "source_id": _element_identifier(element, filtered_index),
                "bbox_value": _extract_bbox(element),
                "raw_index": filtered_to_raw.get(filtered_index),
                "cleaned_lines": cleaned_lines,
                "units": units,
                "explicit_section": _valid_section_label((element.get("metadata") or {}).get("section")),
            }
        )

    def append_prepared_units(
        *,
        source_indexes: Sequence[int],
        units: Sequence[Dict[str, str]],
        page_number: int,
        effective_section: str,
    ) -> None:
        nonlocal current_block, pending_heading
        if not units:
            return

        if current_block and (
            int(current_block.get("page_number") or 1) != page_number
            or _valid_section_label(current_block.get("section")) != effective_section
        ):
            flush_block()

        if current_block is None:
            current_block = {
                "page_number": page_number,
                "section": effective_section,
                "heading": _valid_section_label(pending_heading),
                "units": [],
                "source_element_ids": [],
                "source_categories": [],
                "bbox_values": [],
                "raw_indexes": [],
            }
            pending_heading = ""

        current_block["section"] = effective_section
        for source_index in source_indexes:
            if source_index < 0 or source_index >= len(prepared_entries):
                continue
            source_entry = prepared_entries[source_index]
            current_block["source_element_ids"].append(str(source_entry.get("source_id") or ""))
            current_block["source_categories"].append(str(source_entry.get("category") or ""))
            bbox_value = str(source_entry.get("bbox_value") or "").strip()
            if bbox_value:
                current_block["bbox_values"].append(bbox_value)
            raw_index = source_entry.get("raw_index")
            if raw_index is not None:
                current_block["raw_indexes"].append(raw_index)

        _append_units(current_block["units"], units, stats=cleanup_stats)

    skipped_indexes: set[int] = set()

    for prepared in prepared_entries:
        entry_index = int(prepared.get("index") or 0)
        if entry_index in skipped_indexes:
            continue

        category = str(prepared.get("category") or "")
        page_number = int(prepared.get("page_number") or 1)

        if category in {"Table", "Image", "Picture"}:
            flush_block()
            continue

        if category not in TEXT_CATEGORIES:
            flush_block()
            continue

        heading_result = _assemble_heading_candidate(prepared_entries, start_index=entry_index)
        if heading_result and heading_result.get("candidate"):
            merged_heading_fragments += int(heading_result.get("merged_fragment_count") or 0)
            skipped_indexes.update(
                idx for idx in list(heading_result.get("consumed_indexes") or [])[1:] if idx != entry_index
            )

            if heading_result.get("accepted"):
                flush_block()
                pending_heading = _valid_section_label(heading_result.get("heading"))
                pending_section_seed = pending_heading
                if str(heading_result.get("heading_kind") or "") == "inline":
                    inline_headings_detected += 1

                body_units = list(heading_result.get("body_units") or [])
                body_entry_index = heading_result.get("body_entry_index")
                if body_units and body_entry_index is not None:
                    body_entry = prepared_entries[int(body_entry_index)]
                    effective_section = pending_section_seed or str(body_entry.get("explicit_section") or "")
                    if not effective_section and current_block and int(current_block.get("page_number") or 1) == page_number:
                        effective_section = _valid_section_label(current_block.get("section"))
                    if not effective_section:
                        effective_section = "General / Introduction"
                    append_prepared_units(
                        source_indexes=list(heading_result.get("consumed_indexes") or []),
                        units=body_units,
                        page_number=page_number,
                        effective_section=effective_section,
                    )
                continue

            rejected_reason = str(heading_result.get("rejected_reason") or "")
            if rejected_reason == "orphan":
                orphan_heading_candidates_downgraded += 1
            elif rejected_reason:
                heading_candidates_rejected += 1

            downgraded_units = list(heading_result.get("downgraded_units") or [])
            effective_section = pending_section_seed or str(prepared.get("explicit_section") or "")
            if not effective_section and current_block and int(current_block.get("page_number") or 1) == page_number:
                effective_section = _valid_section_label(current_block.get("section"))
            if not effective_section:
                effective_section = "General / Introduction"
            append_prepared_units(
                source_indexes=list(heading_result.get("consumed_indexes") or [entry_index]),
                units=downgraded_units,
                page_number=page_number,
                effective_section=effective_section,
            )
            continue

        units = list(prepared.get("units") or [])
        if not units:
            continue

        explicit_section = str(prepared.get("explicit_section") or "")
        effective_section = pending_section_seed or explicit_section
        if not effective_section and current_block and int(current_block.get("page_number") or 1) == page_number:
            effective_section = _valid_section_label(current_block.get("section"))
        if not effective_section:
            effective_section = "General / Introduction"

        append_prepared_units(
            source_indexes=[entry_index],
            units=units,
            page_number=page_number,
            effective_section=effective_section,
        )

    flush_block()

    summary = {
        "grouping_version": GROUPING_VERSION,
        "input_filtered_count": len(filtered_list),
        "output_block_count": len(blocks),
        "noise_lines_removed": int(cleanup_stats.get("noise_lines_removed", 0)),
        "label_lines_removed": int(cleanup_stats.get("label_lines_removed", 0)),
        "merged_text_fragments": int(cleanup_stats.get("merged_text_fragments", 0)),
        "merged_heading_fragments": int(merged_heading_fragments),
        "heading_candidates_rejected": int(heading_candidates_rejected),
        "orphan_heading_candidates_downgraded": int(orphan_heading_candidates_downgraded),
        "inline_headings_detected": inline_headings_detected,
        "figure_captions_attached": figure_captions_attached,
        "page_stats": page_stats,
    }

    return {"blocks": blocks, "summary": summary}
