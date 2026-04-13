import type { PendingIngestionPollItem } from "@/app/hooks/useIngestionStatusPoller";

const KEY = "chat-ui-pending-ingestion";

function normalizePendingIngestionItem(raw: any): PendingIngestionPollItem | null {
  const chatId = typeof raw?.chatId === "string" ? raw.chatId.trim() : "";
  const messageId =
    typeof raw?.messageId === "string" ? raw.messageId.trim() : "";
  const jobId = typeof raw?.jobId === "string" ? raw.jobId.trim() : "";
  const sessionId =
    typeof raw?.sessionId === "string" ? raw.sessionId.trim() : "";

  if (!chatId || !messageId || (!jobId && !sessionId)) {
    return null;
  }

  const keyBase = jobId ? `job:${jobId}` : `session:${sessionId}`;

  return {
    id:
      typeof raw?.id === "string" && raw.id.trim()
        ? raw.id.trim()
        : `${keyBase}:msg:${messageId}`,
    jobId: jobId || null,
    sessionId: sessionId || null,
    chatId,
    messageId,
  };
}

export function loadPendingIngestionItems(): PendingIngestionPollItem[] {
  if (typeof window === "undefined") return [];

  try {
    const raw = JSON.parse(window.localStorage.getItem(KEY) || "[]");
    if (!Array.isArray(raw)) {
      window.localStorage.removeItem(KEY);
      return [];
    }

    const seen = new Set<string>();
    const normalized: PendingIngestionPollItem[] = [];

    for (const item of raw) {
      const next = normalizePendingIngestionItem(item);
      if (!next || seen.has(next.id)) continue;
      seen.add(next.id);
      normalized.push(next);
    }

    window.localStorage.setItem(KEY, JSON.stringify(normalized));
    return normalized;
  } catch (err) {
    console.error(
      "Failed to load pending ingestion items. Resetting storage.",
      err
    );
    window.localStorage.removeItem(KEY);
    return [];
  }
}

export function savePendingIngestionItems(items: PendingIngestionPollItem[]) {
  if (typeof window === "undefined") return;

  try {
    window.localStorage.setItem(KEY, JSON.stringify(items));
  } catch (err) {
    console.error("Failed to save pending ingestion items", err);
  }
}
