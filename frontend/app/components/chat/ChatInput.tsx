"use client";

import React, { forwardRef } from "react";
import TextareaAutosize from "react-textarea-autosize";
import { Send, Square } from "lucide-react";
import PdfUploadButton from "../upload/PdfUploadButton";
import { UploadStatus } from "@/app/hooks/useSmartUpload";

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSend: (value?: string) => void;
  
  // Upload props
  sessionId: string | null;
  onUploadStart?: (file: File) => void;
  onUploadSuccess?: (result: any) => void;
  onUploadError?: (error: string) => void;
  //  NEW: Progress Prop
  onUploadProgress?: (status: UploadStatus, percent: number, label: string) => void;
  netBlocked?: boolean;
  showUploadButton?: boolean;
  placeholderText?: string;

  /* Hidden power features */
  onArrowUp?: () => void;   

  disabled?: boolean;
  isGenerating?: boolean;
  onStop?: () => void;
}

const ChatInput = forwardRef<HTMLTextAreaElement, Props>(
  (
    {
      value,
      onChange,
      onSend,
      sessionId,
      onUploadStart,
      onUploadSuccess,
      onUploadError,
      onUploadProgress, //  Destructure
      netBlocked,
      showUploadButton = true,
      placeholderText,
      onArrowUp,
      disabled = false,
      isGenerating = false,
      onStop,
    },
    ref
  ) => {
    const text = typeof value === "string" ? value : "";
    const effectiveDisabled = disabled || Boolean(netBlocked);
    const canSend = !effectiveDisabled && text.trim().length > 0;
    const fallbackPlaceholder = netBlocked
      ? "Net model rate-limited. Try again soon."
      : effectiveDisabled
        ? "AI is responding..."
        : "Message CHAT UI...";
    const placeholder = placeholderText || fallbackPlaceholder;

    return (
      <div
        aria-disabled={effectiveDisabled}
        className={`
          flex items-end gap-2.5 rounded-3xl border border-white/25 bg-[#1a1a1a] px-2.5 py-2.5 shadow-md transition sm:gap-3 sm:px-3 sm:py-3
          ${effectiveDisabled ? "opacity-60" : ""} focus-within:ring-1 focus-within:ring-white/20
        `}
      >
        {/* ================= UPLOAD BUTTON ================= */}
        {showUploadButton && (
          <div className="pb-1">
              <PdfUploadButton 
                  sessionId={sessionId}
                  iconOnly={true}
                  dataId="chat"
                  disabled={effectiveDisabled || isGenerating}
                  onUploadStart={onUploadStart}
                  onUploadSuccess={onUploadSuccess}
                  onUploadError={onUploadError}
                  onUploadProgress={onUploadProgress} //  Pass it down
              />
          </div>
        )}

        {/* ================= TEXTAREA ================= */}
        <div className="flex-1 min-w-0">
          <TextareaAutosize
            ref={ref}
            value={text}
            spellCheck={false}
            disabled={effectiveDisabled}
            minRows={1}
            maxRows={6}
            placeholder={placeholder}
            onChange={(e) => !effectiveDisabled && onChange(e.target.value)}
            onKeyDown={(e) => {
              if (effectiveDisabled) { e.preventDefault(); return; }
              if (isGenerating && e.key === "Enter" && onStop) { e.preventDefault(); onStop(); return; }
              if (e.key === "ArrowUp" && text.trim() === "" && onArrowUp) { e.preventDefault(); onArrowUp(); return; }
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (canSend) onSend(text); }
            }}
            className="w-full resize-none bg-transparent py-2 text-[13px] text-white outline-none placeholder:text-gray-500 disabled:cursor-not-allowed sm:text-sm"
          />
        </div>

        {/* ================= SEND BUTTON ================= */}
        <div className="pb-1">
            <button
            type="button"
            onClick={() => {
                if (isGenerating && onStop) onStop();
                else if (canSend) onSend(text);
            }}
            disabled={!canSend && !isGenerating}
            className={`
                flex h-8 w-8 items-center justify-center rounded-xl transition sm:h-9 sm:w-9 sm:rounded-2xl
                ${isGenerating 
                    ? "bg-red-500/10 text-red-500 hover:bg-red-500/20" 
                    : canSend ? "bg-white text-black hover:bg-gray-200" : "bg-white/10 text-gray-500 cursor-not-allowed"}
            `}
            >
            {isGenerating ? <Square size={14} fill="currentColor" /> : <Send size={16} />}
            </button>
        </div>
      </div>
    );
  }
);

ChatInput.displayName = "ChatInput";
export default ChatInput;
