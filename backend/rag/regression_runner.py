from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_postgres import PGVector

from backend.llm.generate import generate_answer_stream
from backend.llm.hf_cache_utils import resolve_local_snapshot
from backend.llm.model_config_store import HF_CACHE_DIR
from backend.llm.model_selector import resolve_model_id
from backend.llm.prompts import clean_model_output
from backend.rag.collections import (
    DEFAULT_RAG_COLLECTION_NAME,
    normalize_collection_name,
)
from backend.rag.evaluator import evaluate_answer
from backend.rag.mode_profiles import normalize_rag_mode
from backend.rag.retrieve import retrieve_rag_context

UI_EVENT_PREFIX = "__UI_EVENT__"
COLLECTION_NAME = DEFAULT_RAG_COLLECTION_NAME
DEFAULT_DB = os.getenv(
    "DB_CONNECTION",
    "postgresql+psycopg2://postgres:1@localhost:5432/rag_db",
)


@dataclass
class BenchmarkCase:
    case_id: str
    question: str
    company_document_id: str
    revision_number: str
    expected_pages: List[int]
    expected_answer_keywords: List[str]
    expected_answer: Optional[str]
    expected_source_files: List[str]


def _to_int_list(values: Any) -> List[int]:
    out: List[int] = []
    if not isinstance(values, list):
        return out
    for v in values:
        try:
            out.append(int(v))
        except Exception:
            continue
    return out


