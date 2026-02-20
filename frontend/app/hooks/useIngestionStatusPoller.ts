import { useEffect, useRef } from "react";

import {
  fetchUploadIngestionStatus,
  UploadIngestionStatusResponse,
} from "@/app/lib/api";

export interface PendingIngestionPollItem {
  id: string;
  jobId?: string | null;
  sessionId?: string | null;
  chatId: string;
  messageId: string;
}

interface UseIngestionStatusPollerOptions {
  pending: PendingIngestionPollItem[];
  onReady: (
    item: PendingIngestionPollItem,
    status: UploadIngestionStatusResponse
  ) => void;
  onProgress?: (
    item: PendingIngestionPollItem,
    status: UploadIngestionStatusResponse
  ) => void;
  onError?: (
    item: PendingIngestionPollItem,
    status: UploadIngestionStatusResponse
  ) => void;
  intervalMs?: number;
  enabled?: boolean;
}

export function useIngestionStatusPoller({
  pending,
  onReady,
  onProgress,
  onError,
  intervalMs = 2500,
  enabled = true,
}: UseIngestionStatusPollerOptions) {
  const inFlightRef = useRef(false);
  const onReadyRef = useRef(onReady);
  const onProgressRef = useRef(onProgress);
  const onErrorRef = useRef(onError);

  useEffect(() => {
    onReadyRef.current = onReady;
  }, [onReady]);

  useEffect(() => {
    onProgressRef.current = onProgress;
  }, [onProgress]);

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    if (!enabled || pending.length === 0) return;

    let cancelled = false;

    const tick = async () => {
      if (cancelled || inFlightRef.current) return;
      inFlightRef.current = true;

      try {
        const snapshot = [...pending];
        const results = await Promise.allSettled(
          snapshot.map(async (item) => {
            const status = await fetchUploadIngestionStatus({
              jobId: item.jobId,
              sessionId: item.sessionId,
            });
            return { item, status };
          })
        );

        if (cancelled) return;

        for (const result of results) {
          if (result.status !== "fulfilled") continue;
          const { item, status } = result.value;

          if (status.ready || status.status === "READY") {
            onReadyRef.current(item, status);
            continue;
          }

          if (status.status === "ERROR" || status.status === "NOT_FOUND") {
            onErrorRef.current?.(item, status);
            continue;
          }

          onProgressRef.current?.(item, status);
        }
      } finally {
        inFlightRef.current = false;
      }
    };

    void tick();
    const intervalId = window.setInterval(() => {
      void tick();
    }, Math.max(800, intervalMs));

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [pending, intervalMs, enabled]);
}
