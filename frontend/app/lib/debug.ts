// frontend/app/lib/debug.ts

/**
 * RAG DEBUG API CLIENT
 *
 * Purpose:
 * - Fetch read-only RAG debug snapshots from backend
 * - Used ONLY by developer debug UI
 *
 * Rules:
 * - ❌ No retries
 * - ❌ No caching
 * - ❌ No writes
 * - ✅ Read-only
 */

import { API_BASE } from "./config";

// ============================================================
// TYPES
// ============================================================

export type ConfidenceLevel = "high" | "medium" | "low";

export interface RagDebugChunk {
  id: string;
  section?: string;
}

export interface RagDebugConfidence {
  confidence: number;
  level: ConfidenceLevel;
}

export interface RagDebugSnapshot {
  original_question: string;
  rewritten_question: string;
  topic_hint?: string;
  intent: string;
  retrieved_chunks: RagDebugChunk[];
  used_chunk_ids: string[];
  confidence?: RagDebugConfidence;
}

interface RagDebugResponse {
  session_id: string;
  rag_debug: RagDebugSnapshot;
}

// ============================================================
// API CALL
// ============================================================

export async function fetchRagDebug(
  sessionId: string
): Promise<RagDebugSnapshot> {
  if (!sessionId) {
    throw new Error("sessionId is required");
  }

  const res = await fetch(
    `${API_BASE}/debug/rag/${sessionId}`,
    {
      method: "GET",
      cache: "no-store",
    }
  );

  if (!res.ok) {
    throw new Error(
      `Failed to fetch RAG debug data (${res.status})`
    );
  }

  const data = (await res.json()) as RagDebugResponse;

  if (!data || !data.rag_debug) {
    throw new Error("Invalid RAG debug response");
  }

  return data.rag_debug;
}
