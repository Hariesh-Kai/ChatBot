// frontend/lib/netStatus.ts

/**
 * Net status helper
 *
 * Responsibilities:
 * - Fetch /net/status
 * - Cache result briefly to avoid spam
 * - Expose a clean, frontend-safe shape
 *
 * Backend expectations:
 * - 200 → Net available
 * - 429 → Rate limited (Retry-After header)
 * - 503/500 → Net unavailable
 */

export type NetProvider = "groq" | "xai";

export interface NetStatus {
  available: boolean;
  provider?: NetProvider;
  rateLimited: boolean;
  remainingRequests?: number;
  retryAfterSec?: number;
  checkedAt: number;
}

const CACHE_TTL_MS = 30_000; // 30s
let _cache: NetStatus | null = null;

export async function getNetStatus(force = false): Promise<NetStatus> {
  const now = Date.now();

  if (!force && _cache && now - _cache.checkedAt < CACHE_TTL_MS) {
    return _cache;
  }

  try {
    const res = await fetch("/net/status", {
      method: "GET",
      headers: { "Accept": "application/json" },
    });

    // -----------------------------
    // Rate limited
    // -----------------------------
    if (res.status === 429) {
      const retryAfter = Number(res.headers.get("Retry-After") || "30");

      _cache = {
        available: false,
        rateLimited: true,
        retryAfterSec: retryAfter,
        checkedAt: now,
      };
      return _cache;
    }

    // -----------------------------
    // Net unavailable
    // -----------------------------
    if (!res.ok) {
      _cache = {
        available: false,
        rateLimited: false,
        checkedAt: now,
      };
      return _cache;
    }

    // -----------------------------
    // Net available
    // -----------------------------
    const data = await res.json();

    _cache = {
      available: true,
      provider: data.provider,
      rateLimited: false,
      remainingRequests: data.remaining_requests,
      checkedAt: now,
    };

    return _cache;
  } catch {
    // Network / backend down
    _cache = {
      available: false,
      rateLimited: false,
      checkedAt: now,
    };
    return _cache;
  }
}

export function clearNetStatusCache() {
  _cache = null;
}
