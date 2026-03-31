from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from backend.rag.chunk import ContextAwareChunker
from backend.rag.preprocess import stream_pdf_to_elements
from backend.rag.preprocessor_registry import (
    get_rag_preprocessor_options,
    normalize_rag_preprocessor,
)


def _summarize_elements(elements: List[dict]) -> Dict[str, Any]:
    type_counts: Dict[str, int] = {}
    pages = set()
    total_chars = 0

    for item in elements:
        item_type = str(item.get("type") or item.get("category") or "Unknown")
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
        meta = item.get("metadata") or {}
        try:
            pages.add(int(meta.get("page_number") or 0))
        except Exception:
            pass
        total_chars += len(str(item.get("text") or item.get("content") or ""))

    return {
        "element_count": len(elements),
        "page_count": len([p for p in pages if p > 0]),
        "type_counts": type_counts,
        "title_count": type_counts.get("Title", 0),
        "table_count": type_counts.get("Table", 0),
        "narrative_count": type_counts.get("NarrativeText", 0),
        "total_text_chars": total_chars,
    }


def _summarize_chunks(chunks_path: Path) -> Dict[str, Any]:
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    chunk_lengths = [len(str(item.get("content") or "")) for item in chunks]

    type_counts: Dict[str, int] = {}
    for item in chunks:
        meta = item.get("metadata") or {}
        chunk_type = str(meta.get("type") or "text")
        type_counts[chunk_type] = type_counts.get(chunk_type, 0) + 1

    return {
        "chunk_count": len(chunks),
        "chunk_type_counts": type_counts,
        "avg_chunk_chars": round(statistics.mean(chunk_lengths), 2) if chunk_lengths else 0.0,
        "max_chunk_chars": max(chunk_lengths) if chunk_lengths else 0,
        "min_chunk_chars": min(chunk_lengths) if chunk_lengths else 0,
    }


def _render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Preprocess Comparison Report")
    lines.append("")
    lines.append(f"- Generated At (UTC): `{report['generated_at']}`")
    lines.append(f"- PDF: `{report['pdf_path']}`")
    lines.append(f"- RAG Mode: `{report['rag_mode']}`")
    lines.append("")
    lines.append("| Preprocessor | Seconds | Elements | Pages | Titles | Tables | Chunks | Avg Chunk Chars | Error |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")

    for item in report.get("results", []):
        element_summary = item.get("element_summary") or {}
        chunk_summary = item.get("chunk_summary") or {}
        lines.append(
            f"| {item.get('preprocessor')} | {item.get('elapsed_seconds')} | "
            f"{element_summary.get('element_count')} | {element_summary.get('page_count')} | "
            f"{element_summary.get('title_count')} | {element_summary.get('table_count')} | "
            f"{chunk_summary.get('chunk_count')} | {chunk_summary.get('avg_chunk_chars')} | "
            f"{item.get('error') or ''} |"
        )

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare PDF preprocessing backends before full RAG ingestion.",
    )
    parser.add_argument("--pdf", required=True, help="Path to input PDF.")
    parser.add_argument(
        "--output-dir",
        default=str(Path("backend") / "rag" / "reports"),
        help="Directory where comparison artifacts and reports are written.",
    )
    parser.add_argument(
        "--preprocessors",
        default=",".join(get_rag_preprocessor_options().keys()),
        help="Comma-separated preprocessors to compare.",
    )
    parser.add_argument(
        "--rag-mode",
        default="balanced",
        help="RAG ingest mode used for adaptive preprocess settings.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    requested = [
        normalize_rag_preprocessor(item)
        for item in str(args.preprocessors or "").split(",")
        if str(item).strip()
    ]
    preprocessors: List[str] = []
    for item in requested:
        if item not in preprocessors:
            preprocessors.append(item)

    if not preprocessors:
        preprocessors = ["unstructured", "pypdf_text"]

    report_rows: List[Dict[str, Any]] = []
    run_root = output_dir / f"preprocess_compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=True)

    for preprocessor in preprocessors:
        print(f"[PREPROCESS-COMPARE] Running: {preprocessor}")
        work_dir = run_root / preprocessor
        work_dir.mkdir(parents=True, exist_ok=True)
        elements_path = work_dir / "elements.json"
        chunks_path = work_dir / "chunks.json"

        started = time.time()
        elements: List[dict] = []
        error_message = ""
        try:
            for batch in stream_pdf_to_elements(
                str(pdf_path),
                str(elements_path),
                rag_mode=args.rag_mode,
                pipeline_mode="commit",
                preprocessor=preprocessor,
            ):
                elements.extend(batch)
        except Exception as exc:
            error_message = str(exc)

        if error_message:
            report_rows.append(
                {
                    "preprocessor": preprocessor,
                    "elapsed_seconds": round(time.time() - started, 3),
                    "error": error_message,
                    "element_summary": {},
                    "chunk_summary": {},
                }
            )
            continue

        if not elements:
            report_rows.append(
                {
                    "preprocessor": preprocessor,
                    "elapsed_seconds": round(time.time() - started, 3),
                    "error": "No elements extracted.",
                    "element_summary": {},
                    "chunk_summary": {},
                }
            )
            continue

        elements_path.write_text(json.dumps(elements, indent=2), encoding="utf-8")

        chunker = ContextAwareChunker()
        chunker.process(str(elements_path), str(chunks_path))

        report_rows.append(
            {
                "preprocessor": preprocessor,
                "elapsed_seconds": round(time.time() - started, 3),
                "element_summary": _summarize_elements(elements),
                "chunk_summary": _summarize_chunks(chunks_path),
                "elements_path": str(elements_path),
                "chunks_path": str(chunks_path),
            }
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_path": str(pdf_path),
        "rag_mode": args.rag_mode,
        "available_preprocessors": get_rag_preprocessor_options(),
        "results": report_rows,
    }

    json_path = run_root / "report.json"
    md_path = run_root / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print(f"[PREPROCESS-COMPARE] JSON report: {json_path}")
    print(f"[PREPROCESS-COMPARE] Markdown report: {md_path}")


if __name__ == "__main__":
    main()
