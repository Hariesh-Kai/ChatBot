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

export type StreamFrame =
  | { type: "text"; value: string }
  | { type: "event"; value: LLMUIEvent };

export class StreamParser {
  private buffer = "";

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
      // No UI event prefix found → emit nothing yet
      // (wait for more data to avoid partial loss)
      // -------------------------------------------------
      if (eventIdx === -1) {
        break;
      }

      // -------------------------------------------------
      // Emit text BEFORE UI event
      // -------------------------------------------------
      if (eventIdx > 0) {
        frames.push({
          type: "text",
          value: this.buffer.slice(0, eventIdx),
        });
        this.buffer = this.buffer.slice(eventIdx);
      }

      // -------------------------------------------------
      // UI event must be newline-terminated
      // -------------------------------------------------
      const newlineIdx = this.buffer.indexOf("\n");
      if (newlineIdx === -1) {
        // Incomplete UI event → wait for more data
        break;
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
        } else {
          frames.push({ type: "text", value: line });
        }
      } catch {
        frames.push({ type: "text", value: line });
      }
    }

    return frames;
  }

  /**
   * Flush remaining buffered content at stream end.
   * MUST be called when stream completes.
   */
  flush(): StreamFrame[] {
    if (!this.buffer) return [];

    const remaining = this.buffer;
    this.buffer = "";

    // Try parse as UI event first
    if (remaining.startsWith(UI_EVENT_PREFIX)) {
      try {
        const evt = parseLLMUIEvent(remaining);
        if (evt) {
          return [{ type: "event", value: evt }];
        }
      } catch {
        // fall through
      }
    }

    // Fallback: emit remaining text
    return [{ type: "text", value: remaining }];
  }

  /**
   * Reset parser state (abort / new request).
   */
  reset(): void {
    this.buffer = "";
  }
}
