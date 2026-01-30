import { ChatSession, Message } from "./types";

const KEY = "kavin-chats";

/* =========================================
   🔥 NORMALIZERS (CRITICAL)
========================================= */

function normalizeMessage(raw: any): Message {
  // 🔥 SAFETY FIX: If we load a message from disk that says "typing",
  // it is a lie (the stream died when the page closed). Force it to "done".
  let safeStatus = raw?.status ?? "done";
  if (safeStatus === "typing" || safeStatus === "streaming") {
    safeStatus = "done"; // Force unlock UI
  }

  return {
    id: raw?.id ?? crypto.randomUUID(),
    role: raw?.role ?? "assistant",
    content: typeof raw?.content === "string" ? raw.content : "",
    createdAt: typeof raw?.createdAt === "number" ? raw.createdAt : Date.now(),
    status: safeStatus,
    edited: Boolean(raw?.edited),
    regenerated: Boolean(raw?.regenerated),
    // Ensure progress is cleared on load if it was stuck
    progress: undefined, 
  };
}

function normalizeChat(raw: any): ChatSession {
  return {
    id: raw?.id ?? crypto.randomUUID(),
    title: typeof raw?.title === "string" ? raw.title : "",
    model: raw?.model ?? "base",
    pinned: Boolean(raw?.pinned),
    messages: Array.isArray(raw?.messages)
      ? raw.messages.map(normalizeMessage)
      : [],
  };
}

/* =========================================
   LOAD (SAFE)
========================================= */

export function loadChats(): ChatSession[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "[]");

    if (!Array.isArray(raw)) {
      localStorage.removeItem(KEY);
      return [];
    }

    const normalized = raw.map(normalizeChat);

    // 🔁 overwrite broken legacy data immediately
    localStorage.setItem(KEY, JSON.stringify(normalized));

    return normalized;
  } catch (err) {
    console.error(" Failed to load chats. Resetting storage.", err);
    localStorage.removeItem(KEY);
    return [];
  }
}

/* =========================================
   SAVE (SAFE)
========================================= */

export function saveChats(chats: ChatSession[]) {
  if (typeof window === "undefined") return;

  try {
    localStorage.setItem(KEY, JSON.stringify(chats));
  } catch (err) {
    console.error(" Failed to save chats", err);
  }
}