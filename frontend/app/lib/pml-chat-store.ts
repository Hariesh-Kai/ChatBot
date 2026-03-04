import { ChatSession, Message } from "./types";

const KEY = "pml-chats";

function normalizeMessage(raw: any): Message {
  let safeStatus = raw?.status ?? "done";
  if (safeStatus === "typing" || safeStatus === "streaming") {
    safeStatus = "done";
  }

  return {
    id: raw?.id ?? crypto.randomUUID(),
    role: raw?.role ?? "assistant",
    model: "base",
    content: typeof raw?.content === "string" ? raw.content : "",
    createdAt: typeof raw?.createdAt === "number" ? raw.createdAt : Date.now(),
    status: safeStatus,
    edited: Boolean(raw?.edited),
    regenerated: Boolean(raw?.regenerated),
    progress: undefined,
  };
}

function normalizeChat(raw: any): ChatSession {
  return {
    id: raw?.id ?? crypto.randomUUID(),
    title: typeof raw?.title === "string" ? raw.title : "",
    model: "base",
    pinned: Boolean(raw?.pinned),
    messages: Array.isArray(raw?.messages) ? raw.messages.map(normalizeMessage) : [],
  };
}

export function loadPmlChats(): ChatSession[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(raw)) {
      localStorage.removeItem(KEY);
      return [];
    }
    const normalized = raw.map(normalizeChat);
    localStorage.setItem(KEY, JSON.stringify(normalized));
    return normalized;
  } catch {
    localStorage.removeItem(KEY);
    return [];
  }
}

export function savePmlChats(chats: ChatSession[]) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(KEY, JSON.stringify(chats));
  } catch {
    // no-op
  }
}

