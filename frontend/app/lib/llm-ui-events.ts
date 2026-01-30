/* =========================================================
   LLM → UI EVENT CONTRACT (FRONTEND)
   ---------------------------------------------------------
   Defines ALL UI actions the backend can request via stream.

   CONTRACT GUARANTEES:
   - UI events are SINGLE-LINE
   - Each event line is prefixed with "__UI_EVENT__"
   - Payload is strict JSON
   - Parser is stream-safe and never throws
========================================================= */

//  IMPORT Source Type
import { RagSource } from "./types";

/* =========================================================
   CONSTANTS (MUST MATCH BACKEND EXACTLY)
========================================================= */

export const UI_EVENT_PREFIX = "__UI_EVENT__";

/* =========================================================
   EVENT TYPES
========================================================= */

/* ---------- System ---------- */

export type SystemMessageEvent = {
  type: "SYSTEM_MESSAGE";
  text: string;
};

/* ---------- Metadata ---------- */

export type MetadataRequestField = {
  key: string;
  label: string;
  placeholder?: string;
  reason?: string;
  value?: string | null;
  confidence?: number | null;
};

export type RequestMetadataEvent = {
  type: "REQUEST_METADATA";
  fields: MetadataRequestField[];
};

export type MetadataConfirmedEvent = {
 type: "METADATA_CONFIRMED";
  message?: string;
};

/* ---------- Progress ---------- */

export type ProgressEvent = {
  type: "PROGRESS";
  value: number; // 0–100
  label?: string;
};

/* ---------- 🟦 Model Stage (Live Pipeline State) ---------- */

export type ModelStageEvent = {
  type: "MODEL_STAGE";
  stage: string;          // e.g. "intent" | "retrieval" | "reranking" | "generation"
  message?: string;       // Human-readable text
  model?: string;         // lite | base | net
};

/* ---------- Error ---------- */

export type ErrorEvent = {
  type: "ERROR";
  message: string;
};

/* ---------- Net ---------- */



export type NetRateLimitedEvent = {
  type: "NET_RATE_LIMITED";
  retryAfterSec: number;
  provider?: string;
};

/* ----------  NEW: Sources ---------- */

export type SourcesEvent = {
  type: "SOURCES";
  data: RagSource[];
};

export type AnswerConfidenceEvent = {
  type: "ANSWER_CONFIDENCE";
  confidence: number;
  level: "high" | "medium" | "low";
};





/* =========================================================
   UNION TYPE — ALL ALLOWED EVENTS
========================================================= */

export type LLMUIEvent =
  | SystemMessageEvent
  | RequestMetadataEvent
  | MetadataConfirmedEvent
  | ProgressEvent
  | ModelStageEvent
  | ErrorEvent
  | NetRateLimitedEvent
  | SourcesEvent
  | AnswerConfidenceEvent;


/* =========================================================
   STRICT TYPE GUARD
========================================================= */

export function isLLMUIEvent(obj: unknown): obj is LLMUIEvent {
  if (!obj || typeof obj !== "object") return false;

  const type = (obj as any).type;
  if (typeof type !== "string") return false;

  switch (type) {
      case "SYSTEM_MESSAGE":
      case "REQUEST_METADATA":
      case "METADATA_CONFIRMED":
      case "PROGRESS":
      case "MODEL_STAGE":
      case "ERROR":
      case "NET_RATE_LIMITED":
      case "SOURCES": 
      case "ANSWER_CONFIDENCE":

        return true;
      default:
        return false;
    }
}

/* =========================================================
   STREAM-SAFE UI EVENT PARSER
   ---------------------------------------------------------
   CRITICAL FIXES:
   - Allows leading whitespace after prefix
   - Never assumes closing brace exists
   - Ignores partial / malformed JSON safely
   - NEVER throws
========================================================= */

export function parseLLMUIEvent(
  rawLine: string
): LLMUIEvent | null {
  if (!rawLine) return null;

  // Must start with prefix exactly
  if (!rawLine.startsWith(UI_EVENT_PREFIX)) {
    return null;
  }

  // Remove prefix only
  const jsonPart = rawLine.slice(UI_EVENT_PREFIX.length);

  // Allow whitespace before JSON
  const trimmed = jsonPart.trimStart();
  if (!trimmed.startsWith("{")) {
    return null;
  }

  try {
    const parsed = JSON.parse(trimmed);
    return isLLMUIEvent(parsed) ? parsed : null;
  } catch {
    // Partial / malformed JSON — ignore safely
    return null;
  }
}

