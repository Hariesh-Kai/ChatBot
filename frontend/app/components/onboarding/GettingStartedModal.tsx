"use client";

import { useState, useEffect, useCallback } from "react";
import {
  X,
  Upload,
  MessageSquare,
  Layers,
  Keyboard,
  ChevronLeft,
  ChevronRight,
  Sparkles,
} from "lucide-react";

interface GettingStartedModalProps {
  open: boolean;
  onClose: () => void;
  onOpenShortcuts: () => void;
}

const STEPS = [
  {
    icon: <Upload size={28} className="text-blue-400" />,
    title: "Upload a PDF",
    description:
      "Drag-and-drop or click the upload button to add your engineering document, report, or specification.",
    tip: "Tip: Press Ctrl/Cmd + Shift + U to upload from anywhere.",
    color: "text-blue-400",
    bg: "bg-blue-400/10",
  },
  {
    icon: <MessageSquare size={28} className="text-emerald-400" />,
    title: "Ask Questions",
    description:
      "Type any question about your document. Chat UI retrieves the exact passages and cites the page number for every fact.",
    tip: "Tip: Press Ctrl/Cmd + K to jump to the message box instantly.",
    color: "text-emerald-400",
    bg: "bg-emerald-400/10",
  },
  {
    icon: <Layers size={28} className="text-purple-400" />,
    title: "Switch Models",
    description:
      "Use Lite for instant CPU answers, Base for deeper quality, or Net to route through cloud models like Groq.",
    tip: "Tip: Lite is best for quick lookups; Base for detailed analysis.",
    color: "text-purple-400",
    bg: "bg-purple-400/10",
  },
  {
    icon: <Keyboard size={28} className="text-amber-400" />,
    title: "Use Shortcuts",
    description:
      "Navigate the app at full speed with keyboard shortcuts — new chat, toggle sidebar, upload, and more.",
    tip: "Press ? anywhere to see all shortcuts.",
    color: "text-amber-400",
    bg: "bg-amber-400/10",
  },
];

export default function GettingStartedModal({
  open,
  onClose,
  onOpenShortcuts,
}: GettingStartedModalProps) {
  const [step, setStep] = useState(0);

  const prev = useCallback(() => setStep((s) => Math.max(0, s - 1)), []);
  const next = useCallback(
    () => setStep((s) => Math.min(STEPS.length - 1, s + 1)),
    []
  );

  // Reset to step 0 every time modal opens
  useEffect(() => {
    if (open) setStep(0);
  }, [open]);

  // Arrow key + Escape navigation
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        prev();
      } else if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, next, prev, onClose]);

  if (!open) return null;

  const current = STEPS[step];
  const isFirst = step === 0;
  const isLast = step === STEPS.length - 1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#111] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3.5 border-b border-white/10">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <Sparkles size={15} className="text-yellow-400" />
            Welcome to Chat UI
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-gray-500 hover:text-white hover:bg-white/10 transition-colors"
            title="Close (Esc)"
          >
            <X size={15} />
          </button>
        </div>

        {/* Step Content */}
        <div className="px-6 pt-7 pb-5 min-h-[220px] flex flex-col gap-4">
          <div className={`flex items-center gap-3 p-3 rounded-lg ${current.bg} w-fit`}>
            {current.icon}
            <span className={`text-lg font-semibold ${current.color}`}>
              {current.title}
            </span>
          </div>

          <p className="text-sm text-gray-300 leading-relaxed">
            {current.description}
          </p>

          <div className="rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-400">
            {current.tip}
          </div>
        </div>

        {/* Progress Dots */}
        <div className="flex justify-center gap-2 pb-2">
          {STEPS.map((_, i) => (
            <button
              key={i}
              onClick={() => setStep(i)}
              className={`h-1.5 rounded-full transition-all duration-200 ${i === step ? "bg-white w-5" : "bg-white/25 w-1.5"
                }`}
              aria-label={`Step ${i + 1}`}
            />
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3.5 border-t border-white/10">
          <div className="flex items-center gap-1 text-[10px] text-gray-600 select-none">
            <span className="border border-white/15 rounded px-1 py-0.5 font-mono">←</span>
            <span className="border border-white/15 rounded px-1 py-0.5 font-mono">→</span>
            <span className="ml-1">Navigate</span>
          </div>

          <div className="flex items-center gap-2">
            {!isFirst && (
              <button
                onClick={prev}
                className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
              >
                <ChevronLeft size={13} />
                Back
              </button>
            )}

            {!isLast ? (
              <button
                onClick={next}
                className="flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium bg-white text-black hover:bg-gray-200 transition-colors"
              >
                Next
                <ChevronRight size={13} />
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => {
                    onClose();
                    onOpenShortcuts();
                  }}
                  className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
                >
                  <Keyboard size={13} />
                  Shortcuts
                </button>
                <button
                  onClick={onClose}
                  className="rounded-md px-3 py-1.5 text-xs font-medium bg-white text-black hover:bg-gray-200 transition-colors"
                >
                  {"Let's go →"}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
