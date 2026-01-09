"use client";

import { ChevronDown, ChevronRight, BrainCircuit } from "lucide-react";
import { useState } from "react";

export default function ThinkingDisclosure({ content }: { content: string }) {
  const [open, setOpen] = useState(false);

  if (!content) return null;

  return (
    <div className="mb-4 rounded-lg border border-white/10 bg-white/5 overflow-hidden animate-in fade-in slide-in-from-top-2 duration-300">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-gray-400 hover:bg-white/5 hover:text-gray-200 transition-colors"
      >
        <BrainCircuit size={14} className={!open ? "animate-pulse" : ""} />
        <span>{open ? "Hide thought process" : "View thought process"}</span>
        <div className="ml-auto">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
      </button>

      {open && (
        <div className="px-3 py-3 text-xs text-gray-400 font-mono border-t border-white/5 bg-black/20 whitespace-pre-wrap leading-relaxed">
          {content}
        </div>
      )}
    </div>
  );
}