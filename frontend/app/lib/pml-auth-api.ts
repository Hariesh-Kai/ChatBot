import { API_BASE } from "./config";

const PML_API_BASE = (
  process.env.NEXT_PUBLIC_PML_API_BASE ?? API_BASE
).replace(/\/+$/, "");

export type PmlAuthUser = {
  username: string;
  email: string;
  role?: string;
};

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
  } catch {
    // ignore
  }

  return text || "Request failed";
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

export async function waitForPmlBackendReady({
  timeoutMs = 15000,
  intervalMs = 600,
}: {
  timeoutMs?: number;
  intervalMs?: number;
} = {}): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${PML_API_BASE}/health`, { cache: "no-store" });
      if (res.ok) return true;
    } catch {
      // ignore and retry
    }
    await sleep(intervalMs);
  }
  return false;
}

export async function pmlAuthLogin(
  identifier: string,
  password: string
): Promise<PmlAuthUser> {
  const res = await fetch(`${PML_API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ identifier, password }),
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

export async function pmlAuthLogout(): Promise<void> {
  const res = await fetch(`${PML_API_BASE}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await normalizeError(res));
}

export async function pmlAuthMe(): Promise<PmlAuthUser | null> {
  try {
    const res = await fetch(`${PML_API_BASE}/auth/me`, {
      credentials: "include",
      cache: "no-store",
    });
    if (res.status === 401) return null;
    if (!res.ok) throw new Error(await normalizeError(res));
    return res.json();
  } catch {
    return null;
  }
}

