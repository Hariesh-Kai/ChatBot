from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag


_CAPTION_LIKE_RE = re.compile(r"^\s*(?:table|tab\.?)\b", re.IGNORECASE)
_UNIT_HINT_RE = re.compile(r"\bunits?\b\s*[:\-]\s*([^)]+?)(?:\)|$)", re.IGNORECASE)
_CONTINUATION_RE = re.compile(r"\b(?:continued|cont\.?|contd\.?)\b", re.IGNORECASE)


def _stable_table_id(table_id: Optional[str], html: str) -> str:
    explicit = str(table_id or "").strip()
    if explicit:
        return explicit
    digest = hashlib.md5(str(html or "").encode("utf-8")).hexdigest()  # nosec B324
    return f"table_{digest[:12]}"


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
        if parsed > 0:
            return parsed
    except Exception:
        pass
    return default


def _cell_text(cell: Tag) -> str:
    for br in cell.find_all("br"):
        br.replace_with("\n")
    text = cell.get_text(separator="\n")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _candidate_text(node: Any) -> str:
    if not isinstance(node, Tag):
        return ""
    return node.get_text(separator=" ", strip=True)


def _clean_optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _first_explicit_units(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        match = _UNIT_HINT_RE.search(text)
        if match:
            units = str(match.group(1) or "").strip()
            if units:
                return units
    return None


def _is_units_only_text(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(_UNIT_HINT_RE.fullmatch(text.rstrip(")")))


def _extract_caption_and_context(
    soup: BeautifulSoup,
    table: Tag,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    caption = _candidate_text(table.find("caption", recursive=False))
    if caption:
        units_match = _UNIT_HINT_RE.search(caption)
        return (
            caption,
            units_match.group(1).strip() if units_match else None,
            None,
        )

    figure = table.find_parent("figure")
    figcaption = _candidate_text(figure.find("figcaption", recursive=False)) if isinstance(figure, Tag) else ""
    if figcaption:
        units_match = _UNIT_HINT_RE.search(figcaption)
        return (
            figcaption,
            units_match.group(1).strip() if units_match else None,
            None,
        )

    previous_context = ""
    previous = table.previous_sibling
    while previous is not None:
        candidate = _candidate_text(previous)
        if candidate:
            previous_context = candidate
            if _CAPTION_LIKE_RE.match(candidate):
                units_match = _UNIT_HINT_RE.search(candidate)
                return (
                    candidate,
                    units_match.group(1).strip() if units_match else None,
                    None,
                )
            break
        previous = previous.previous_sibling

    next_context = ""
    following = table.next_sibling
    while following is not None:
        candidate = _candidate_text(following)
        if candidate:
            next_context = candidate
            break
        following = following.next_sibling

    return (None, None, previous_context or next_context or None)


def extract_explicit_table_signals(
    html: str,
    *,
    nearby_text: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Optional[str]]:
    payload = str(html or "").strip()
    above_text = _clean_optional_text((nearby_text or {}).get("above"))
    below_text = _clean_optional_text((nearby_text or {}).get("below"))
    if not payload:
        return {
            "caption": None,
            "units": _first_explicit_units(above_text, below_text),
            "context": (
                above_text
                if above_text and not _is_units_only_text(above_text) and not _CAPTION_LIKE_RE.match(above_text)
                else (
                    below_text
                    if below_text and not _is_units_only_text(below_text) and not _CAPTION_LIKE_RE.match(below_text)
                    else None
                )
            ),
        }

    soup = BeautifulSoup(payload, "html.parser")
    table = soup.find("table")
    if table is None and soup.find("tr") is not None:
        soup = BeautifulSoup(f"<table>{payload}</table>", "html.parser")
        table = soup.find("table")
    if table is None:
        return {
            "caption": None,
            "units": _first_explicit_units(above_text, below_text),
            "context": (
                above_text
                if above_text and not _is_units_only_text(above_text) and not _CAPTION_LIKE_RE.match(above_text)
                else (
                    below_text
                    if below_text and not _is_units_only_text(below_text) and not _CAPTION_LIKE_RE.match(below_text)
                    else None
                )
            ),
        }

    caption, units, context = _extract_caption_and_context(soup, table)
    caption = _clean_optional_text(caption)
    units = _clean_optional_text(units)
    context = _clean_optional_text(context)

    if caption is None:
        for candidate in (above_text, below_text):
            if candidate and _CAPTION_LIKE_RE.match(candidate):
                caption = candidate
                if units is None:
                    units = _first_explicit_units(candidate)
                break

    if context is None:
        for candidate in (above_text, below_text):
            if (
                candidate
                and candidate != caption
                and not _CAPTION_LIKE_RE.match(candidate)
                and not _is_units_only_text(candidate)
            ):
                context = candidate
                break

    if units is None:
        units = _first_explicit_units(caption, context, above_text, below_text)

    return {
        "caption": caption,
        "units": units,
        "context": context,
    }


def _ordered_rows(table: Tag) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for child in table.children:
        if not isinstance(child, Tag):
            continue

        name = child.name.lower()
        if name in {"caption", "colgroup"}:
            continue

        if name == "tr":
            cell_tags = child.find_all(["th", "td"], recursive=False)
            if cell_tags:
                rows.append({"section": "tbody", "cells": cell_tags})
            continue

        if name in {"thead", "tbody", "tfoot"}:
            for tr in child.find_all("tr", recursive=False):
                cell_tags = tr.find_all(["th", "td"], recursive=False)
                if cell_tags:
                    rows.append({"section": name, "cells": cell_tags})
            continue

    if rows:
        return rows

    for tr in table.find_all("tr"):
        cell_tags = tr.find_all(["th", "td"], recursive=False)
        if cell_tags:
            rows.append({"section": "tbody", "cells": cell_tags})
    return rows


def _expanded_grid(rows: Sequence[Dict[str, Any]]) -> List[List[Optional[Dict[str, Any]]]]:
    grid_map: Dict[Tuple[int, int], Dict[str, Any]] = {}
    max_col = 0

    for row_index, row_info in enumerate(rows):
        column_index = 0
        for cell_index, cell in enumerate(row_info.get("cells") or []):
            while (row_index, column_index) in grid_map:
                column_index += 1

            rowspan = _safe_int(cell.get("rowspan"), 1)
            colspan = _safe_int(cell.get("colspan"), 1)
            origin_id = f"r{row_index}c{cell_index}"
            slot_base = {
                "origin_id": origin_id,
                "text": _cell_text(cell),
                "tag": cell.name.lower(),
                "section": str(row_info.get("section") or "tbody"),
                "source_row_index": row_index,
                "source_cell_index": cell_index,
                "rowspan": rowspan,
                "colspan": colspan,
            }

            for row_offset in range(rowspan):
                for col_offset in range(colspan):
                    grid_map[(row_index + row_offset, column_index + col_offset)] = {
                        **slot_base,
                        "is_origin": row_offset == 0 and col_offset == 0,
                    }

            column_index += colspan
            max_col = max(max_col, column_index)

    max_row = (max((row for row, _ in grid_map.keys()), default=-1) + 1) if grid_map else 0
    return [
        [grid_map.get((row_index, column_index)) for column_index in range(max_col)]
        for row_index in range(max_row)
    ]


def _detect_header_row_count(rows: Sequence[Dict[str, Any]]) -> int:
    if not rows:
        return 0

    explicit = 0
    for row in rows:
        if str(row.get("section") or "").lower() == "thead":
            explicit += 1
        elif explicit:
            break
        else:
            break
    if explicit:
        return explicit

    detected = 0
    for row in rows:
        cells = row.get("cells") or []
        if not cells:
            break
        if all(getattr(cell, "name", "").lower() == "th" for cell in cells):
            detected += 1
            continue
        break
    return detected


def _column_paths(
    grid: Sequence[Sequence[Optional[Dict[str, Any]]]],
    *,
    header_row_count: int,
) -> List[List[str]]:
    column_count = max((len(row) for row in grid), default=0)
    paths: List[List[str]] = []

    for column_index in range(column_count):
        path: List[str] = []
        previous_origin_id: Optional[str] = None
        for row_index in range(min(header_row_count, len(grid))):
            slot = grid[row_index][column_index] if column_index < len(grid[row_index]) else None
            if not slot:
                continue

            origin_id = str(slot.get("origin_id") or "")
            if origin_id and origin_id == previous_origin_id:
                continue

            label = str(slot.get("text") or "")
            if label:
                path.append(label)
            previous_origin_id = origin_id or previous_origin_id

        paths.append(path)

    return paths


def _build_column_tree(paths: Sequence[Sequence[str]]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    index = 0

    while index < len(paths):
        path = list(paths[index] or [])
        if not path:
            index += 1
            continue

        label = path[0]
        suffixes: List[List[str]] = []
        while index < len(paths):
            current = list(paths[index] or [])
            if not current or current[0] != label:
                break
            suffixes.append(current[1:])
            index += 1

        run_index = 0
        while run_index < len(suffixes):
            suffix = suffixes[run_index]
            if not suffix:
                nodes.append({"name": label, "children": []})
                run_index += 1
                continue

            child_group: List[List[str]] = []
            while run_index < len(suffixes) and suffixes[run_index]:
                child_group.append(suffixes[run_index])
                run_index += 1

            nodes.append(
                {
                    "name": label,
                    "children": _build_column_tree(child_group),
                }
            )

    return nodes


def _row_groups(
    grid: Sequence[Sequence[Optional[Dict[str, Any]]]],
    *,
    header_row_count: int,
    column_paths: Sequence[Sequence[str]],
) -> List[Tuple[int, List[Dict[str, Any]]]]:
    grouped_rows: List[Tuple[int, List[Dict[str, Any]]]] = []
    output_row_index = 1

    for row_index in range(header_row_count, len(grid)):
        row = grid[row_index]
        row_cells: List[Dict[str, Any]] = []
        for column_index, slot in enumerate(row):
            if slot is None:
                continue
            row_cells.append(
                {
                    "row_index": output_row_index,
                    "column_path": list(column_paths[column_index]) if column_index < len(column_paths) else [],
                    "value": str(slot.get("text") or ""),
                }
            )

        if row_cells:
            grouped_rows.append((output_row_index, row_cells))
            output_row_index += 1

    return grouped_rows


def _unique_column_paths(cells: Sequence[Dict[str, Any]]) -> List[List[str]]:
    ordered: List[List[str]] = []
    seen: set[Tuple[str, ...]] = set()
    for cell in cells or []:
        if not isinstance(cell, dict):
            continue
        path = [str(part) for part in list(cell.get("column_path") or []) if str(part)]
        path_key = tuple(path)
        if path_key in seen:
            continue
        seen.add(path_key)
        ordered.append(path)
    return ordered


def _clamp_confidence(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 3)


def _confidence_scores(
    *,
    rows: Sequence[Dict[str, Any]],
    grid: Sequence[Sequence[Optional[Dict[str, Any]]]],
    header_row_count: int,
    cells: Sequence[Dict[str, Any]],
) -> Dict[str, float]:
    if not rows or not grid:
        return {"structure": 0.0, "headers": 0.0}

    explicit_thead = any(
        str(row.get("section") or "").lower() == "thead"
        for row in list(rows)[:header_row_count]
    )
    header_origin_slots = 0
    non_empty_header_slots = 0
    span_slots = 0
    for row_index in range(min(header_row_count, len(grid))):
        for slot in grid[row_index]:
            if not slot or not bool(slot.get("is_origin")):
                continue
            header_origin_slots += 1
            if str(slot.get("text") or "").strip():
                non_empty_header_slots += 1
            if int(slot.get("rowspan") or 1) > 1 or int(slot.get("colspan") or 1) > 1:
                span_slots += 1

    header_confidence = 0.0
    if header_row_count > 0:
        header_confidence = 0.35
        header_confidence += 0.35 if explicit_thead else 0.2
        if header_origin_slots:
            fill_ratio = non_empty_header_slots / header_origin_slots
            if fill_ratio >= 0.9:
                header_confidence += 0.2
            elif fill_ratio >= 0.5:
                header_confidence += 0.1
        if span_slots > 0:
            header_confidence += 0.1

    column_count = max((len(row) for row in grid), default=0)
    body_rows = list(grid[header_row_count:]) if header_row_count < len(grid) else []
    body_row_count = len(body_rows)
    body_filled_counts = [
        sum(1 for slot in row if slot and str(slot.get("text") or "").strip())
        for row in body_rows
    ]
    unique_paths = _unique_column_paths(cells)

    structure_confidence = 0.15
    if column_count > 0:
        structure_confidence += 0.15
    if body_row_count > 0:
        structure_confidence += 0.2
    if any(body_filled_counts):
        structure_confidence += 0.2
    if unique_paths:
        non_empty_path_ratio = (
            sum(1 for path in unique_paths if path) / max(len(unique_paths), 1)
        )
        if non_empty_path_ratio >= 0.9:
            structure_confidence += 0.15
        elif non_empty_path_ratio >= 0.5:
            structure_confidence += 0.075
    if header_row_count > 0:
        structure_confidence += 0.1
    if column_count > 0 and body_filled_counts:
        avg_fill_ratio = sum(count / max(column_count, 1) for count in body_filled_counts) / len(body_filled_counts)
        if avg_fill_ratio >= 0.9:
            structure_confidence += 0.15
        elif avg_fill_ratio >= 0.6:
            structure_confidence += 0.075
    if span_slots > 0:
        structure_confidence += 0.05

    return {
        "structure": _clamp_confidence(structure_confidence),
        "headers": _clamp_confidence(header_confidence),
    }


def filter_normalized_table_rows(
    normalized_table: Dict[str, Any],
    *,
    row_noise_detector=None,
    row_key_builder=None,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    row_noise_detector = row_noise_detector or (lambda _text: None)
    row_key_builder = row_key_builder or (lambda text: text)

    grouped_rows: Dict[int, List[Dict[str, Any]]] = {}
    ordered_row_indexes: List[int] = []
    for cell in normalized_table.get("cells") if isinstance(normalized_table, dict) else []:
        if not isinstance(cell, dict):
            continue
        try:
            row_index = int(cell.get("row_index"))
        except Exception:
            continue
        if row_index not in grouped_rows:
            grouped_rows[row_index] = []
            ordered_row_indexes.append(row_index)
        grouped_rows[row_index].append(
            {
                "row_index": row_index,
                "column_path": list(cell.get("column_path") or []),
                "value": str(cell.get("value") or ""),
            }
        )

    kept_cells: List[Dict[str, Any]] = []
    seen_row_keys: set[str] = set()
    rows_removed = 0
    new_row_index = 1

    for original_row_index in ordered_row_indexes:
        row_cells = grouped_rows.get(original_row_index) or []
        combined_text = " | ".join(
            str(cell.get("value") or "")
            for cell in row_cells
            if str(cell.get("value") or "")
        ).strip()
        row_reason = row_noise_detector(combined_text)
        row_key = str(row_key_builder(combined_text) or "").strip()

        if row_reason or (row_key and row_key in seen_row_keys):
            rows_removed += 1
            continue

        if row_key:
            seen_row_keys.add(row_key)

        for cell in row_cells:
            kept_cells.append(
                {
                    "row_index": new_row_index,
                    "column_path": list(cell.get("column_path") or []),
                    "value": str(cell.get("value") or ""),
                }
            )
        new_row_index += 1

    return (
        {
            "table_id": str(normalized_table.get("table_id") or ""),
            "caption": normalized_table.get("caption"),
            "columns": list(normalized_table.get("columns") or []),
            "cells": kept_cells,
            "metadata": dict(normalized_table.get("metadata") or {}),
        },
        {"rows_removed": rows_removed, "changed": rows_removed > 0},
    )


def merge_normalized_tables(
    primary: Dict[str, Any],
    secondary: Dict[str, Any],
) -> Dict[str, Any]:
    primary_cells = [cell for cell in list(primary.get("cells") or []) if isinstance(cell, dict)]
    secondary_cells = [cell for cell in list(secondary.get("cells") or []) if isinstance(cell, dict)]

    row_offset = max(
        (int(cell.get("row_index") or 0) for cell in primary_cells),
        default=0,
    )
    merged_cells: List[Dict[str, Any]] = list(primary_cells)
    for cell in secondary_cells:
        try:
            secondary_row_index = int(cell.get("row_index") or 0)
        except Exception:
            secondary_row_index = 0
        merged_cells.append(
            {
                "row_index": row_offset + secondary_row_index,
                "column_path": list(cell.get("column_path") or []),
                "value": str(cell.get("value") or ""),
            }
        )

    primary_meta = dict(primary.get("metadata") or {})
    secondary_meta = dict(secondary.get("metadata") or {})
    primary_conf = dict(primary_meta.get("confidence") or {})
    secondary_conf = dict(secondary_meta.get("confidence") or {})

    return {
        "table_id": str(primary.get("table_id") or secondary.get("table_id") or ""),
        "caption": primary.get("caption") if primary.get("caption") is not None else secondary.get("caption"),
        "columns": list(primary.get("columns") or secondary.get("columns") or []),
        "cells": merged_cells,
        "metadata": {
            "units": primary_meta.get("units") if primary_meta.get("units") is not None else secondary_meta.get("units"),
            "context": primary_meta.get("context") if primary_meta.get("context") is not None else secondary_meta.get("context"),
            "confidence": {
                "structure": _clamp_confidence(
                    min(
                        float(primary_conf.get("structure") or 0.0),
                        float(secondary_conf.get("structure") or 0.0),
                    )
                ),
                "headers": _clamp_confidence(
                    min(
                        float(primary_conf.get("headers") or 0.0),
                        float(secondary_conf.get("headers") or 0.0),
                    )
                ),
            },
        },
    }


def normalize_html_table(
    *,
    html: str,
    table_id: Optional[str] = None,
    nearby_text: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    payload = str(html or "").strip()
    stable_id = _stable_table_id(table_id, payload)
    empty_result = {
        "table_id": stable_id,
        "caption": None,
        "columns": [],
        "cells": [],
        "metadata": {
            "units": None,
            "context": None,
            "confidence": {
                "structure": 0.0,
                "headers": 0.0,
            },
        },
    }
    if not payload:
        return empty_result

    explicit_signals = extract_explicit_table_signals(
        payload,
        nearby_text=nearby_text,
    )
    soup = BeautifulSoup(payload, "html.parser")
    table = soup.find("table")
    if table is None and soup.find("tr") is not None:
        soup = BeautifulSoup(f"<table>{payload}</table>", "html.parser")
        table = soup.find("table")
    if table is None:
        return empty_result

    rows = _ordered_rows(table)
    if not rows:
        return {
            **empty_result,
            "caption": explicit_signals.get("caption"),
            "metadata": {
                "units": explicit_signals.get("units"),
                "context": explicit_signals.get("context"),
                "confidence": {
                    "structure": 0.0,
                    "headers": 0.0,
                },
            },
        }

    grid = _expanded_grid(rows)
    header_row_count = _detect_header_row_count(rows)
    paths = _column_paths(grid, header_row_count=header_row_count)
    grouped_rows = _row_groups(
        grid,
        header_row_count=header_row_count,
        column_paths=paths,
    )

    cells: List[Dict[str, Any]] = []
    for _, row_cells in grouped_rows:
        cells.extend(row_cells)
    confidence = _confidence_scores(
        rows=rows,
        grid=grid,
        header_row_count=header_row_count,
        cells=cells,
    )

    return {
        "table_id": stable_id,
        "caption": explicit_signals.get("caption"),
        "columns": _build_column_tree(paths),
        "cells": cells,
        "metadata": {
            "units": explicit_signals.get("units"),
            "context": explicit_signals.get("context"),
            "confidence": confidence,
        },
    }


def normalized_table_to_json(normalized_table: Dict[str, Any]) -> str:
    return json.dumps(normalized_table, ensure_ascii=False, indent=2)


def normalized_table_to_text(normalized_table: Dict[str, Any]) -> str:
    if not isinstance(normalized_table, dict):
        return ""

    cells = normalized_table.get("cells")
    if not isinstance(cells, list) or not cells:
        return ""

    lines: List[str] = []
    caption = normalized_table.get("caption")
    if isinstance(caption, str) and caption.strip():
        lines.append(f"Caption: {caption.strip()}")
    metadata = dict(normalized_table.get("metadata") or {})
    units = metadata.get("units")
    context = metadata.get("context")
    if isinstance(units, str) and units.strip():
        lines.append(f"Units: {units.strip()}")
    if isinstance(context, str) and context.strip():
        lines.append(f"Context: {context.strip()}")

    column_order: List[List[str]] = []
    seen_paths: set[Tuple[str, ...]] = set()
    row_groups: Dict[int, List[Dict[str, Any]]] = {}
    row_order: List[int] = []

    for cell in cells:
        if not isinstance(cell, dict):
            continue
        try:
            row_index = int(cell.get("row_index"))
        except Exception:
            continue

        column_path = [str(part) for part in list(cell.get("column_path") or [])]
        path_key = tuple(column_path)
        if path_key not in seen_paths:
            seen_paths.add(path_key)
            column_order.append(column_path)

        if row_index not in row_groups:
            row_groups[row_index] = []
            row_order.append(row_index)
        row_groups[row_index].append(
            {
                "column_path": column_path,
                "value": str(cell.get("value") or ""),
            }
        )

    if column_order:
        lines.append("Columns:")
        for column_path in column_order:
            lines.append(f"- {json.dumps(column_path, ensure_ascii=False)}")

    lines.append("Rows:")
    for row_index in row_order:
        lines.append(f"Row {row_index}:")
        for cell in row_groups.get(row_index) or []:
            path_json = json.dumps(list(cell.get("column_path") or []), ensure_ascii=False)
            value = str(cell.get("value") or "")
            lines.append(f"- {path_json}: {value}")

    return "\n".join(lines).strip()
