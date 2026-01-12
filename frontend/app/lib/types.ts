// frontend/app/lib/types.ts

import type { KavinModelId } from "./kavin-models";

/* ================= MESSAGE ROLES ================= */

/**
 * Message roles inside the chat.
 * - user: human input
 * - assistant: LLM response
 * - system: internal / lifecycle / status messages
 */
export type MessageRole =
  | "user"
  | "assistant"
  | "system";

/* ================= MESSAGE STATUS ================= */

/**
 * Message lifecycle status.
 * Mostly relevant for assistant messages.
 */
export type MessageStatus =
  | "typing"        // initial thinking (TypingIndicator)
  | "streaming"     // tokens streaming
  | "progress"      // 🔥 determinate progress (PDF / ingestion)
  | "done"          // final answer rendered
  | "error";        // error state (metadata / pipeline)

/* ================= SOURCE TYPE (✅ NEW) ================= */

export interface RagSource {
  id: string;
  filename: string; // Changed from fileName to match backend usually, or keep fileName if strict
  page: number;
  bbox?: string; // JSON string "[[x,y], ...]"
  company_doc_id?: string;
  revision?: number;
  text?: string; // Often useful to have the text snippet
  score?: number;
}

/* ================= MESSAGE ================= */

/**
 * Core chat message type.
 */
export interface Message {
  id: string;
  role: MessageRole;

  /**
   * Message text.
   * Optional because:
   * - progress messages don’t have text
   * - typing indicator is UI-only
   */
  content?: string;

  createdAt: number;

  /**
   * Lifecycle status.
   * - user messages → usually undefined
   * - assistant messages → typing / streaming / progress / done
   */
  status?: MessageStatus;

  /* ================= UI / LIFECYCLE FLAGS ================= */

  edited?: boolean;
  regenerated?: boolean;

  /* ================= 🔥 PROGRESS (PDF / JOB ONLY) ================= */

  /**
   * 0–100 progress value.
   * Used ONLY when status === "progress"
   */
  progress?: number;

  /**
   * Optional progress label.
   * Example: "Chunking PDF", "Embedding vectors"
   */
  progressLabel?: string;

  /* ================= 📚 SOURCES (✅ NEW) ================= */
  
  /**
   * List of citations used to generate this message.
   * Used for the "Source Viewer" modal.
   */
  sources?: RagSource[];
}

/* ================= CHAT SESSION ================= */

export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  model: KavinModelId;
  pinned?: boolean;
}