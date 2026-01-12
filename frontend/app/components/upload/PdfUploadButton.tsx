// frontend/app/components/upload/PdfUploadButton.tsx

"use client";

import { useRef } from "react";
import { Upload } from "lucide-react";
import { useSmartUpload, UploadStatus } from "@/app/hooks/useSmartUpload";

type Props = {
  sessionId: string | null;
  onUploadStart?: () => void;
  // ✅ NEW: Receive progress callback from parent
  onUploadProgress?: (status: UploadStatus, percent: number, label: string) => void;
  onUploadSuccess?: (result: any) => void;
  onUploadError?: (error: string) => void;
  iconOnly?: boolean;
  disabled?: boolean;
};

export default function PdfUploadButton({
  sessionId,
  onUploadStart,
  onUploadProgress,
  onUploadSuccess,
  onUploadError,
  iconOnly = false,
  disabled = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  
  // We only pull the start function. State is now managed by the ChatWindow via callbacks.
  const { startUpload } = useSmartUpload();

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";

    if (!sessionId) {
      onUploadError?.("Initializing chat... please try again.");
      return;
    }

    if (file.type !== "application/pdf") {
      onUploadError?.("Only PDF files are supported");
      return;
    }

    // 1. Notify Parent (Creates the bubble)
    onUploadStart?.();

    // 2. Start Logic
    await startUpload(
      file,
      sessionId,
      // Pass the progress updates to parent to render in bubble
      (status, pct, label) => onUploadProgress?.(status, pct, label),
      (data) => onUploadSuccess?.(data),
      (err) => onUploadError?.(err)
    );
  }

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        title={sessionId ? "Upload PDF" : "Start chat to upload"}
        className={`
          relative flex items-center justify-center gap-2 rounded-md border border-white/10
          ${iconOnly ? "p-2" : "w-full px-3 py-2 text-sm"}
          ${disabled ? "cursor-not-allowed text-gray-500 bg-white/5" : "text-gray-400 hover:text-white hover:bg-white/10"}
        `}
      >
        {iconOnly ? <Upload size={18} /> : "Upload PDF"}
      </button>
      <input ref={inputRef} type="file" accept="application/pdf" hidden onChange={handleFileChange} />
    </>
  );
}