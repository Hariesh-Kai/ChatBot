from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from backend.llm.model_selector import resolve_model_id
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    collection_name_for_preprocessor,
    normalize_collection_name,
)
from backend.rag.pipeline import run_pipeline
from backend.rag.preprocessor_registry import (
    get_rag_preprocessor_options,
    normalize_rag_preprocessor,
)
from backend.rag.regression_runner import run_regression_benchmark


def _load_benchmark_identity(
    benchmark_file: Path,
    *,
    company_document_id: str | None,
    revision_number: str | None,
) -> Tuple[str, str]:
    data = json.loads(benchmark_file.read_text(encoding="utf-8"))
    defaults = data.get("defaults", {}) if isinstance(data, dict) else {}
    raw_cases = data.get("cases", []) if isinstance(data, dict) else data

    distinct_doc_ids = set()
    distinct_revisions = set()

    if isinstance(raw_cases, list):
        for item in raw_cases:
            if not isinstance(item, dict):
                continue
            doc_id = str(
                item.get("company_document_id")
                or defaults.get("company_document_id")
                or ""
            ).strip()
            revision = str(
                item.get("revision_number")
                or defaults.get("revision_number")
                or "1"
            ).strip()
            if doc_id:
                distinct_doc_ids.add(doc_id)
            if revision:
                distinct_revisions.add(revision)

    resolved_doc_id = str(company_document_id or "").strip()
    resolved_revision = str(revision_number or "").strip()

    if not resolved_doc_id:
        if len(distinct_doc_ids) != 1:
            raise SystemExit(
                "Benchmark file must resolve to exactly one company_document_id, "
                "or pass --company-document-id explicitly."
            )
        resolved_doc_id = next(iter(distinct_doc_ids))

    if not resolved_revision:
        if len(distinct_revisions) > 1:
            raise SystemExit(
                "Benchmark file resolves to multiple revisions. "
                "Pass --revision-number explicitly."
            )
        resolved_revision = next(iter(distinct_revisions or {"1"}))

    return resolved_doc_id, resolved_revision


