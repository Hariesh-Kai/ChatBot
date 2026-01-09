// app/lib/types.ts

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

  /* ================= STREAMING (OPTIONAL) ================= */

  /**
   * Legacy / optional streaming hint.
   * (Not required if status is used consistently)
   */
  streaming?: boolean;

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
}

/* ================= CHAT SESSION ================= */

export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  model: KavinModelId;
  pinned?: boolean;
}
