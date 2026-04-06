# RAG Regression Benchmark Format

Use this folder for benchmark datasets consumed by:

`backend/rag/regression_runner.py`

## Run

```bash
python -m backend.rag.regression_runner \
  --benchmark-file backend/rag/benchmarks/sample_regression_benchmark.json
```

Reports are written to:

`backend/rag/reports/*.json` and `backend/rag/reports/*.md`

If embeddings are not auto-resolved from local HF cache, provide:

```bash
python -m backend.rag.regression_runner \
  --benchmark-file backend/rag/benchmarks/sample_regression_benchmark.json \
  --embedding-model path/to/local/embedding/model
```

## JSON Schema

```json
{
  "name": "your_benchmark_name",
  "defaults": {
    "company_document_id": "doc-id",
    "revision_number": "1"
  },
  "cases": [
    {
      "id": "q001",
      "question": "What is the design pressure?",
      "company_document_id": "optional-override-doc-id",
      "revision_number": "optional-override-rev",
      "expected_outcome": "release",
      "challenge_tags": ["numeric_fact"],
      "expected_pages": [12],
      "expected_source_files": ["file.pdf"],
      "expected_answer_keywords": ["design pressure", "bar"],
      "expected_answer": "Optional golden answer text"
    }
  ]
}
```

## Notes

- `question` and `company_document_id` are required per case (or via `defaults`).
- `expected_outcome` defaults to `release`. Set it to `abstain` for intentionally unanswerable questions where the system should refuse to release a fact answer.
- `challenge_tags` is optional metadata for grouping harder cases such as `repeated_metadata`, `competing_numbers`, `enumerated_list`, or `unanswerable`.
- `expected_pages`, `expected_source_files`, and `expected_answer_keywords` are optional but strongly recommended for release cases.
- `expected_answer` is optional; when present, the runner computes token-level similarity (`answer_similarity_f1`).
- For `abstain` cases, the runner treats an explicit abstention plus `should_release=false` as the expected success condition.
