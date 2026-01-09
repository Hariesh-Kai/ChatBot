// frontend/app/lib/net.ts

/* =========================================================
   KAVINBASE NET — FRONTEND API LAYER
   ---------------------------------------------------------
   • Talks ONLY to backend
   • Never stores API keys directly
   • Explicit success / failure
   • No retries, no silent fallback
========================================================= */

import { API_BASE } from "./config";

/* ================= TYPES ================= */

export type NetVerifyResult =
  | { ok: true; provider: string; model: string }
  | { ok: false; error: string };

export type NetStatusResult =
  | { ok: true; enabled: boolean }
  | { ok: false; error: string };

/* ================= VERIFY API KEY ================= */

/**
 * Verifies a Net API key with backend.
 * Backend decides validity, quota, provider.
 */
export async function verifyNetKey(
  apiKey: string
): Promise<NetVerifyResult> {
  try {
    const res = await fetch(`${API_BASE}/net/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ api_key: apiKey }),
    });

    if (!res.ok) {
      const text = await res.text();
      return {
        ok: false,
        error: text || "Net key verification failed",
      };
    }

    const data = await res.json();

    return {
      ok: true,
      provider: data.provider,
      model: data.model,
    };
  } catch (err) {
    return {
      ok: false,
      error: "Unable to reach Net verification service",
    };
  }
}

/* ================= NET STATUS ================= */

/**
 * Checks if Net is currently enabled server-side.
 * (Useful for maintenance / kill-switch)
 */
export async function fetchNetStatus(): Promise<NetStatusResult> {
  try {
    const res = await fetch(`${API_BASE}/net/status`, {
      method: "GET",
    });

    if (!res.ok) {
      return {
        ok: false,
        error: "Failed to fetch Net status",
      };
    }

    const data = await res.json();

    return {
      ok: true,
      enabled: Boolean(data.enabled),
    };
  } catch {
    return {
      ok: false,
      error: "Unable to reach Net service",
    };
  }
}
