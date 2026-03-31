"use client";

import { useEffect } from "react";

interface StartupSplashProps {
  open: boolean;
  onDone: () => void;
  durationMs?: number;
}

export default function StartupSplash({
  open,
  onDone,
  durationMs = 1400,
}: StartupSplashProps) {
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(onDone, durationMs);
    return () => clearTimeout(id);
  }, [open, onDone, durationMs]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black">
      <div className="flex flex-col items-center gap-5 text-center">
        <div className="relative h-24 w-24">
          <div className="absolute inset-0 rounded-full border border-white/10 animate-spin" />
          <div className="absolute inset-2 rounded-full border border-white/20 animate-pulse" />
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="h-14 w-14 rounded-2xl border border-white/10 bg-white/5 flex items-center justify-center text-xl font-semibold text-white">
              K
            </div>
          </div>
        </div>

        <div className="text-xs uppercase tracking-[0.35em] text-gray-400">
          Kavin
        </div>

        <div className="flex items-center gap-1 text-xs text-gray-500">
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-500 [animation-delay:0ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-500 [animation-delay:150ms]" />
          <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-500 [animation-delay:300ms]" />
        </div>
      </div>
    </div>
  );
}
