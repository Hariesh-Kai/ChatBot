"use client";

import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { API_BASE } from "@/app/lib/config";

/* ================= PROPS ================= */

type Props = {
  /** Active chat session id (REQUIRED for RAG uploads) */
  sessionId: string | null;

  onUploadStart?: () => void;
  onUploadSuccess?: (result: any) => void;
  onUploadError?: (error: string) => void;
  iconOnly?: boolean;
};

/* ================= COMPONENT ================= */

export default function PdfUploadButton({
  sessionId,
  onUploadStart,
  onUploadSuccess,
  onUploadError,
  iconOnly = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  const [isUploading, setIsUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  /* ================= HANDLER ================= */

  async function handleFileChange(
    e: React.ChangeEvent<HTMLInputElement>
  ) {
    const file = e.target.files?.[0];
    if (!file) return;

    // allow re-selecting same file later
    e.target.value = "";

    // 🔥 FIX: Guard against null session
    if (!sessionId) {
      onUploadError?.("Initializing chat... please try again in a second.");
      return;
    }

    if (file.type !== "application/pdf") {
      onUploadError?.("Only PDF files are supported");
      return;
    }

    let fakeTimer: NodeJS.Timeout | null = null;

    try {
      setIsUploading(true);
      setProgress(0);
      onUploadStart?.();

      const formData = new FormData();
      formData.append("file", file);
      formData.append("session_id", sessionId); // ✅ FIX

      /* ================= FAKE PROGRESS =================
         fetch() has no upload progress, so we simulate
       ================================================== */
      fakeTimer = setInterval(() => {
        setProgress((p) => (p < 90 ? p + 1 : p));
      }, 120);

      const res = await fetch(`${API_BASE}/upload/`, {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "Upload failed");
      }

      const result = await res.json();

      // finish progress
      setProgress(100);
      onUploadSuccess?.(result);
    } catch (err: any) {
      console.error("PDF upload failed:", err);
      onUploadError?.(
        err?.message || "Failed to upload document"
      );
    } finally {
      if (fakeTimer) clearInterval(fakeTimer);

      // small delay so user sees 100%
      setTimeout(() => {
        setIsUploading(false);
        setProgress(0);
      }, 600);
    }
  }

  /* ================= UI ================= */

  const radius = 10;
  const circumference = 2 * Math.PI * radius;
  const dashOffset =
    circumference * (1 - progress / 100);

  return (
    <>
      <button
        type="button"
        disabled={isUploading}
        onClick={() => inputRef.current?.click()}
        title={
          sessionId
            ? "Upload PDF"
            : "Start a chat before uploading a PDF"
        }
        className={`
          relative flex items-center justify-center gap-2
          rounded-md border border-white/10
          ${iconOnly ? "p-2" : "w-full px-3 py-2 text-sm"}
          ${
            isUploading
              ? "cursor-not-allowed text-gray-400"
              : sessionId
              ? "text-gray-400 hover:text-white hover:bg-white/10"
              : "cursor-not-allowed text-gray-600"
          }
        `}
      >
        {/* ========== NORMAL STATE ========== */}
        {!isUploading && (
          <>
            {iconOnly ? <Upload size={18} /> : "Upload PDF"}
          </>
        )}

        {/* ========== UPLOADING STATE ========== */}
        {isUploading && (
          <>
            <svg
              className="-rotate-90"
              width="24"
              height="24"
              viewBox="0 0 24 24"
            >
              <circle
                cx="12"
                cy="12"
                r={radius}
                stroke="rgba(255,255,255,0.15)"
                strokeWidth="3"
                fill="none"
              />
              <circle
                cx="12"
                cy="12"
                r={radius}
                stroke="#3b82f6"
                strokeWidth="3"
                fill="none"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                className="transition-all duration-300 ease-out"
              />
            </svg>

            {!iconOnly && (
              <span className="text-xs text-gray-300">
                Uploading PDF… {progress}%
              </span>
            )}
          </>
        )}
      </button>

      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        hidden
        onChange={handleFileChange}
      />
    </>
  );
}