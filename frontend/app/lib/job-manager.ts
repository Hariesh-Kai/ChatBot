/* =========================================================
   JOB MANAGER (FRONTEND)
   ---------------------------------------------------------
   Responsibilities:
   - Ensure only ONE active CHAT streaming job at a time
   - Abort previous CHAT job before starting a new one
   - NEVER reset abort from frontend
   - NEVER interfere with upload / metadata jobs
   - Clean up correctly on completion or abort
========================================================= */

import { API_BASE } from "./config";

/* =========================================================
   TYPES
========================================================= */

type JobHandle = {
  controller: AbortController;
  sessionId: string;
  active: boolean;
};

/* =========================================================
   STATE (CHAT ONLY)
========================================================= */

// 🔥 IMPORTANT:
// This job manager controls CHAT STREAMS ONLY.
// Uploads / metadata must NOT go through this.
let currentChatJob: JobHandle | null = null;

/* =========================================================
   INTERNAL HELPERS
========================================================= */

async function sendAbort(sessionId: string) {
  try {
    await fetch(`${API_BASE}/abort/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch {
    // best-effort only — never block UI
  }
}

/* =========================================================
   PUBLIC API
========================================================= */

/**
 * Start a new CHAT streaming job.
 * Automatically aborts any previous CHAT job.
 */
export function startJob(sessionId: string): AbortController {
  // 🔥 Abort ONLY previous chat job
  if (currentChatJob) {
    abortJob("superseded");
  }

  const controller = new AbortController();

  currentChatJob = {
    controller,
    sessionId,
    active: true,
  };

  return controller;
}

/**
 * Abort the currently running CHAT job.
 */
export function abortJob(reason: string = "user") {
  if (!currentChatJob) return;

  const { controller, sessionId } = currentChatJob;

  try {
    controller.abort(reason);
  } catch {
    // ignore
  }

  // 🔥 Inform backend — backend is authoritative
  sendAbort(sessionId);

  currentChatJob.active = false;
  currentChatJob = null;
}

/**
 * Mark CHAT job as finished (stream completed normally).
 * MUST be called when stream ends cleanly.
 */
export function finishJob() {
  if (!currentChatJob) return;

  currentChatJob.active = false;
  currentChatJob = null;
}

/**
 * Check if a CHAT job is currently active.
 */
export function hasActiveJob(): boolean {
  return Boolean(currentChatJob?.active);
}