def _to_str_list(values: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(values, list):
        return out
    for v in values:
        s = str(v or "").strip()
        if s:
            out.append(s)
    return out


def _normalize_text_tokens(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[a-z0-9]{2,}", text.lower())


def _keyword_hit_rate(keywords: List[str], text: str) -> Optional[float]:
    if not keywords:
        return None
    hay = (text or "").lower()
    if not hay:
        return 0.0
    hits = 0
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        if k in hay:
            hits += 1
    return round(hits / max(len(keywords), 1), 4)


def _token_f1(reference: str, hypothesis: str) -> Optional[float]:
    ref_tokens = _normalize_text_tokens(reference)
    hyp_tokens = _normalize_text_tokens(hypothesis)
    if not ref_tokens:
        return None
    if not hyp_tokens:
        return 0.0

    ref_set = set(ref_tokens)
    hyp_set = set(hyp_tokens)
    overlap = len(ref_set & hyp_set)
    if overlap == 0:
        return 0.0

    precision = overlap / len(hyp_set)
    recall = overlap / len(ref_set)
    if precision + recall == 0:
        return 0.0
    return round((2 * precision * recall) / (precision + recall), 4)


def _page_metrics(expected_pages: List[int], retrieved_pages: List[int]) -> Tuple[Optional[float], Optional[float]]:
    if not expected_pages:
        return None, None

    exp = set(expected_pages)
    ret = set(retrieved_pages)
    matched = len(exp & ret)

    recall = matched / max(len(exp), 1)
    precision = matched / max(len(ret), 1) if ret else 0.0
    return round(recall, 4), round(precision, 4)


def _source_file_precision(expected_files: List[str], retrieved_files: List[str]) -> Optional[float]:
    if not expected_files:
        return None
    exp = set(f.strip().lower() for f in expected_files if f.strip())
    ret = set(f.strip().lower() for f in retrieved_files if f.strip())
    if not ret:
        return 0.0
    return round(len(exp & ret) / len(ret), 4)


def _safe_mean(values: List[Optional[float]]) -> Optional[float]:
    xs = [float(v) for v in values if isinstance(v, (int, float))]
    if not xs:
        return None
    return round(statistics.mean(xs), 4)


def _load_cases(benchmark_file: Path) -> Tuple[str, List[BenchmarkCase]]:
    data = json.loads(benchmark_file.read_text(encoding="utf-8"))
    benchmark_name = str(data.get("name") or benchmark_file.stem)
    defaults = data.get("defaults", {}) if isinstance(data, dict) else {}
    raw_cases = data.get("cases", []) if isinstance(data, dict) else data

    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Benchmark file must contain a non-empty 'cases' list.")

    out: List[BenchmarkCase] = []
    for idx, item in enumerate(raw_cases):
        if not isinstance(item, dict):
            continue

        question = str(item.get("question") or "").strip()
        company_document_id = str(
            item.get("company_document_id")
            or defaults.get("company_document_id")
            or ""
        ).strip()
        revision_number = str(
            item.get("revision_number")
            or defaults.get("revision_number")
            or "1"
        ).strip()

        if not question or not company_document_id:
            raise ValueError(
                f"Invalid case at index {idx}: 'question' and 'company_document_id' are required."
            )

        case_id = str(item.get("id") or f"case_{idx+1:03d}")
        out.append(
            BenchmarkCase(
                case_id=case_id,
                question=question,
                company_document_id=company_document_id,
                revision_number=revision_number,
                expected_pages=_to_int_list(item.get("expected_pages")),
                expected_answer_keywords=_to_str_list(item.get("expected_answer_keywords")),
                expected_answer=(
                    str(item.get("expected_answer")).strip()
                    if item.get("expected_answer") is not None
                    else None
                ),
                expected_source_files=_to_str_list(item.get("expected_source_files")),
            )
        )

    if not out:
        raise ValueError("No valid benchmark cases were loaded.")
    return benchmark_name, out


def _get_vector_store(
    connection_string: str,
    embedding_model: str,
    *,
    collection_name: str = COLLECTION_NAME,
) -> PGVector:
    resolved_model = resolve_local_snapshot(HF_CACHE_DIR, embedding_model) or embedding_model
    try:
        embeddings = HuggingFaceEmbeddings(
            model_name=resolved_model,
            model_kwargs={"device": "cpu", "local_files_only": True},
            encode_kwargs={"normalize_embeddings": True},
        )
    except Exception as e:
        raise RuntimeError(
            "Failed to load embedding model in offline mode. "
            f"Model='{embedding_model}'. Ensure it is cached under models/hf_cache "
            "or pass --embedding-model with a valid local path."
        ) from e

    try:
        return PGVector.from_existing_index(
            embedding=embeddings,
            collection_name=normalize_collection_name(collection_name),
            connection=connection_string,
        )
    except Exception as e:
        raise RuntimeError(
            "Failed to connect to PGVector index. "
            "Check DB_CONNECTION and ensure the requested collection exists."
        ) from e


def _generate_answer(
    *,
    question: str,
    model_id: str,
    rag_chunks: List[Dict[str, Any]],
    max_new_tokens: int,
) -> Tuple[str, List[str]]:
    parts: List[str] = []
    errors: List[str] = []

    for token in generate_answer_stream(
        question=question,
        model_id=model_id,
        context_chunks=rag_chunks,
        max_tokens=max_new_tokens,
        session_id=None,
    ):
        if not token:
            continue
        if token.startswith(UI_EVENT_PREFIX):
            try:
                event = json.loads(token[len(UI_EVENT_PREFIX):])
                etype = str(event.get("type") or "").upper()
                if etype == "ERROR":
                    msg = str(event.get("message") or "Generation error")
                    if msg:
                        errors.append(msg)
                elif etype == "TEXT":
                    content = str(event.get("content") or "")
                    if content:
                        parts.append(content)
            except Exception:
                continue
            continue
        parts.append(str(token))

    answer = clean_model_output("".join(parts)).strip()
    return answer, errors


def _score_case(
    *,
    page_recall: Optional[float],
    retrieval_keyword_hit_rate: Optional[float],
    answer_keyword_hit_rate: Optional[float],
    answer_eval_overall: Optional[float],
    answer_similarity_f1: Optional[float],
) -> float:
    # Dynamically normalize by available metrics.
    weighted_sum = 0.0
    total_weight = 0.0

    components = [
        (page_recall, 0.30),
        (retrieval_keyword_hit_rate, 0.15),
        (answer_keyword_hit_rate, 0.15),
        (answer_eval_overall, 0.30),
        (answer_similarity_f1, 0.10),
    ]

    for value, weight in components:
        if isinstance(value, (int, float)):
            weighted_sum += float(value) * weight
            total_weight += weight

    if total_weight <= 0:
        return 0.0
    return round(weighted_sum / total_weight, 4)


def _run_mode(
    *,
    mode: str,
    cases: List[BenchmarkCase],
    vector_store: PGVector,
    generate_answers: bool,
    model_id: Optional[str],
    max_new_tokens: int,
    force_detailed: bool,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []

    for case in cases:
        retrieval_t0 = time.time()
        chunks = retrieve_rag_context(
            question=case.question,
            vector_store=vector_store,
            company_document_id=case.company_document_id,
            revision_number=case.revision_number,
            rag_mode=mode,
            force_detailed=force_detailed,
        )
        retrieval_ms = round((time.time() - retrieval_t0) * 1000, 2)

        retrieved_pages = sorted(
            {
                int((c.get("metadata") or {}).get("page_number") or 1)
                for c in chunks
                if isinstance((c.get("metadata") or {}).get("page_number"), (int, float, str))
            }
        )
        retrieved_files = sorted(
            {
                str((c.get("metadata") or {}).get("source_file") or "").strip()
                for c in chunks
                if str((c.get("metadata") or {}).get("source_file") or "").strip()
            }
        )

        chunk_corpus = "\n".join(str(c.get("content") or "") for c in chunks)
        page_recall, page_precision = _page_metrics(case.expected_pages, retrieved_pages)
        retrieval_keyword_hit_rate = _keyword_hit_rate(case.expected_answer_keywords, chunk_corpus)
        source_file_precision = _source_file_precision(case.expected_source_files, retrieved_files)

        answer = ""
        answer_errors: List[str] = []
        generation_ms: Optional[float] = None
        answer_eval = None
        answer_eval_overall: Optional[float] = None
        answer_keyword_hit_rate: Optional[float] = None
        answer_similarity_f1: Optional[float] = None

        if generate_answers and model_id:
            gen_t0 = time.time()
            answer, answer_errors = _generate_answer(
                question=case.question,
                model_id=model_id,
                rag_chunks=chunks,
                max_new_tokens=max_new_tokens,
            )
            generation_ms = round((time.time() - gen_t0) * 1000, 2)

            if answer:
                answer_eval = evaluate_answer(case.question, answer, chunks)
                answer_eval_overall = float(answer_eval.get("overall", 0.0))
                answer_keyword_hit_rate = _keyword_hit_rate(case.expected_answer_keywords, answer)
                if case.expected_answer:
                    answer_similarity_f1 = _token_f1(case.expected_answer, answer)

        composite_score = _score_case(
            page_recall=page_recall,
            retrieval_keyword_hit_rate=retrieval_keyword_hit_rate,
            answer_keyword_hit_rate=answer_keyword_hit_rate,
            answer_eval_overall=answer_eval_overall,
            answer_similarity_f1=answer_similarity_f1,
        )

        rows.append(
            {
                "case_id": case.case_id,
                "question": case.question,
                "company_document_id": case.company_document_id,
                "revision_number": case.revision_number,
                "retrieval_mode": mode,
                "retrieval_ms": retrieval_ms,
                "generation_ms": generation_ms,
                "retrieved_chunk_count": len(chunks),
                "retrieved_pages": retrieved_pages,
                "retrieved_source_files": retrieved_files,
                "expected_pages": case.expected_pages,
                "expected_source_files": case.expected_source_files,
                "page_recall": page_recall,
                "page_precision": page_precision,
                "source_file_precision": source_file_precision,
                "retrieval_keyword_hit_rate": retrieval_keyword_hit_rate,
                "answer_keyword_hit_rate": answer_keyword_hit_rate,
                "answer_similarity_f1": answer_similarity_f1,
                "answer_eval": answer_eval,
                "answer": answer,
                "answer_errors": answer_errors,
                "composite_score": composite_score,
                "passed": composite_score >= 0.65,
            }
        )

    summary = {
        "mode": mode,
        "cases": len(rows),
        "avg_composite_score": _safe_mean([r.get("composite_score") for r in rows]),
        "pass_rate": _safe_mean([1.0 if r.get("passed") else 0.0 for r in rows]),
        "avg_retrieval_ms": _safe_mean([r.get("retrieval_ms") for r in rows]),
        "avg_generation_ms": _safe_mean([r.get("generation_ms") for r in rows]),
        "avg_page_recall": _safe_mean([r.get("page_recall") for r in rows]),
        "avg_page_precision": _safe_mean([r.get("page_precision") for r in rows]),
        "avg_source_file_precision": _safe_mean([r.get("source_file_precision") for r in rows]),
        "avg_retrieval_keyword_hit_rate": _safe_mean([r.get("retrieval_keyword_hit_rate") for r in rows]),
        "avg_answer_keyword_hit_rate": _safe_mean([r.get("answer_keyword_hit_rate") for r in rows]),
        "avg_answer_eval_overall": _safe_mean(
            [
                (r.get("answer_eval") or {}).get("overall")
                if isinstance(r.get("answer_eval"), dict)
                else None
                for r in rows
            ]
        ),
    }

    worst = sorted(rows, key=lambda x: float(x.get("composite_score") or 0.0))[:5]
    summary["worst_cases"] = [
        {
            "case_id": w["case_id"],
            "composite_score": w["composite_score"],
            "page_recall": w["page_recall"],
            "retrieval_keyword_hit_rate": w["retrieval_keyword_hit_rate"],
            "answer_eval_overall": (
                (w.get("answer_eval") or {}).get("overall")
                if isinstance(w.get("answer_eval"), dict)
                else None
            ),
        }
        for w in worst
    ]

    return {"summary": summary, "results": rows}


def _render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# RAG Regression Report")
    lines.append("")
    lines.append(f"- Generated At (UTC): `{report['generated_at']}`")
    lines.append(f"- Benchmark: `{report['benchmark_name']}`")
    lines.append(f"- Cases: `{report['case_count']}`")
    lines.append(f"- Generation Enabled: `{report['generate_answers']}`")
    lines.append(f"- Model: `{report.get('model_id') or 'N/A'}`")
    lines.append(f"- Collection: `{report.get('collection_name') or COLLECTION_NAME}`")
    lines.append("")
    lines.append("## Mode Summary")
    lines.append("")
    lines.append("| Mode | Avg Score | Pass Rate | Avg Retrieval ms | Avg Gen ms | Avg Page Recall | Avg Eval Overall |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")

    for item in report.get("mode_summaries", []):
        lines.append(
            "| {mode} | {score} | {pass_rate} | {r_ms} | {g_ms} | {page_recall} | {eval_overall} |".format(
                mode=item.get("mode"),
                score=item.get("avg_composite_score"),
                pass_rate=item.get("pass_rate"),
                r_ms=item.get("avg_retrieval_ms"),
                g_ms=item.get("avg_generation_ms"),
                page_recall=item.get("avg_page_recall"),
                eval_overall=item.get("avg_answer_eval_overall"),
            )
        )

    lines.append("")
    for item in report.get("mode_summaries", []):
        lines.append(f"### Worst Cases: `{item.get('mode')}`")
        lines.append("")
        lines.append("| Case ID | Score | Page Recall | Retrieval Keyword Hit | Answer Eval Overall |")
        lines.append("|---|---:|---:|---:|---:|")
        for w in item.get("worst_cases", []):
            lines.append(
                f"| {w.get('case_id')} | {w.get('composite_score')} | "
                f"{w.get('page_recall')} | {w.get('retrieval_keyword_hit_rate')} | "
                f"{w.get('answer_eval_overall')} |"
            )
        lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run automatic RAG regression benchmarks and generate score report.",
    )
    parser.add_argument(
        "--benchmark-file",
        required=True,
        help="Path to benchmark JSON file.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path("backend") / "rag" / "reports"),
        help="Directory for JSON/Markdown reports.",
    )
    parser.add_argument(
        "--db-connection",
        default=DEFAULT_DB,
        help="PGVector DB connection string.",
    )
    parser.add_argument(
        "--embedding-model",
        default="BAAI/bge-m3",
        help="Embedding model ID or local model path for retrieval.",
    )
    parser.add_argument(
        "--collection-name",
        default=COLLECTION_NAME,
        help="PGVector collection name to benchmark.",
    )
    parser.add_argument(
        "--modes",
        default="fast,balanced,high_fidelity",
        help="Comma-separated retrieval modes.",
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
        help="Skip answer generation and only score retrieval.",
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
        help="Enable force_detailed retrieval during benchmark.",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Optional cap for number of cases (0 = all).",
    )
    return parser.parse_args()


def run_regression_benchmark(
    *,
    benchmark_file: Path,
    output_dir: Path,
    db_connection: str,
    embedding_model: str,
    modes: List[str],
    generate_answers: bool,
    model_id: Optional[str],
    max_new_tokens: int,
    force_detailed: bool,
    max_cases: int = 0,
    collection_name: str = COLLECTION_NAME,
) -> Dict[str, Any]:
    benchmark_file = Path(benchmark_file).resolve()
    if not benchmark_file.exists():
        raise SystemExit(f"Benchmark file not found: {benchmark_file}")

    benchmark_name, cases = _load_cases(benchmark_file)
    if max_cases and max_cases > 0:
        cases = cases[:max_cases]

    resolved_modes = [normalize_rag_mode(m) for m in (modes or []) if str(m).strip()]
    if not resolved_modes:
        resolved_modes = ["fast", "balanced", "high_fidelity"]

    resolved_collection_name = normalize_collection_name(collection_name)

    print(f"[REGRESSION] Benchmark: {benchmark_name}")
    print(f"[REGRESSION] Cases: {len(cases)}")
    print(f"[REGRESSION] Modes: {resolved_modes}")
    print(f"[REGRESSION] Generate answers: {generate_answers} | model={model_id or 'N/A'}")
    print(f"[REGRESSION] Collection: {resolved_collection_name}")

    try:
        vector_store = _get_vector_store(
            db_connection,
            embedding_model,
            collection_name=resolved_collection_name,
        )
    except Exception as e:
        raise SystemExit(f"[REGRESSION] Setup failed: {e}")

    mode_blocks: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []

    started = time.time()
    for mode in resolved_modes:
        print(f"[REGRESSION] Running mode: {mode}")
        block = _run_mode(
            mode=mode,
            cases=cases,
            vector_store=vector_store,
            generate_answers=generate_answers,
            model_id=model_id,
            max_new_tokens=max(16, int(max_new_tokens)),
            force_detailed=bool(force_detailed),
        )
        mode_blocks.append(block)
        all_rows.extend(block["results"])

    finished = time.time()

    mode_summaries = [b["summary"] for b in mode_blocks]
    best_mode = None
    if mode_summaries:
        best_mode = sorted(
            mode_summaries,
            key=lambda x: float(x.get("avg_composite_score") or 0.0),
            reverse=True,
        )[0].get("mode")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_name": benchmark_name,
        "benchmark_file": str(benchmark_file),
        "case_count": len(cases),
        "modes": resolved_modes,
        "generate_answers": generate_answers,
        "model_id": model_id,
        "force_detailed": bool(force_detailed),
        "collection_name": resolved_collection_name,
        "elapsed_seconds": round(finished - started, 3),
        "best_mode_by_score": best_mode,
        "mode_summaries": mode_summaries,
        "results": all_rows,
    }

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", benchmark_name).strip("_") or "benchmark"

    json_path = output_dir / f"{safe_name}_{stamp}.json"
    md_path = output_dir / f"{safe_name}_{stamp}.md"

    report["json_report_path"] = str(json_path)
    report["markdown_report_path"] = str(md_path)
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")

    print("[REGRESSION] Done.")
    print(f"[REGRESSION] JSON report: {json_path}")
    print(f"[REGRESSION] Markdown report: {md_path}")
    print(f"[REGRESSION] Best mode by average score: {best_mode}")
    return report


def main() -> None:
    args = parse_args()

    raw_modes = [m.strip() for m in str(args.modes or "").split(",") if m.strip()]
    if not raw_modes:
        raw_modes = ["fast", "balanced", "high_fidelity"]

    generate_answers = not bool(args.no_generate)
    model_id: Optional[str] = None
    if generate_answers:
        model_id = resolve_model_id(args.chat_mode)

    run_regression_benchmark(
        benchmark_file=Path(args.benchmark_file),
        output_dir=Path(args.output_dir),
        db_connection=args.db_connection,
        embedding_model=args.embedding_model,
        modes=raw_modes,
        generate_answers=generate_answers,
        model_id=model_id,
        max_new_tokens=int(args.max_new_tokens),
        force_detailed=bool(args.force_detailed),
        max_cases=int(args.max_cases or 0),
        collection_name=args.collection_name,
    )


if __name__ == "__main__":
    main()
