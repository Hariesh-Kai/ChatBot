// frontend/app/lib/analytics.ts

/* =========================================================
   ANALYTICS / LOGGING (SAFE + METRICS)
   ---------------------------------------------------------
   • No PII
   • No message content
   • Token + latency + cost metrics
   • Stream-aware
   • Non-blocking
   • Console-based (replace later)
========================================================= */

/* ================= EVENTS ================= */

export type AnalyticsEvent =
  /* ---------- Chat ---------- */
  | "chat_started"
  | "chat_stream_started"
  | "chat_stream_completed"
  | "chat_stream_error"
  | "chat_aborted"

  /* ---------- Metrics ---------- */
  | "chat_tokens"
  | "chat_cost"

  /* ---------- Metadata ---------- */
  | "metadata_requested"
  | "metadata_submitted"
  | "metadata_submit_error"

  /* ---------- API ---------- */
  | "api_retry"
  | "api_failure"

  /* ---------- Job Manager ---------- */
  | "job_duplicate_blocked"
  | "job_started"
  | "job_success"
  | "job_failed"
  | "job_retrying"
  | "job_aborted"
  | "job_force_aborted"
  | "job_session_aborted";

/* ================= PAYLOAD ================= */

export interface AnalyticsPayload {
  /* session */
  sessionId?: string;

  /* model */
  model?: string;

  /* timing */
  durationMs?: number;

  /* retry */
  retries?: number;
  attempt?: number;

  /* error */
  reason?: string;

  /* job */
  key?: string;

  /* tokens */
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;

  /* cost */
  estimatedCostUsd?: number;

  /* extensibility */
  extra?: Record<string, any>;
}

/* ================= CONFIG ================= */

/**
 * Rough token cost estimates (USD / 1K tokens)
 * ⚠️ Approximate, frontend-only
 */
const NET_TOKEN_COST_USD_PER_1K: Record<
  "groq" | "xai",
  number
> = {
  groq: 0.0005,
  xai: 0.002,
};

/* ================= LOGGER ================= */

/**
 * Central analytics logger.
 * Replace with backend / Sentry later.
 */
export function logEvent(
  event: AnalyticsEvent,
  payload: AnalyticsPayload = {}
) {
  // 🔒 Never block UI
  queueMicrotask(() => {
    try {
      console.info("[ANALYTICS]", event, {
        ts: Date.now(),
        ...payload,
      });
    } catch {
      // Never throw from analytics
    }
  });
}

/* ================= TIMING ================= */

/**
 * Measure async duration automatically.
 */
export async function withTiming<T>(
  label: AnalyticsEvent,
  fn: () => Promise<T>,
  payload: AnalyticsPayload = {}
): Promise<T> {
  const start = performance.now();

  try {
    const result = await fn();

    logEvent(label, {
      ...payload,
      durationMs: Math.round(performance.now() - start),
    });

    return result;
  } catch (err) {
    logEvent("api_failure", {
      ...payload,
      durationMs: Math.round(performance.now() - start),
      reason: (err as Error)?.message,
    });
    throw err;
  }
}

/* ================= TOKEN METRICS ================= */

/**
 * Estimate tokens very roughly from text length.
 * (~4 chars per token heuristic)
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.ceil(text.length / 4);
}

/**
 * Log token usage for a chat.
 */
export function logTokenUsage({
  sessionId,
  model,
  inputText,
  outputText,
}: {
  sessionId?: string;
  model?: string;
  inputText: string;
  outputText: string;
}) {
  const inputTokens = estimateTokens(inputText);
  const outputTokens = estimateTokens(outputText);
  const totalTokens = inputTokens + outputTokens;

  logEvent("chat_tokens", {
    sessionId,
    model,
    inputTokens,
    outputTokens,
    totalTokens,
  });

  return { inputTokens, outputTokens, totalTokens };
}

/* ================= COST METRICS ================= */

/**
 * Estimate Net cost (frontend-only advisory).
 */
export function estimateNetCostUsd({
  provider,
  totalTokens,
}: {
  provider?: "groq" | "xai";
  totalTokens: number;
}): number {
  if (!provider) return 0;

  const rate = NET_TOKEN_COST_USD_PER_1K[provider] ?? 0;

  return Number(((totalTokens / 1000) * rate).toFixed(6));
}

/**
 * Log estimated Net cost.
 */
export function logNetCost({
  sessionId,
  model,
  provider,
  totalTokens,
}: {
  sessionId?: string;
  model?: string;
  provider?: "groq" | "xai";
  totalTokens: number;
}) {
  const estimatedCostUsd = estimateNetCostUsd({
    provider,
    totalTokens,
  });

  logEvent("chat_cost", {
    sessionId,
    model,
    estimatedCostUsd,
    extra: { provider },
  });

  return estimatedCostUsd;
}
