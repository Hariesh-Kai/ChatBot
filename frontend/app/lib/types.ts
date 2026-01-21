// frontend/app/lib/types.ts

import type { KavinModelId } from "./kavin-models";

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
  fileName: string; // ✅ Fixed: Must be camelCase to match Backend & MessageBubble
  page: number;
  
  // ✅ FIX: Allow array (from backend) or string to handle legacy/mismatches safely
  bbox?: any;    

  company_doc_id?: string;
  revision?: number;
  text?: string;    // Snippet text
  score?: number;
}

/* ================= MESSAGE ================= */

/**
 * Core chat message type.
 */
export interface Message {
  id: string;
  role: Role;

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
}

/* ================= CHAT SESSION ================= */

export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  model: KavinModelId;
  pinned?: boolean;
}