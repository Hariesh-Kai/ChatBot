"use client";

/* =========================================================
   TYPES
========================================================= */

export type UploadPhase =
  | "idle"
  | "uploading"
  | "metadata"
  | "waiting_user"
  | "committing"
  | "done"
  | "error";

/* ================= PROPS ================= */

interface UploadProgressProps {
  /** Upload phase */
  phase: UploadPhase;

  /** Progress value (0–100) */
  progress?: number;

  /** Optional error message */
  errorMessage?: string;

  /** Compact mode (sidebar / icon-only) */
  compact?: boolean;
}

/* =========================================================
   HELPERS
========================================================= */

function getPhaseLabel(phase: UploadPhase): string {
  switch (phase) {
    case "uploading":
      return "Uploading PDF";
    case "metadata":
      return "Extracting metadata";
    case "waiting_user":
      return "Waiting for metadata";
    case "committing":
      return "Chunking & indexing";
    case "done":
      return "Completed";
    case "error":
      return "Failed";
    default:
      return "Idle";
  }
}

function getPhaseColor(phase: UploadPhase): string {
  if (phase === "error") return "#ef4444"; // red
  if (phase === "done") return "#22c55e";  // green
  return "#3b82f6";                        // blue
}

/* =========================================================
   COMPONENT
========================================================= */

export default function UploadProgress({
  phase,
  progress = 0,
  errorMessage,
  compact = false,
}: UploadProgressProps) {
  if (phase === "idle") return null;

  const safeProgress = Math.min(100, Math.max(0, progress));

  const radius = 10;
  const circumference = 2 * Math.PI * radius;
  const offset =
    circumference * (1 - safeProgress / 100);

  const isError = phase === "error";
  const color = getPhaseColor(phase);
  const label = getPhaseLabel(phase);

  return (
    <div className="flex items-center gap-2">
      {/* ================= CIRCULAR PROGRESS ================= */}
      <svg
        className="-rotate-90"
        width="24"
        height="24"
        viewBox="0 0 24 24"
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
          stroke={color}
          strokeWidth="3"
          fill="none"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className="transition-all duration-300 ease-out"
        />
      </svg>

      {/* ================= LABEL ================= */}
      {!compact && (
        <div className="text-xs leading-tight min-w-[120px]">
          <div
            className={
              isError ? "text-red-400" : "text-gray-300"
            }
          >
            {label}
          </div>

          {/* Subtext */}
          {isError ? (
            <div className="text-[10px] text-red-400">
              {errorMessage || "Something went wrong"}
            </div>
          ) : phase === "done" ? (
            <div className="text-[10px] text-green-400">
              100%
            </div>
          ) : (
            <div className="text-[10px] text-gray-400">
              {safeProgress}%
            </div>
          )}
        </div>
      )}
    </div>
  );
}
