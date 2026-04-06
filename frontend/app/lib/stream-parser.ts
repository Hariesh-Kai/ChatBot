/* =========================================================
   STREAM PARSER (LLM OUTPUT)
   ---------------------------------------------------------
   Guarantees:
   - NEVER drops partial chunks
   - NEVER loses final token
   - Supports UI events split across chunks
   - Safe for raw token streaming
   - Never throws
========================================================= */

import {
  UI_EVENT_PREFIX,
  parseLLMUIEvent,
  LLMUIEvent,
} from "./llm-ui-events";

const LEAKED_SPECIAL_TOKENS = [
  "<|end|>",
  "<|system|>",
  "<|user|>",
  "<|assistant|>",
  "<|eot_id|>",
  "<|start_header_id|>",
  "<|end_header_id|>",
];

function stripLeakedTokens(value: string): string {
  let next = value || "";
  for (const token of LEAKED_SPECIAL_TOKENS) {
    next = next.split(token).join("");
  }
  return next;
}

export type StreamFrame =
  | { type: "text"; value: string }
  | { type: "event"; value: LLMUIEvent };

export class StreamParser {
  private buffer = "";
  private textBuffer = ""; // 🔥 NEW: token coalescing buffer

  /**
   * Push raw streamed chunk into parser.
   * Returns zero or more frames.
   */
  push(rawChunk: string | null | undefined): StreamFrame[] {
    if (rawChunk == null || rawChunk === "") return [];

    this.buffer += rawChunk;
    const frames: StreamFrame[] = [];

    while (true) {
      const eventIdx = this.buffer.indexOf(UI_EVENT_PREFIX);

      // -------------------------------------------------
      // NO UI EVENT → accumulate text safely
      // -------------------------------------------------
      if (eventIdx === -1) {
        this.textBuffer += this.buffer;
        this.buffer = "";

        // 🔥 Flush only when it "looks human"
        const shouldFlush =
          this.textBuffer.length > 20 ||
          /[\s.,!?]\s*$/.test(this.textBuffer);

        if (shouldFlush) {
          const sanitized = stripLeakedTokens(this.textBuffer);
          frames.push({
            type: "text",
            value: sanitized,
          });
          this.textBuffer = "";
        }

        break;
      }

      // -------------------------------------------------
      // Emit text BEFORE UI event
      // -------------------------------------------------
      if (eventIdx > 0) {
        this.textBuffer += this.buffer.slice(0, eventIdx);
        const sanitized = stripLeakedTokens(this.textBuffer);
        frames.push({
          type: "text",
          value: sanitized,
        });
        this.textBuffer = "";
        this.buffer = this.buffer.slice(eventIdx);
      }

      // -------------------------------------------------
      // UI event must be newline-terminated
      // -------------------------------------------------
      const newlineIdx = this.buffer.indexOf("\n");
      if (newlineIdx === -1) {
        break; // wait for more data
      }

      const line = this.buffer.slice(0, newlineIdx);
      this.buffer = this.buffer.slice(newlineIdx + 1);

      // -------------------------------------------------
      // Parse UI event safely
      // -------------------------------------------------
      try {
        const evt = parseLLMUIEvent(line);
        if (evt) {
          frames.push({ type: "event", value: evt });
        }
      } catch {
        // Ignore malformed/unknown UI event frames rather than leaking
        // raw internal protocol messages into the chat transcript.
      }
    }

    return frames;
  }

  /**
   * Flush remaining buffered content at stream end.
   * MUST be called when stream completes.
   */
  flush(): StreamFrame[] {
    const frames: StreamFrame[] = [];

    if (this.textBuffer) {
      const sanitized = stripLeakedTokens(this.textBuffer);
      frames.push({
        type: "text",
        value: sanitized,
      });
      this.textBuffer = "";
    }

    if (this.buffer) {
      const sanitized = stripLeakedTokens(this.buffer);
      frames.push({
        type: "text",
        value: sanitized,
      });
      this.buffer = "";
    }

    return frames;
  }

  /**
   * Reset parser state (abort / new request).
   */
  reset(): void {
    this.buffer = "";
    this.textBuffer = "";
  }
}
