// frontend/app/lib/types.ts

import type { ChatUIModelId } from "./chat-ui-models";

/* ================= MESSAGE ROLES ================= */

/**
 * Message roles inside the chat.
 * - user: human input
 * - assistant: LLM response
 * - system: internal / lifecycle / status messages
 */
export type Role = "user" | "assistant" | "system";

/* ================= MESSAGE STATUS ================= */

/**
 * Message lifecycle status.
 * Mostly relevant for assistant messages.
 */
export type MessageStatus =
  | "typing"        // initial thinking (TypingIndicator)
  | "streaming"     // tokens streaming OR processing bubble
  | "progress"      // 🔥 determinate progress (PDF upload)
  | "done"          // final answer rendered
  | "error";        // error state (metadata / pipeline)

/* ================= SOURCE TYPE ================= */

export interface RagSource {
  id: string;
  fileName: string;
  page: number;

  bbox?: any;
  chunk_type?: "text" | "parent" | "child" | "image";
  section?: string;

  company_document_id?: string;
  revision_number?: number;
  chunk_id?: string;

  text?: string;
  score?: number;
}



/* ================= MESSAGE ================= */

/**
 * Core chat message type.
 */
export interface Message {
  id: string;
  role: Role;
  model?: ChatUIModelId;

  /**
   * Message text.
   * We treat this as required (empty string if no content) to prevent UI crashes.
   */
  content: string;

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
   * Used for 'progress' (upload) and 'streaming' (processing bubble) states.
   */
  progress?: number;

  /**
   * Optional progress label.
   * Example: "Chunking PDF", "Embedding vectors"
   */
  progressLabel?: string;

  /* ================= 📚 SOURCES ================= */
  
  /**
   * List of citations used to generate this message.
   * Used for the "Source Viewer" modal.
   */
  sources?: RagSource[];

  /**
   * Confidence payload (if emitted by backend).
   */
  confidence?: {
    confidence: number;
    level: "high" | "medium" | "low";
  };
}

/* ================= CHAT SESSION ================= */

export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  model: ChatUIModelId;
  pinned?: boolean;
}
