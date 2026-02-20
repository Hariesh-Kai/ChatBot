// frontend/app/lib/api.ts

/* =========================================================
   API CLIENT — ENTERPRISE SAFE
========================================================= */

import { logEvent } from "./analytics";
import { API_BASE } from "./config";
import { UI_EVENT_PREFIX, parseLLMUIEvent } from "./llm-ui-events";


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
   TYPES — AUTH
========================================================= */

export type AuthUser = {
  username: string;
  email: string;
  role?: string;
};

/* =========================================================
   AUTH
========================================================= */

export async function authLogin(
  identifier: string,
  password: string
): Promise<AuthUser> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ identifier, password }),
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

export async function authLogout(): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });

  if (!res.ok) throw new Error(await normalizeError(res));
}

export async function authMe(): Promise<AuthUser | null> {
  try {
    const res = await fetch(`${API_BASE}/auth/me`, {
      credentials: "include",
    });

    if (res.status === 401) return null;
    if (!res.ok) throw new Error(await normalizeError(res));
    return res.json();
  } catch {
    return null;
  }
}

/* =========================================================
   TYPES — UPLOAD (PHASE 1)
========================================================= */

export interface UploadPdfResponse {
  job_id: string;
  company_document_id: string;
  revision_number: number;
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
  next_action:
  | "WAIT_FOR_METADATA"
  | "READY_FOR_PROCESSING"
  | "READY_TO_COMMIT"
  | "READY_FOR_COMMIT"
  | string; // fallback — servers sometimes differ
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
  company_document_id: string;
  revision_number: string;
  status: string;
}

export interface UploadIngestionStatusResponse {
  job_id?: string | null;
  session_id?: string | null;
  status: string;
  ready: boolean;
  message: string;
  error?: string | null;
  progress?: number | null;
  progress_label?: string | null;
  active_document?: {
    company_document_id?: string;
    revision_number?: string;
    filename?: string;
  } | null;
}

/* =========================================================
   TYPES — METADATA UPDATE
========================================================= */

export interface MetadataUpdateRequest {
  job_id: string;
  metadata: Record<string, string>;
  force?: boolean;
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
  } catch {}

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

export async function waitForBackendReady({
  timeoutMs = 15000,
  intervalMs = 600,
}: {
  timeoutMs?: number;
  intervalMs?: number;
} = {}): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
      if (res.ok) return true;
    } catch {
      // ignore and retry
    }
    await sleep(intervalMs);
  }
  return false;
}

async function withRetry<T>(
  fn: () => Promise<T>,
  { retries = DEFAULT_RETRIES, signal }: { retries?: number; signal?: AbortSignal } = {}
): Promise<T> {
  let attempt = 0;

  while (true) {
    try {
      return await fn();
    } catch (err: any) {
     if (signal?.aborted || err?.name === "AbortError") throw err;
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
   CHAT STREAM
========================================================= */

export async function streamChat(
  payload: ChatRequest,
  signal?: AbortSignal
): Promise<ReadableStream<Uint8Array>> {
  return withRetry(async () => {
    const res = await fetch(`${API_BASE}/chat/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
      signal,
    });

    if (res.status === 429) {
      const retryAfter = Number(res.headers.get("Retry-After")) || 30;
      throw new Error(
        UI_EVENT_PREFIX +
          JSON.stringify({
            type: "NET_RATE_LIMITED",
            retryAfterSec: retryAfter,
          })
      );
    }

    if (!res.ok) throw new Error(await normalizeError(res));
    if (!res.body) throw new Error("Chat stream missing response body");

    return res.body;
  }, { signal });
}

/* =========================================================
   PDF UPLOAD — PHASE 1
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
    credentials: "include",
    body: form,
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  logEvent("job_started", { key: "upload", sessionId });
  return res.json();
}

/* =========================================================
   PDF COMMIT — PHASE 2
========================================================= */

export async function commitUpload(
  payload: CommitUploadRequest
): Promise<CommitUploadResponse> {
  const res = await fetch(`${API_BASE}/upload/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(payload),
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  logEvent("job_success", { key: "commit", sessionId: payload.job_id });
  return res.json();
}

export async function fetchUploadIngestionStatus(params: {
  jobId?: string | null;
  sessionId?: string | null;
}): Promise<UploadIngestionStatusResponse> {
  const query = new URLSearchParams();
  if (params.jobId) query.set("job_id", params.jobId);
  if (params.sessionId) query.set("session_id", params.sessionId);

  if (!query.toString()) {
    throw new Error("jobId or sessionId is required");
  }

  const res = await fetch(`${API_BASE}/upload/status?${query.toString()}`, {
    credentials: "include",
    cache: "no-store",
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

/* =========================================================
   METADATA UPDATE (STREAMING)
========================================================= */

export async function updateMetadata(
  payload: MetadataUpdateRequest,
  onProgress?: (event: { message?: string; progress?: number }) => void
): Promise<void> {
  // Try canonical endpoints in order to avoid client/server mismatch
let res: Response | null = null;
const attempts = [
  // Prefer the streaming commit endpoint so the frontend can show chunking/embedding/indexing progress.
  `${API_BASE}/metadata/update`,
  `${API_BASE}/metadata/`,
];

let lastError: Error | null = null;

for (const url of attempts) {
  try {
    const attemptRes = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
    });

    // if endpoint explicitly not found / not allowed, try the next option
    if (attemptRes.status === 404 || attemptRes.status === 405) {
      continue;
    }

    // If we got any other response, accept it (even if non-OK — we'll surface error)
    res = attemptRes;
    break;
  } catch (err: any) {
    lastError = err;
    continue; // try next URL
  }
}

if (!res) {
  throw lastError ?? new Error("Failed to reach metadata endpoint");
}

if (!res.ok) throw new Error(await normalizeError(res));

  const reader = res.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

        // HANDLE UI EVENTS FIRST
        if (trimmed.startsWith(UI_EVENT_PREFIX)) {
          const evt = parseLLMUIEvent(trimmed);
          if (!evt) continue;

          if (evt.type === "ERROR") {
            throw new Error(evt.message);
          }

          if (evt.type === "PROGRESS" && onProgress) {
            onProgress({
              message: evt.label,
              progress: evt.value,
            });
          }

          continue;
        }


        const data = safeJsonParse<any>(trimmed);
        if (!data) continue;

        if (data.stage === "error") {
          throw new Error(data.message || "Server error");
        }

        if (onProgress && data.message) {
          onProgress({
            message: data.message,
            progress: data.progress,
          });
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  logEvent("metadata_submitted", { sessionId: payload.job_id });
}

/* =========================================================
   NET
========================================================= */

export async function verifyNetKey(
  apiKey: string
): Promise<NetKeyVerifyResponse> {
  const res = await fetch(`${API_BASE}/net-key/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ api_key: apiKey }),
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

export async function fetchNetStatus(): Promise<NetStatusResponse> {
  const res = await fetch(`${API_BASE}/net/status`, { credentials: "include" });
  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

/* =========================================================
   AUTO-TITLING
========================================================= */

export async function generateChatTitle(question: string): Promise<string> {
  try {
    const res = await fetch(`${API_BASE}/chat/title`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ question }),
    });

    if (!res.ok) return "New Chat";
    const data = await res.json();
    return data.title || "New Chat";
  } catch {
    return "New Chat";
  }
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
