# backend/rag/chunk_strategy.py

"""
Document-Type Aware Chunking Strategy

Purpose:
- Different document types need different chunking parameters
- Technical specs → large chunks (preserve table context)
- Procedures/SOPs → small chunks (step isolation)
- Reports → medium chunks
- Auto-detects document type from metadata or content signals

Design Rules:
- Returns ChunkConfig dataclass — never modifies chunker directly
- Detection is heuristic-based (no LLM)
- Falls back to default config on any error
- Used at ingest time to configure ContextAwareChunker
"""

import re
from dataclasses import dataclass
from typing import Optional, Dict, Any


# ============================================================
# CHUNK CONFIG MODEL
# ============================================================

@dataclass
class ChunkConfig:
    """Chunking parameters for a given document type."""
    chunk_size:    int
    chunk_overlap: int
    doc_type:      str
    description:   str


# ============================================================
# PRESET CONFIGS BY DOC TYPE
# ============================================================

CHUNK_CONFIGS: Dict[str, ChunkConfig] = {
    "specification": ChunkConfig(
        chunk_size    = 3500,
        chunk_overlap = 500,
        doc_type      = "specification",
        description   = "Large chunks for technical specs — preserve table+section context",
    ),
    "procedure": ChunkConfig(
        chunk_size    = 800,
        chunk_overlap = 100,
        doc_type      = "procedure",
        description   = "Small chunks for procedures/SOPs — isolate individual steps",
    ),
    "report": ChunkConfig(
        chunk_size    = 2000,
        chunk_overlap = 300,
        doc_type      = "report",
        description   = "Medium chunks for reports and studies",
    ),
    "drawing": ChunkConfig(
        chunk_size    = 1000,
        chunk_overlap = 150,
        doc_type      = "drawing",
        description   = "Compact chunks for P&IDs / engineering drawings",
    ),
    "datasheet": ChunkConfig(
        chunk_size    = 1500,
        chunk_overlap = 200,
        doc_type      = "datasheet",
        description   = "Tight chunks for datasheets — preserve parameter rows",
    ),
    "default": ChunkConfig(
        chunk_size    = 3000,
        chunk_overlap = 400,
        doc_type      = "default",
        description   = "Standard chunking config",
    ),
}


# ============================================================
# DOCUMENT TYPE DETECTION
# ============================================================

# Keyword signals per document type
_TYPE_SIGNALS: Dict[str, list] = {
    "specification": [
        "basis of design", "design specification", "technical specification",
        "process specification", "engineering specification", "spec sheet",
        "design criteria", "bsd", "bod",
    ],
    "procedure": [
        "operating procedure", "standard procedure", "maintenance procedure",
        "sop", "work instruction", "startup procedure", "shutdown procedure",
        "step 1", "step 2", "step 3",
    ],
    "report": [
        "project report", "feasibility study", "hazop", "risk assessment",
        "inspection report", "survey report", "study report", "basis report",
    ],
    "drawing": [
        "p&id", "pid", "isometric", "general arrangement", "plot plan",
        "piping layout", "instrument loop", "electrical single line",
    ],
    "datasheet": [
        "datasheet", "data sheet", "equipment data", "instrument datasheet",
        "valve datasheet", "pump datasheet", "equipment specification sheet",
    ],
}


def detect_document_type(
    filename: Optional[str] = None,
    document_title: Optional[str] = None,
    content_sample: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Detect document type from filename, title, and content signals.
    Returns one of: specification, procedure, report, drawing, datasheet, default.
    Never raises.
    """
    try:
        # Combine all available text signals
        signals = []
        if filename:
            signals.append(filename.lower())
        if document_title:
            signals.append(document_title.lower())
        if content_sample:
            signals.append(content_sample[:500].lower())
        if metadata:
            for k in ("document_type", "doc_type", "title", "filename"):
                v = metadata.get(k)
                if v:
                    signals.append(str(v).lower())

        combined = " ".join(signals)

        # Score each type
        scores: Dict[str, int] = {}
        for doc_type, keywords in _TYPE_SIGNALS.items():
            hit = sum(1 for kw in keywords if kw in combined)
            if hit > 0:
                scores[doc_type] = hit

        if scores:
            best = max(scores, key=lambda t: scores[t])
            return best

        return "default"

    except Exception as e:
        print(f"[CHUNK_STRATEGY] detect_document_type error (non-fatal): {e}")
        return "default"


def get_chunk_config(
    filename: Optional[str] = None,
    document_title: Optional[str] = None,
    content_sample: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    doc_type: Optional[str] = None,
) -> ChunkConfig:
    """
    Get ChunkConfig for a document.

    Pass explicit doc_type to skip detection.
    Falls back to default config on any error.
    """
    try:
        if not doc_type:
            doc_type = detect_document_type(
                filename=filename,
                document_title=document_title,
                content_sample=content_sample,
                metadata=metadata,
            )

        config = CHUNK_CONFIGS.get(doc_type, CHUNK_CONFIGS["default"])
        print(f"[CHUNK_STRATEGY] Detected '{config.doc_type}' → "
              f"chunk_size={config.chunk_size}, overlap={config.chunk_overlap}")
        return config

    except Exception as e:
        print(f"[CHUNK_STRATEGY] get_chunk_config error (non-fatal): {e}")
        return CHUNK_CONFIGS["default"]
