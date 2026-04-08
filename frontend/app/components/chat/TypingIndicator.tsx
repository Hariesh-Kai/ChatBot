"use client";

import { HardDrive, Search, Cpu, AlertCircle } from "lucide-react";
import Avatar from "../ui/Avatar";

/* ================= PROPS ================= */

interface TypingIndicatorProps {
  /** "Chat UI", "System", etc. */
  modelLabel?: string;
  
  /** "is thinking...", "Uploading...", "Searching..." */
  label?: string;
  
  /** 0-100. If present, shows progress ring/bar. */
  progress?: number;
  
  /** "typing" | "uploading" | "searching" | "processing" | "error" */
  type?: "typing" | "uploading" | "searching" | "processing" | "error";

  /** Optional compact action shown inside the bubble. */
  action?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
  };
}

/* ================= COMPONENT ================= */

export default function TypingIndicator({
  modelLabel = "KAVIN",
  label = "is thinking...",
  progress,
  type = "typing",
  action,
}: TypingIndicatorProps) {

  // --- 1. Choose Icon based on Type ---
  const renderIcon = () => {
    switch (type) {
      case "uploading":
        return <HardDrive size={14} className="text-blue-400 animate-pulse" />;
      case "searching":
        return <Search size={14} className="text-yellow-400 animate-bounce" />;
      case "processing":
        return <Cpu size={14} className="text-purple-400 animate-pulse" />;
      case "error":
        return <AlertCircle size={14} className="text-red-500" />;
      default: // typing
        return (
          <div className="flex gap-1 items-center h-full">
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:0ms]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:150ms]" />
            <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:300ms]" />
          </div>
        );
    }
  };

  // --- 2. Compact Progress Bar ---
  const renderProgress = () => {
    if (progress === undefined) return null;
    const safe = Math.min(100, Math.max(0, progress));
    
    return (
      <div className="mt-1.5 h-1 w-32 overflow-hidden rounded-full bg-white/10">
        <div 
            className="h-full bg-blue-500 transition-all duration-300 ease-out" 
            style={{ width: `${safe}%` }} 
        />
      </div>
    );
  };

  return (
    <div className="flex items-start gap-3 animate-fade-in py-2">
      {/* Avatar (Left) */}
      <Avatar role="assistant" />

      {/* Content (Right) */}
      <div className="min-w-0 rounded-xl border border-white/5 bg-[#1f1f1f] px-4 py-3 shadow-sm">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-gray-200">{modelLabel}</span>
          
          <span className="text-gray-600">.</span>
          
          <span className="flex min-w-0 items-center gap-2 text-gray-400">
            {renderIcon()}
            <span className={type === "error" ? "text-red-400" : "italic"}>
              {label}
            </span>
          </span>

          {action && (
            <button
              type="button"
              onClick={action.onClick}
              disabled={action.disabled}
              title={action.label}
              aria-label={action.label}
              className="ml-2 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-red-400/30 bg-red-500/10 text-red-100 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="h-2.5 w-2.5 rounded-[2px] bg-current" />
            </button>
          )}
        </div>

        {/* Progress Bar (Optional) */}
        {renderProgress()}
      </div>
    </div>
  );
}
