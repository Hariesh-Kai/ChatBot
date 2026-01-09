"use client";

import { CheckCircle, AlertCircle, Loader2, FileText } from "lucide-react";

/* =========================================================
   TYPES
========================================================= */

export type ChunkingPhase =
  | "idle"
  | "processing"
  | "waiting_metadata"
  | "committing"
  | "done"
  | "error";

type ChunkingResult = {
  document_id: string;
  revision: number;
  chunk_count?: number;
};

/* ================= PROPS ================= */

interface ChunkingProgressProps {
  phase: ChunkingPhase;
  result?: ChunkingResult | null;
  error?: string | null;
}

/* =========================================================
   COMPONENT
========================================================= */

export default function ChunkingProgress({
  phase,
  result,
  error,
}: ChunkingProgressProps) {
  if (phase === "idle") return null;

  return (
    <div className="mx-4 mt-3 rounded-xl border border-white/10 bg-black/60 p-4 backdrop-blur">
      {/* =====================================================
         PROCESSING (UPLOAD / METADATA / CHUNKING)
      ===================================================== */}
      {(phase === "processing" || phase === "committing") && (
        <div className="flex items-start gap-3 text-sm text-gray-300">
          <Loader2 className="mt-0.5 h-5 w-5 animate-spin text-blue-400" />
          <div>
            <p className="font-medium text-white">
              {phase === "processing"
                ? "Processing document"
                : "Indexing document"}
            </p>
            <p className="text-xs text-gray-400">
              {phase === "processing"
                ? "Extracting metadata and preparing content…"
                : "Chunking and storing embeddings…"}
            </p>
          </div>
        </div>
      )}

      {/* =====================================================
         WAITING FOR USER METADATA
      ===================================================== */}
      {phase === "waiting_metadata" && (
        <div className="flex items-start gap-3 text-sm text-yellow-300">
          <FileText className="mt-0.5 h-5 w-5 text-yellow-400" />
          <div>
            <p className="font-medium text-white">
              Metadata required
            </p>
            <p className="text-xs text-gray-400">
              Please provide missing document details to continue.
            </p>
          </div>
        </div>
      )}

      {/* =====================================================
         SUCCESS
      ===================================================== */}
      {phase === "done" && result && (
        <div className="flex items-start gap-3 text-sm text-green-300">
          <CheckCircle className="mt-0.5 h-5 w-5 text-green-400" />
          <div>
            <p className="font-medium text-white">
              Document ready
            </p>
            <p className="text-xs text-gray-400">
              Revision <span className="text-white">v{result.revision}</span>
              {typeof result.chunk_count === "number" && (
                <>
                  {" "}
                  • {result.chunk_count} chunks indexed
                </>
              )}
            </p>
          </div>
        </div>
      )}

      {/* =====================================================
         ERROR
      ===================================================== */}
      {phase === "error" && (
        <div className="flex items-start gap-3 text-sm text-red-300">
          <AlertCircle className="mt-0.5 h-5 w-5 text-red-400" />
          <div>
            <p className="font-medium text-white">
              Ingestion failed
            </p>
            <p className="text-xs text-gray-400">
              {error || "An unexpected error occurred"}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
