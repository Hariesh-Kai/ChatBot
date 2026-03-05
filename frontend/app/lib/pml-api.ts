import { API_BASE } from "./config";

const PML_API_BASE = (
  process.env.NEXT_PUBLIC_PML_API_BASE ?? API_BASE
).replace(/\/+$/, "");

export type PmlHistoryMessage = {
  role: "user" | "assistant";
  content: string;
};

export type PmlChatRequest = {
  session_id: string;
  question: string;
  history?: PmlHistoryMessage[];
  max_tokens?: number;
};

export type PmlStatusResponse = {
  ok: boolean;
  configured: boolean;
  base_url?: string;
  model?: string;
};

export type PmlLearnRequest = {
  code: string;
  note?: string;
};

export type PmlLearnResponse = {
  ok: boolean;
  store_path?: string;
  count?: number;
  example?: {
    id?: string;
    code?: string;
    note?: string;
    source?: string;
    created_at?: string;
    updated_at?: string;
  };
};

export type PmlTemplate = {
  id: string;
  code: string;
  note?: string;
  source?: string;
  created_at?: string;
  updated_at?: string;
};

export type PmlTemplateListResponse = {
  ok: boolean;
  templates: PmlTemplate[];
  store_path?: string;
  count?: number;
};

export type PmlTemplateDeleteResponse = {
  ok: boolean;
  deleted_id?: string;
  store_path?: string;
  count?: number;
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
    // no-op
  }

  return text || "Request failed";
}

export async function fetchPmlStatus(): Promise<PmlStatusResponse> {
  const res = await fetch(`${PML_API_BASE}/pml-chat/status`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

export async function streamPmlChat(
  payload: PmlChatRequest,
  signal?: AbortSignal
): Promise<ReadableStream<Uint8Array>> {
  const res = await fetch(`${PML_API_BASE}/pml-chat/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(payload),
    signal,
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  if (!res.body) throw new Error("PML stream missing response body");

  return res.body;
}

export async function learnPmlTemplate(
  payload: PmlLearnRequest
): Promise<PmlLearnResponse> {
  const res = await fetch(`${PML_API_BASE}/pml-chat/learn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify(payload),
  });

  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

export async function fetchPmlTemplates(limit = 100): Promise<PmlTemplateListResponse> {
  const q = Number.isFinite(limit) ? `?limit=${Math.max(1, Math.min(500, Math.floor(limit)))}` : "";
  const res = await fetch(`${PML_API_BASE}/pml-chat/learn/templates${q}`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}

export async function deletePmlTemplate(templateId: string): Promise<PmlTemplateDeleteResponse> {
  const cleanId = (templateId || "").trim();
  if (!cleanId) {
    throw new Error("templateId is required");
  }
  const res = await fetch(`${PML_API_BASE}/pml-chat/learn/templates/${encodeURIComponent(cleanId)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await normalizeError(res));
  return res.json();
}