def _render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Preprocessor Benchmark Report")
    lines.append("")
    lines.append(f"- Generated At (UTC): `{report['generated_at']}`")
    lines.append(f"- PDF: `{report['pdf_path']}`")
    lines.append(f"- Benchmark File: `{report['benchmark_file']}`")
    lines.append(f"- Company Document ID: `{report['company_document_id']}`")
    lines.append(f"- Revision Number: `{report['revision_number']}`")
    lines.append(f"- Base Collection: `{report['collection_base']}`")
    lines.append("")
    lines.append("| Preprocessor | Collection | Ingest Seconds | Best Mode | Avg Best Score | Error |")
    lines.append("|---|---|---:|---|---:|---|")

    for item in report.get("results", []):
        summary = item.get("best_mode_summary") or {}
        lines.append(
            "| {preprocessor} | {collection} | {ingest_seconds} | {best_mode} | {best_score} | {error} |".format(
                preprocessor=item.get("preprocessor"),
                collection=item.get("collection_name"),
                ingest_seconds=item.get("ingest_seconds"),
                best_mode=summary.get("mode") or "",
                best_score=summary.get("avg_composite_score") or "",
                error=item.get("error") or "",
            )
        )

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Index the same PDF with multiple preprocessors into isolated "
            "PGVector collections, then run regression scoring for each."
        ),
    )
    parser.add_argument("--pdf", required=True, help="Path to input PDF.")
    parser.add_argument("--benchmark-file", required=True, help="Path to benchmark JSON file.")
    parser.add_argument(
        "--output-dir",
        default=str(Path("backend") / "rag" / "reports"),
        help="Directory where benchmark reports are written.",
    )
    parser.add_argument(
        "--db-connection",
        default="postgresql+psycopg2://postgres:1@localhost:5432/rag_db",
        help="PGVector DB connection string.",
    )
    parser.add_argument(
        "--embedding-model",
        default="BAAI/bge-m3",
        help="Embedding model ID or local path used by regression retrieval.",
    )
    parser.add_argument(
        "--preprocessors",
        default=",".join(get_rag_preprocessor_options().keys()),
        help="Comma-separated preprocessors to index and benchmark.",
    )
    parser.add_argument(
        "--rag-mode",
        default="balanced",
        help="RAG ingest mode used while indexing benchmark collections.",
    )
    parser.add_argument(
        "--modes",
        default="fast,balanced,high_fidelity",
        help="Comma-separated retrieval modes for regression scoring.",
    )
    parser.add_argument(
        "--chat-mode",
        default="lite",
        choices=["lite", "base", "net"],
        help="Model family for answer generation (ignored with --no-generate).",
    )
    parser.add_argument(
        "--no-generate",
        action="store_true",
        help="Skip answer generation and benchmark retrieval only.",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=220,
        help="Generation max_new_tokens when answer generation is enabled.",
    )
    parser.add_argument(
        "--force-detailed",
        action="store_true",
        help="Enable force_detailed retrieval during regression.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Optional cap for number of benchmark cases (0 = all).",
    )
    parser.add_argument(
        "--company-document-id",
        default="",
        help="Override company_document_id for indexing if benchmark inference is ambiguous.",
    )
    parser.add_argument(
        "--revision-number",
        default="",
        help="Override revision_number for indexing if benchmark inference is ambiguous.",
    )
    parser.add_argument(
        "--source-file",
        default="",
        help="Override source_file metadata used during ingest. Defaults to the PDF filename.",
    )
    parser.add_argument(
        "--collection-base",
        default=DEFAULT_RAG_COLLECTION_NAME,
        help="Base collection prefix used to derive per-preprocessor collections.",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip indexing and benchmark existing collections only.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    benchmark_file = Path(args.benchmark_file).resolve()
    if not benchmark_file.exists():
        raise SystemExit(f"Benchmark file not found: {benchmark_file}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    company_document_id, revision_number = _load_benchmark_identity(
        benchmark_file,
        company_document_id=args.company_document_id,
        revision_number=args.revision_number,
    )

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
        preprocessors = list(get_rag_preprocessor_options().keys())

    raw_modes = [m.strip() for m in str(args.modes or "").split(",") if m.strip()]
    if not raw_modes:
        raw_modes = ["fast", "balanced", "high_fidelity"]

    generate_answers = not bool(args.no_generate)
    model_id = resolve_model_id(args.chat_mode) if generate_answers else None
    source_file = str(args.source_file or pdf_path.name).strip() or pdf_path.name
    collection_base = normalize_collection_name(args.collection_base)

    run_root = output_dir / f"preprocessor_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=True)

    report_rows: List[Dict[str, Any]] = []

    for preprocessor in preprocessors:
        collection_name = collection_name_for_preprocessor(
            preprocessor,
            base_collection=collection_base,
        )
        work_dir = run_root / preprocessor
        work_dir.mkdir(parents=True, exist_ok=True)

        row: Dict[str, Any] = {
            "preprocessor": preprocessor,
            "collection_name": collection_name,
            "ingest_seconds": None,
            "regression_seconds": None,
            "best_mode_summary": None,
            "benchmark_json_report": None,
            "benchmark_markdown_report": None,
            "error": None,
        }

        if not args.skip_ingest:
            ingest_started = time.time()
            try:
                for _event in run_pipeline(
                    pdf_path=str(pdf_path),
                    job_dir=str(work_dir / "job"),
                    company_document_id=company_document_id,
                    db_connection=args.db_connection,
                    extra_metadata={
                        "company_document_id": company_document_id,
                        "revision_number": str(revision_number),
                        "source_file": source_file,
                        "rag_preprocessor": preprocessor,
                        "rag_collection_name": collection_name,
                        "replace_existing": True,
                    },
                    mode="commit",
                    rag_mode=args.rag_mode,
                    preprocessor=preprocessor,
                    collection_name=collection_name,
                ):
                    pass
                row["ingest_seconds"] = round(time.time() - ingest_started, 3)
            except Exception as exc:
                row["ingest_seconds"] = round(time.time() - ingest_started, 3)
                row["error"] = f"ingest failed: {exc}"

        regression_started = time.time()
        try:
            regression_report = run_regression_benchmark(
                benchmark_file=benchmark_file,
                output_dir=work_dir,
                db_connection=args.db_connection,
                embedding_model=args.embedding_model,
                modes=raw_modes,
                generate_answers=generate_answers,
                model_id=model_id,
                max_new_tokens=max(16, int(args.max_new_tokens)),
                force_detailed=bool(args.force_detailed),
                max_cases=int(args.max_cases or 0),
                collection_name=collection_name,
            )
            row["regression_seconds"] = round(time.time() - regression_started, 3)
            mode_summaries = list(regression_report.get("mode_summaries") or [])
            if mode_summaries:
                row["best_mode_summary"] = sorted(
                    mode_summaries,
                    key=lambda item: float(item.get("avg_composite_score") or 0.0),
                    reverse=True,
                )[0]
            row["benchmark_json_report"] = regression_report.get("json_report_path")
            row["benchmark_markdown_report"] = regression_report.get("markdown_report_path")
            if row.get("error"):
                row["error"] = f"{row['error']} | benchmark ran on existing collection"
        except Exception as exc:
            row["regression_seconds"] = round(time.time() - regression_started, 3)
            row["error"] = (
                f"{row['error']} | regression failed: {exc}"
                if row.get("error")
                else f"regression failed: {exc}"
            )

        report_rows.append(row)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_path": str(pdf_path),
        "benchmark_file": str(benchmark_file),
        "company_document_id": company_document_id,
        "revision_number": str(revision_number),
        "collection_base": collection_base,
        "rag_mode": args.rag_mode,
        "modes": raw_modes,
        "generate_answers": generate_answers,
        "model_id": model_id,
        "available_preprocessors": get_rag_preprocessor_options(),
        "results": report_rows,
    }

    json_path = run_root / "report.json"
    md_path = run_root / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print(f"[PREPROCESSOR-BENCHMARK] JSON report: {json_path}")
    print(f"[PREPROCESSOR-BENCHMARK] Markdown report: {md_path}")


if __name__ == "__main__":
    main()
