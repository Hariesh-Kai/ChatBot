"use client";

import { useRef, useState } from "react";

/**
 * PdfUpload
 *
 * Responsibilities:
 * - Select a PDF file
 * - Validate type (PDF only)
 * - Hand off file to upload flow
 *
 * ❌ Does NOT:
 * - Call APIs directly
 * - Handle metadata popup
 * - Handle chunking / commit
 *
 * Those are handled by:
 * - page.tsx
 * - UploadProgress
 * - Metadata modal
 */

type PdfUploadProps = {
  onSelect: (file: File) => void;
  disabled?: boolean;
};

export default function PdfUpload({
  onSelect,
  disabled = false,
}: PdfUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function handleClick() {
    if (disabled) return;
    setError(null);
    inputRef.current?.click();
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    // Reset input so same file can be reselected
    e.target.value = "";

    // -----------------------------
    // VALIDATION
    // -----------------------------
    if (file.type !== "application/pdf") {
      setError("Only PDF files are supported");
      setFileName(null);
      return;
    }

    // Optional size guard (enterprise-safe)
    const MAX_SIZE_MB = 50;
    if (file.size > MAX_SIZE_MB * 1024 * 1024) {
      setError(`PDF must be smaller than ${MAX_SIZE_MB}MB`);
      setFileName(null);
      return;
    }

    setError(null);
    setFileName(file.name);

    // 🔥 Hand off to upload pipeline (page.tsx / hook)
    onSelect(file);
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled}
        className={`
          w-full rounded-lg border px-4 py-2 text-sm transition
          ${
            disabled
              ? "cursor-not-allowed border-white/5 text-gray-500"
              : "border-white/10 bg-transparent text-gray-200 hover:bg-[#1f1f1f]"
          }
        `}
      >
        Upload PDF
      </button>

      {/* Selected file */}
      {fileName && !error && (
        <div className="text-xs text-gray-400 truncate">
          📄 {fileName}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="text-xs text-red-400">
          ⚠️ {error}
        </div>
      )}

      {/* Hidden input */}
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        hidden
        onChange={handleChange}
      />
    </div>
  );
}
