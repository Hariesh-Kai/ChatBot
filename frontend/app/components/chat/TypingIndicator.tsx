// app/components/chat/TypingIndicator.tsx
"use client";

import Avatar from "../ui/Avatar";

/* ================= PROPS ================= */

interface TypingIndicatorProps {
  /** Model label shown to user (e.g. "KavinBase Lite") */
  modelLabel?: string;

  /** Activity text (e.g. "is typing", "embedding chunks") */
  label?: string;

  /** Determinate progress (0–100). If undefined → typing dots */
  progress?: number;

  /** Error state (halts animation & progress) */
  error?: boolean;

  /** Cancelled / aborted state */
  cancelled?: boolean;
}

/* ================= COMPONENT ================= */

export default function TypingIndicator({
  modelLabel = "KAVIN",
  label = "is typing",
  progress,
  error = false,
  cancelled = false,
}: TypingIndicatorProps) {
  /* ---------------- STATE DERIVATION ---------------- */

  const hasProgress = typeof progress === "number";
  const isDeterminate = hasProgress && !error && !cancelled;
  const isTyping = !hasProgress && !error && !cancelled;

  const safeProgress = hasProgress
    ? Math.min(100, Math.max(0, progress!))
    : 0;

  /* ---------------- SVG PROGRESS MATH ---------------- */

  const radius = 10;
  const circumference = 2 * Math.PI * radius;
  const dashOffset =
    circumference * (1 - safeProgress / 100);

  /* ---------------- LABEL RESOLUTION ---------------- */

  const resolvedLabel = error
    ? "encountered an error"
    : cancelled
    ? "stopped"
    : label;

  /* ================= RENDER ================= */

  return (
    <div
      className={`flex items-center gap-3 transition-opacity duration-200 ${
        error || cancelled ? "opacity-80" : "opacity-100"
      }`}
      aria-live="polite"
    >

      {/* Content */}
      <div className="flex items-center gap-3 text-sm">
        {/* ================= DETERMINATE PROGRESS ================= */}
        {isDeterminate && (
          <div className="relative h-6 w-6 shrink-0">
            <svg
              className="h-full w-full -rotate-90"
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              {/* Track */}
              <circle
                cx="12"
                cy="12"
                r={radius}
                stroke="rgba(255,255,255,0.15)"
                strokeWidth="3"
                fill="none"
              />

              {/* Progress */}
              <circle
                cx="12"
                cy="12"
                r={radius}
                stroke="#3b82f6"
                strokeWidth="3"
                fill="none"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                strokeLinecap="round"
                className="transition-all duration-300 ease-out"
              />
            </svg>
          </div>
        )}

        {/* ================= TEXT ================= */}
        <div
          className={`flex items-center gap-2 ${
            error
              ? "text-red-400"
              : cancelled
              ? "text-gray-500"
              : "text-gray-400"
          }`}
        >
          <span>
            <span className="font-medium text-gray-300">
              {modelLabel}
            </span>{" "}
            {resolvedLabel}
            {isDeterminate && ` (${safeProgress}%)`}
          </span>

          {/* ================= TYPING DOTS ================= */}
          {isTyping && (
            <span className="flex gap-1">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:0ms]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:150ms]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:300ms]" />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
