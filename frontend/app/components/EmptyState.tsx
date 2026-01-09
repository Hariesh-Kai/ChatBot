"use client";

import { useState, useRef } from "react";
import PromptCard from "./chat/PromptCard";
import { Send } from "lucide-react";

interface Props {
  onSend: (text?: string) => void;
  disabled?: boolean;
}

export default function EmptyState({
  onSend,
  disabled = false,
}: Props) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const canSend = !disabled && value.trim().length > 0;

  function handleSubmit(text?: string) {
    if (disabled) return;

    const finalText = (text ?? value).trim();
    if (!finalText) return;

    onSend(finalText);
    setValue("");
  }

  return (
    <div className="flex h-full w-full items-center justify-center">
      <div className="w-full max-w-2xl px-4 text-center animate-fade-in">

        {/* ---------- TITLE ---------- */}
        <h1 className="text-2xl font-semibold text-white">
          How can I help you today?
        </h1>

        <p className="mt-2 text-sm text-gray-400">
          Ask a question or upload a PDF to get started
        </p>

        {/* ---------- INPUT ---------- */}
        <div className="mt-8">
          <div
            className={`
              flex items-end gap-3
              rounded-xl px-4 py-3
              border border-white/10
              bg-[#1a1a1a]
              shadow-md transition
              focus-within:ring-1 focus-within:ring-white/20
              ${disabled ? "opacity-60" : ""}
            `}
          >
            <input
              ref={inputRef}
              value={value}
              disabled={disabled}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (disabled) return;

                if (e.key === "Enter") {
                  e.preventDefault();
                  if (canSend) handleSubmit();
                }
              }}
              placeholder={
                disabled
                  ? "AI is responding…"
                  : "Ask anything…"
              }
              className="
                flex-1 bg-transparent
                text-sm text-white
                placeholder:text-gray-500
                outline-none
                disabled:cursor-not-allowed
              "
            />

            {/* SEND BUTTON */}
            <button
              type="button"
              onClick={() => canSend && handleSubmit()}
              disabled={!canSend}
              className={`
                flex h-9 w-9 items-center justify-center
                rounded-full transition
                ${
                  canSend
                    ? "bg-white text-black hover:bg-gray-200"
                    : "bg-white/10 text-gray-500 cursor-not-allowed"
                }
              `}
              aria-label="Send"
              title="Send"
            >
              <Send size={16} />
            </button>
          </div>
        </div>

        {/* ---------- PROMPT CARDS ---------- */}
        <div className="mt-8 grid w-full grid-cols-1 gap-4 sm:grid-cols-2">
          <PromptCard
            title="Summarize a PDF"
            description="Upload a document and get a concise summary"
            onClick={() => handleSubmit("Summarize this PDF")}
          />
          <PromptCard
            title="Ask about requirements"
            description="Clarify design specs, scope, or constraints"
            onClick={() =>
              handleSubmit("What are the requirements for this?")
            }
          />
          <PromptCard
            title="Extract key points"
            description="Pull tables, bullets, or highlights"
            onClick={() =>
              handleSubmit("Extract the key points from this")
            }
          />
          <PromptCard
            title="Explain technical sections"
            description="Understand complex engineering content"
            onClick={() =>
              handleSubmit("Explain the technical sections")
            }
          />
        </div>
      </div>
    </div>
  );
}
