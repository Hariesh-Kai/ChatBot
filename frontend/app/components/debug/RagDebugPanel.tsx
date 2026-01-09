"use client";

/**
 * RAG DEBUG PANEL
 *
 * Purpose:
 * - Developer-only UI for inspecting RAG behavior
 * - Read-only, no mutations
 * - Safe for streaming chat UI
 *
 * Rules:
 * - ❌ No LLM calls
 * - ❌ No chat mutations
 * - ❌ No prompt injection
 */

import { useEffect, useState } from "react";
import {
  fetchRagDebug,
  RagDebugSnapshot,
} from "@/app/lib/debug";

interface RagDebugPanelProps {
  sessionId: string;
  open: boolean;
}

export default function RagDebugPanel({
  sessionId,
  open,
}: RagDebugPanelProps) {
  const [data, setData] = useState<RagDebugSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // ----------------------------------------------------------
  // DEV-ONLY GUARD (IMPORTANT)
  // ----------------------------------------------------------

  if (process.env.NODE_ENV !== "development") {
    return null;
  }

  // ----------------------------------------------------------
  // FETCH DEBUG SNAPSHOT
  // ----------------------------------------------------------

  useEffect(() => {
    if (!open || !sessionId) return;

    setLoading(true);
    setError(null);

    fetchRagDebug(sessionId)
      .then((snapshot) => {
        setData(snapshot);
      })
      .catch(() => {
        setError("No RAG debug snapshot available.");
        setData(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [open, sessionId]);

  if (!open) return null;

  // ----------------------------------------------------------
  // RENDER
  // ----------------------------------------------------------

  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-zinc-900 p-4 text-xs text-zinc-300">
      <div className="mb-3 flex items-center justify-between">
        <span className="font-semibold text-zinc-200">
          🐞 RAG Debug (Dev Only)
        </span>
      </div>

      {/* Loading */}
      {loading && (
        <div className="text-zinc-400">
          Loading debug snapshot…
        </div>
      )}

      {/* Error */}
      {!loading && error && (
        <div className="text-red-400">
          {error}
        </div>
      )}

      {/* Empty */}
      {!loading && !error && !data && (
        <div className="text-zinc-400">
          No debug data yet.
        </div>
      )}

      {/* Debug Data */}
      {!loading && data && (
        <div className="space-y-3">
          <DebugRow label="Original Question">
            {data.original_question}
          </DebugRow>

          <DebugRow label="Rewritten Question">
            {data.rewritten_question}
          </DebugRow>

          {data.topic_hint && (
            <DebugRow label="Topic Hint">
              {data.topic_hint}
            </DebugRow>
          )}

          <DebugRow label="Intent">
            <span className="rounded bg-zinc-800 px-2 py-0.5">
              {data.intent}
            </span>
          </DebugRow>

          <div>
            <div className="mb-1 font-medium text-zinc-400">
              Retrieved Chunks
            </div>
            <ul className="ml-4 list-disc space-y-1">
              {(data.retrieved_chunks ?? []).map((c) => (
                <li key={c.id}>
                  {c.section || "Unknown"}{" "}
                  <span className="text-zinc-500">
                    ({c.id.slice(0, 6)}…)
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <DebugRow label="Used Chunk IDs">
            {data.used_chunk_ids.length > 0
              ? data.used_chunk_ids
                  .map((id) => id.slice(0, 6))
                  .join(", ")
              : "None"}
          </DebugRow>

          {data.confidence && (
            <DebugRow label="Confidence">
              <span
                className={
                  data.confidence.level === "high"
                    ? "text-green-400"
                    : data.confidence.level === "medium"
                    ? "text-yellow-400"
                    : "text-red-400"
                }
              >
                {data.confidence.confidence} (
                {data.confidence.level})
              </span>
            </DebugRow>
          )}
        </div>
      )}
    </div>
  );
}

// ============================================================
// INTERNAL HELPER COMPONENT
// ============================================================

function DebugRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 font-medium text-zinc-400">
        {label}
      </div>
      <div className="break-words rounded bg-zinc-800 px-2 py-1">
        {children}
      </div>
    </div>
  );
}
