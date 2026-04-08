// frontend/app/components/upload/PdfUploadButton.tsx

"use client";

import { useRef, useState, type DragEvent } from "react";
import { Upload } from "lucide-react";
import { useSmartUpload, UploadStatus } from "@/app/hooks/useSmartUpload";
import { getFirstPdfFile, validatePdfFile } from "@/app/lib/pdf-upload";

type Props = {
  sessionId: string | null;
  onUploadStart?: (file: File) => void;
  //  NEW: Receive progress callback from parent
  onUploadProgress?: (status: UploadStatus, percent: number, label: string) => void;
  onUploadSuccess?: (result: any) => void;
  onUploadError?: (error: string) => void;
  iconOnly?: boolean;
  disabled?: boolean;
  dataId?: string;
};

export default function PdfUploadButton({
  sessionId,
  onUploadStart,
  onUploadProgress,
  onUploadSuccess,
  onUploadError,
  iconOnly = false,
  disabled = false,
  dataId,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  
  // We only pull the start function. State is now managed by the ChatWindow via callbacks.
  const { startUpload } = useSmartUpload();

  async function handleUploadFile(file: File) {
    if (!sessionId) {
      onUploadError?.("Initializing chat... please try again.");
      return;
    }

    const validationError = validatePdfFile(file);
    if (validationError) {
      onUploadError?.(validationError);
      return;
    }

    onUploadStart?.(file);
    await startUpload(
      file,
      sessionId,
      (status, pct, label) => onUploadProgress?.(status, pct, label),
      (data) => onUploadSuccess?.(data),
      (err) => onUploadError?.(err)
    );
  }

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = getFirstPdfFile(e.target.files);
    e.target.value = "";
    if (!file) return;
    await handleUploadFile(file);
  }

  function hasFiles(event: DragEvent<HTMLElement>) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
  }

  function handleDragEnter(e: DragEvent<HTMLButtonElement>) {
    if (disabled || !sessionId || !hasFiles(e)) return;
    e.preventDefault();
    setDragActive(true);
  }

  function handleDragOver(e: DragEvent<HTMLButtonElement>) {
    if (disabled || !sessionId || !hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    if (!dragActive) setDragActive(true);
  }

  function handleDragLeave(e: DragEvent<HTMLButtonElement>) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    setDragActive(false);
  }

  async function handleDrop(e: DragEvent<HTMLButtonElement>) {
    if (!hasFiles(e)) return;
    e.preventDefault();
    setDragActive(false);
    if (disabled || !sessionId) return;
    const file = getFirstPdfFile(e.dataTransfer.files);
    if (!file) return;
    await handleUploadFile(file);
  }

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        title={sessionId ? "Upload or drop PDF" : "Start chat to upload"}
        data-upload-id={dataId}
        className={`
          relative flex items-center justify-center gap-2 rounded-md border border-white/10
          ${iconOnly ? "p-2" : "w-full px-3 py-2 text-sm"}
          ${
            disabled
              ? "cursor-not-allowed text-gray-500 bg-white/5"
              : dragActive
                ? "border-cyan-400/60 bg-cyan-500/15 text-cyan-100"
                : "text-gray-400 hover:text-white hover:bg-white/10"
          }
        `}
      >
        {iconOnly ? <Upload size={18} /> : dragActive ? "Drop PDF to Upload" : "Upload or Drop PDF"}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf"
        hidden
        disabled={disabled || !sessionId}
        data-upload-id={dataId}
        onChange={handleFileChange}
      />
    </>
  );
}
