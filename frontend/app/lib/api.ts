// frontend/app/lib/api.ts

/* =========================================================
   API CLIENT — ENTERPRISE SAFE
   ---------------------------------------------------------
   • Chat streaming (unchanged)
   • PDF upload (phase 1: metadata only)
   • PDF commit (phase 2: chunk + ingest)
   • Metadata update
   • Force ingest support
   • Abort + retry safe
========================================================= */

import { logEvent } from "./analytics";
import { API_BASE } from "./config";
import { UI_EVENT_PREFIX } from "./llm-ui-events";

/* =========================================================
   CONFIG
========================================================= */

const DEFAULT_RETRIES = 2;
const BASE_DELAY_MS = 400;

/* =========================================================
   TYPES — CHAT
========================================================= */

export type ChatMode = "lite" | "base" | "net";

export interface ChatRequest {
  session_id: string;
  question: string;
  mode: ChatMode;
}

/* =========================================================
   TYPES — UPLOAD (PHASE 1)
========================================================= */

export interface UploadPdfResponse {
  job_id: string;
  document_id: string;
  revision: number;
  filename: string;
  status: string;

  metadata: Record<
    string,
    {
      key: string;
      value?: string | null;
      confidence?: number | null;
    }
  >;

  missing_metadata: string[];
  next_action: "WAIT_FOR_METADATA" | "READY_TO_COMMIT";
}

/* =========================================================
   TYPES — COMMIT (PHASE 2)
========================================================= */

export interface CommitUploadRequest {
  job_id: string;
  metadata: Record<string, string>;
  force?: boolean;
}

export interface CommitUploadResponse {
  job_id: string;
  document_id: string;
  revision: number;
  status: string;
}

/* =========================================================
   TYPES — METADATA UPDATE
========================================================= */

export interface MetadataUpdateRequest {
  job_id: string;
  metadata: {
    document_type?: string;
    revision_code?: string;
    revision_number?: string;
    company_document_id?: string; // usually not editable
  };
}


/* =========================================================
   TYPES — NET
========================================================= */

export interface NetStatusResponse {
  ok: boolean;
  enabled: boolean;
  provider?: string | null;
  model?: string | null;
}

export interface NetKeyVerifyResponse {
  valid: boolean;
  provider: string;
  message: string;
}

/* =========================================================
   ERROR NORMALIZATION
========================================================= */

async function normalizeError(res: Response): Promise<string> {
  let text = "";

  try {
    text = await res.text();
  } catch {
    return "Request failed";
  }

  try {
    const data = JSON.parse(text);

    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.message === "string") return data.message;
    if (Array.isArray(data?.detail)) {
      return data.detail.map((d: any) => d?.msg || d).join(", ");
    }
  } catch {
    /* ignore */
  }

  if (text.startsWith("<")) return "Server error occurred";
  return text || "Request failed";
}

/* =========================================================
   UTILS
========================================================= */

function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const id = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener("abort", () => {
        clearTimeout(id);
        reject(new DOMException("Aborted", "AbortError"));
      });
    }
  });
}

async function withRetry<T>(
  fn: () => Promise<T>,
  {
    retries = DEFAULT_RETRIES,
    signal,
  }: {
    retries?: number;
    signal?: AbortSignal;
  } = {}
): Promise<T> {
  let attempt = 0;

  while (true) {
    try {
      return await fn();
    } catch (err: any) {
      if (signal?.aborted) throw err;

      if (attempt >= retries) {
        logEvent("api_failure", { reason: err?.message });
        throw err;
      }

      attempt += 1;
      logEvent("api_retry", { retries: attempt, reason: err?.message });

      const delay = BASE_DELAY_MS * Math.pow(2, attempt - 1);
      await sleep(delay, signal);
    }
  }
}

/* =========================================================
   CHAT STREAM (UNCHANGED)
========================================================= */

export async function streamChat(
  payload: ChatRequest,
  signal?: AbortSignal
): Promise<ReadableStream<Uint8Array>> {
  return withRetry(
    async () => {
      const res = await fetch(`${API_BASE}/chat/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal,
      });

      if (res.status === 429) {
        const retryAfter =
          Number(res.headers.get("Retry-After")) || 30;

        throw new Error(
          UI_EVENT_PREFIX +
            JSON.stringify({
              type: "NET_RATE_LIMITED",
              retryAfterSec: retryAfter,
            })
        );
      }

      if (!res.ok) {
        throw new Error(await normalizeError(res));
      }

      if (!res.body) {
        throw new Error("Chat stream missing response body");
      }

      return res.body;
    },
    { signal }
  );
}

/* =========================================================
   PDF UPLOAD — PHASE 1 (METADATA ONLY)
========================================================= */

export async function uploadPdf(
  file: File,
  sessionId: string
): Promise<UploadPdfResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("session_id", sessionId);

  const res = await fetch(`${API_BASE}/upload/`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    throw new Error(await normalizeError(res));
  }

  logEvent("job_started", { key: "upload", sessionId });

  return res.json();
}

/* =========================================================
   PDF COMMIT — PHASE 2 (CHUNK + INGEST)
========================================================= */

export async function commitUpload(
  payload: CommitUploadRequest
): Promise<CommitUploadResponse> {
  const res = await fetch(`${API_BASE}/upload/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    throw new Error(await normalizeError(res));
  }

  logEvent("job_success", {
    key: "commit",
    sessionId: payload.job_id,
  });

  return res.json();
}

/* =========================================================
   METADATA UPDATE (POST-POPUP)
========================================================= */

export async function updateMetadata(
  payload: MetadataUpdateRequest
): Promise<void> {
  const res = await fetch(`${API_BASE}/metadata/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    throw new Error(await normalizeError(res));
  }

  logEvent("metadata_submitted", {
    sessionId: payload.job_id,
  });
}

/* =========================================================
   NET (UNCHANGED)
========================================================= */

export async function verifyNetKey(
  apiKey: string
): Promise<NetKeyVerifyResponse> {
  const res = await fetch(`${API_BASE}/net-key/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });

  if (!res.ok) {
    throw new Error(await normalizeError(res));
  }

  return res.json();
}

export async function fetchNetStatus(): Promise<NetStatusResponse> {
  const res = await fetch(`${API_BASE}/net/status`);

  if (!res.ok) {
    throw new Error(await normalizeError(res));
  }

  return res.json();
}

/* =========================================================
   SAFE JSON
========================================================= */

export function safeJsonParse<T = any>(value: string): T | null {
  try {
    return JSON.parse(value) as T;
  } catch {
    return null;
  }
}
