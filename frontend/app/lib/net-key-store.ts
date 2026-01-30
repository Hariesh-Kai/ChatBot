/**
 * net-key-store.ts
 *
 * Frontend-only persistence for KavinBase Net API key.
 *
 * IMPORTANT RULES:
 * - This file ONLY stores the key
 * - It does NOT decide if Net is enabled
 * - Backend (/net/status) is the source of truth
 * - UI must NOT assume Net works just because a key exists
 */

const STORAGE_KEY = "kavinbase_net_api_key";

/* =========================================================
   ENV GUARD
========================================================= */

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

/* =========================================================
   READ
========================================================= */

/**
 * Get stored Net API key.
 * Returns null if missing or invalid.
 */
export function getNetApiKey(): string | null {
  if (!isBrowser()) return null;

  try {
    const key = window.localStorage.getItem(STORAGE_KEY);
    if (!key) return null;

    const trimmed = key.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

/* =========================================================
   WRITE
========================================================= */

/**
 * Store Net API key locally.
 * DOES NOT verify or activate Net.
 */
export function setNetApiKey(key: string): void {
  if (!isBrowser()) return;

  const trimmed = key?.trim();
  if (!trimmed) {
    throw new Error("Net API key cannot be empty");
  }

  try {
    window.localStorage.setItem(STORAGE_KEY, trimmed);
  } catch {
    throw new Error("Failed to persist Net API key");
  }
}

/* =========================================================
   DELETE
========================================================= */

/**
 * Remove stored Net API key.
 * Used when user logs out or disables Net.
 */
export function clearNetApiKey(): void {
  if (!isBrowser()) return;

  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // intentionally silent
  }
}

/* =========================================================
   STATUS HELPERS
========================================================= */

/**
 * Indicates whether a key exists locally.
 *
 * DOES NOT mean Net is enabled.
 * Always check backend /net/status.
 */
export function hasNetApiKey(): boolean {
  return Boolean(getNetApiKey());
}
