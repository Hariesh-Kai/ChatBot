// frontend/app/lib/feedback.ts

import { API_BASE } from "./config";

/* ================================
   TYPES
================================ */

export type FeedbackLabel =
  | "correct"
  | "partial"
  | "incorrect"
  | "hallucination"
  | "missing_context";

export interface FeedbackPayload {
  session_id?: string;
  job_id?: string;

  company_document_id?: string;
  revision_number?: string;

  question: string;
  answer: string;

  feedback_label: FeedbackLabel;

  feedback_score?: number;
  comment?: string;
  chunk_ids?: string[];
}


/* ================================
   API CALL
================================ */

/**
 * Submit feedback for a generated answer.
 * Must NEVER throw or block UI.
 */
export async function submitFeedback(
  payload: FeedbackPayload
): Promise<void> {
  // 🔥 GUARD: do not send invalid / incomplete feedback
  if (
    !payload.session_id &&
    !payload.job_id &&
    !payload.company_document_id
  ) {
    return;
  }

  try {
    await fetch(`${API_BASE}/feedback/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch {
    // Intentionally silent
  }
}
