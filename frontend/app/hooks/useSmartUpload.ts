// frontend/app/hooks/useSmartUpload.ts

import { useRef, useCallback } from "react";
import { uploadPdfWithProgress } from "@/app/lib/upload-client";

// Estimate: 1MB takes ~2 seconds to process
const PROCESSING_SPEED_SEC_PER_MB = 2.0;
const MIN_PROCESSING_TIME_MS = 2000;

// Status labels
const PROCESSING_LABELS = [
  "Extracting text...",
  "Analyzing structure...",
  "Chunking content...",
  "Generating embeddings...",
  "Indexing vectors...",
  "Finalizing...",
];

export type UploadStatus =
  | "idle"
  | "uploading"
  | "processing"
  | "done"
  | "error";

export function useSmartUpload() {
  const processingInterval = useRef<NodeJS.Timeout | null>(null);

  // 🔥 Upload generation token (prevents ghost updates)
  const activeUploadId = useRef(0);

  const stopSimulation = () => {
    if (processingInterval.current) {
      clearInterval(processingInterval.current);
      processingInterval.current = null;
    }
  };

 

  const startUpload = useCallback(
    async (
      file: File,
      sessionId: string,
      onProgress: (
        status: UploadStatus,
        percent: number,
        label: string
      ) => void,
      onSuccess: (data: any) => void,
      onError: (msg: string) => void
    ) => {
      // 🔥 New upload generation
      const uploadId = ++activeUploadId.current;
      

      stopSimulation();
      onProgress("uploading", 0, "Uploading PDF...");

      try {
        const result = await uploadPdfWithProgress({
          file,
          sessionId,
          onProgress: (pct) => {
            // Ignore stale uploads
            if (uploadId !== activeUploadId.current) return;

            const visualPercent = Math.min(pct * 0.4, 40);

            if (pct >= 100) {
              onProgress("processing", 40, "Processing PDF...");
            } 
            
            else {
              onProgress(
                "uploading",
                visualPercent,
                `Uploading ${Math.round(pct)}%...`
              );
            }
          },
        });

        // 🔥 Success path
        if (uploadId !== activeUploadId.current) return;
        stopSimulation();
      

        // Backend-driven flow control
        if (result?.next_action === "WAIT_FOR_METADATA") {
          // Do NOT mark done — frontend must wait for metadata form
          onProgress("processing", 40, "Waiting for metadata...");
        } else if (result?.next_action === "READY_FOR_PROCESSING") {
          // Backend is ready for commit → allow processing simulation
          onProgress("processing", 40, "Processing PDF...");
        } else {
          // Fallback (safe)
          onProgress("done", 100, "Complete");
        }

        onSuccess(result);
      } catch (err: any) {
        if (uploadId !== activeUploadId.current) return;

        stopSimulation();
        (window as any).__KAVIN_UPLOAD_ACTIVE__ = false;
        onProgress("error", 0, "Failed");
        onError(err?.message || "Upload failed");
      }
    },
    []
  );

  return { startUpload };
}
