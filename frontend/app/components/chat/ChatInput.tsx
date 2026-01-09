// frontend/components/chat/ChatInput.tsx

"use client";

import React, { forwardRef } from "react";
import TextareaAutosize from "react-textarea-autosize";
import { Send, Square } from "lucide-react";

/* ================= PROPS ================= */
interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: (value?: string) => void;

  /* Hidden power features */
  onArrowUp?: () => void;   // edit last message
  onEscape?: () => void;    // cancel edit

  disabled?: boolean;
  isEditing?: boolean;

  isGenerating?: boolean;
  onStop?: () => void;
}

/**
 * ChatInput
 */
const ChatInput = forwardRef<HTMLTextAreaElement, Props>(
  (
    {
      value,
      onChange,
      onSend,
      onArrowUp,
      onEscape,
      disabled = false,
      isEditing = false,
      isGenerating = false,
      onStop,
    },
    ref
  ) => {
    const text = typeof value === "string" ? value : "";
    const canSend = !disabled && text.trim().length > 0;

    return (
      <div
        aria-disabled={disabled}
        className={`
          flex items-end gap-3
          rounded-xl px-4 py-3
          border border-white/10
          bg-[#1a1a1a]
          shadow-md transition
          ${disabled ? "opacity-60" : ""}
          focus-within:ring-1 focus-within:ring-white/20
        `}
      >
        {/* ================= TEXTAREA ================= */}
        <div className="flex-1">
          <TextareaAutosize
            ref={ref}
            value={text}
            disabled={disabled}
            minRows={1}
            maxRows={6}
            placeholder={
              disabled
                ? "AI is responding…"
                : isEditing
                ? "Edit message…"
                : "Message KAVIN"
            }
            onChange={(e) => {
              if (disabled) return;
              onChange(e.target.value);
            }}
            onKeyDown={(e) => {
              if (disabled) {
                e.preventDefault();
                return;
              }

              /* Stop generation on Enter while generating */
              if (isGenerating && e.key === "Enter" && onStop) {
                e.preventDefault();
                onStop();
                return;
              }

              /* ↑ Arrow → edit last message */
              if (e.key === "ArrowUp" && text.trim() === "" && onArrowUp) {
                e.preventDefault();
                onArrowUp();
                return;
              }

              /* ESC → cancel edit */
              if (e.key === "Escape" && onEscape) {
                e.preventDefault();
                onEscape();
                return;
              }

              /* Ctrl + Enter → send */
              if (e.key === "Enter" && e.ctrlKey) {
                e.preventDefault();
                if (canSend) onSend(text);
                return;
              }

              /* Enter → send */
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (canSend) onSend(text);
              }
            }}
            className="
              w-full resize-none bg-transparent
              text-sm text-white outline-none
              placeholder:text-gray-500
              disabled:cursor-not-allowed
            "
          />
        </div>

        {/* ================= SEND / STOP BUTTON ================= */}
        <button
          type="button"
          onClick={() => {
            if (isGenerating && onStop) {
              onStop();
            } else if (canSend) {
              onSend(text);
            }
          }}
          disabled={!canSend && !isGenerating}
          className={`
            flex h-9 w-9 items-center justify-center
            rounded-full transition
            ${
              isGenerating
                ? "bg-red-500/10 text-red-500 hover:bg-red-500/20 hover:text-red-400"
                : canSend
                ? "bg-white text-black hover:bg-gray-200"
                : "bg-white/10 text-gray-500 cursor-not-allowed"
            }
          `}
          title={isGenerating ? "Stop generating" : "Send"}
        >
          {isGenerating ? (
            <Square size={14} fill="currentColor" />
          ) : (
            <Send size={16} />
          )}
        </button>
      </div>
    );
  }
);

ChatInput.displayName = "ChatInput";

export default ChatInput;
