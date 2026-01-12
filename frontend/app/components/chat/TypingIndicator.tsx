"use client";

import { Loader2, HardDrive, Search, Cpu, AlertCircle } from "lucide-react";
import Avatar from "../ui/Avatar";

/* ================= PROPS ================= */

interface TypingIndicatorProps {
  /** "KavinBase", "System", etc. */
  modelLabel?: string;
  
  /** "is thinking...", "Uploading...", "Searching..." */
  label?: string;
  
  /** 0-100. If present, shows progress ring/bar. */
  progress?: number;
  
  /** "typing" | "uploading" | "searching" | "processing" | "error" */
  type?: "typing" | "uploading" | "searching" | "processing" | "error";
}

/* ================= COMPONENT ================= */

export default function TypingIndicator({
  modelLabel = "KAVIN",
  label = "is thinking...",
  progress,
  type = "typing",
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
      <div className="flex flex-col justify-center pt-0.5">
        <div className="flex items-center gap-2 text-xs">
          <span className="font-semibold text-gray-200">{modelLabel}</span>
          
          <span className="text-gray-600">•</span>
          
          <span className="flex items-center gap-2 text-gray-400">
            {renderIcon()}
            <span className={type === "error" ? "text-red-400" : "italic"}>
              {label}
            </span>
          </span>
        </div>

        {/* Progress Bar (Optional) */}
        {renderProgress()}
      </div>
    </div>
  );
}