// frontend/app/hooks/useSmartUpload.ts

import { useRef, useCallback } from "react";
import { uploadPdfWithProgress } from "@/app/lib/upload-client";

// Estimate: 1MB takes ~2 seconds to process
const PROCESSING_SPEED_SEC_PER_MB = 2.0; 
const MIN_PROCESSING_TIME_MS = 2000;

// 🔥 New: Labels to cycle through so it feels alive
const PROCESSING_LABELS = [
    "Extracting text...",
    "Analyzing structure...",
    "Chunking content...",
    "Generating embeddings...",
    "Indexing vectors...",
    "Finalizing..."
];

export type UploadStatus = "idle" | "uploading" | "processing" | "done" | "error";

export function useSmartUpload() {
  const processingInterval = useRef<NodeJS.Timeout | null>(null);

  const startUpload = useCallback(async (
    file: File, 
    sessionId: string,
    // Callback to push updates to the UI (ChatWindow)
    onProgress: (status: UploadStatus, percent: number, label: string) => void,
    onSuccess: (data: any) => void,
    onError: (msg: string) => void
  ) => {
    
    // 1. Upload Phase
    onProgress("uploading", 0, "Uploading PDF...");

    try {
      const result = await uploadPdfWithProgress({
        file,
        sessionId,
        onProgress: (pct) => {
          // Map 0-100 network bytes -> 0-40% visual
          const visualPercent = Math.min(pct * 0.4, 40); 
          
          if (pct >= 100) {
             // 2. Start Processing Simulation
             onProgress("processing", 40, "Processing PDF...");
             startProcessingSimulation(file.size, (simPct, label) => {
                onProgress("processing", simPct, label);
             });
          } else {
             onProgress("uploading", visualPercent, `Uploading ${Math.round(pct)}%...`);
          }
        }
      });

      stopSimulation();
      // Snap to 100
      onProgress("done", 100, "Complete");
      onSuccess(result);

    } catch (err: any) {
      stopSimulation();
      onProgress("error", 0, "Failed");
      onError(err.message || "Upload failed");
    }
  }, []);

  // --- ZENO'S PARADOX SIMULATION ---
  const startProcessingSimulation = (fileSizeBytes: number, updateCb: (p: number, l: string) => void) => {
    const sizeMB = fileSizeBytes / (1024 * 1024);
    const estimatedDurationMs = Math.max(
      sizeMB * PROCESSING_SPEED_SEC_PER_MB * 1000, 
      MIN_PROCESSING_TIME_MS
    );
    
    const step = 200; // Update every 200ms
    let elapsed = 0;

    if (processingInterval.current) clearInterval(processingInterval.current);

    processingInterval.current = setInterval(() => {
      elapsed += step;
      
      // Curve: 40% -> 95%
      const timePercent = Math.min(elapsed / estimatedDurationMs, 1);
      const ease = 1 - Math.pow(1 - timePercent, 2);
      const nextProgress = 40 + (ease * 55); 
      
      // Cycle labels based on progress
      const labelIndex = Math.floor((nextProgress - 40) / (55 / PROCESSING_LABELS.length));
      const currentLabel = PROCESSING_LABELS[Math.min(labelIndex, PROCESSING_LABELS.length - 1)];

      updateCb(nextProgress, currentLabel);
    }, step);
  };

  const stopSimulation = () => {
    if (processingInterval.current) {
      clearInterval(processingInterval.current);
      processingInterval.current = null;
    }
  };

  return { startUpload };
}