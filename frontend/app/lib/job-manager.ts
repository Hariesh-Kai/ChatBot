/* =========================================================
   JOB MANAGER (FRONTEND)
   ---------------------------------------------------------
   Responsibilities:
   - Ensure only ONE active streaming job per session
   - Abort previous job before starting a new one
   - Reset abort state correctly
   - Clean up on errors and stream end
========================================================= */

import { API_BASE } from "./config";

type JobHandle = {
  controller: AbortController;
  sessionId: string;
  active: boolean;
};

let currentJob: JobHandle | null = null;

/* ---------------------------------------------------------
   INTERNAL HELPERS
--------------------------------------------------------- */

async function sendAbort(sessionId: string) {
  try {
    await fetch(`${API_BASE}/abort`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch {
    // best-effort only
  }
}

async function resetAbort(sessionId: string) {
  try {
    await fetch(`${API_BASE}/abort/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
  } catch {
    // best-effort only
  }
}

/* ---------------------------------------------------------
   PUBLIC API
--------------------------------------------------------- */

/**
 * Start a new streaming job.
 * Automatically aborts any previous job.
 */
export function startJob(sessionId: string): AbortController {
  // Abort existing job if any
  if (currentJob) {
    abortJob("superseded");
  }

  const controller = new AbortController();

  currentJob = {
    controller,
    sessionId,
    active: true,
  };

  // 🔥 CRITICAL: reset abort BEFORE new request
  resetAbort(sessionId);

  return controller;
}

/**
 * Abort the currently running job.
 */
export function abortJob(reason: string = "user") {
  if (!currentJob) return;

  const { controller, sessionId } = currentJob;

  try {
    controller.abort(reason);
  } catch {
    // ignore
  }

  sendAbort(sessionId);

  currentJob.active = false;
  currentJob = null;
}

/**
 * Mark job as finished (stream completed normally).
 */
export function finishJob() {
  if (!currentJob) return;

  currentJob.active = false;
  currentJob = null;
}

/**
 * Check if a job is currently active.
 */
export function hasActiveJob(): boolean {
  return Boolean(currentJob?.active);
}
