"use client";

import type { HealthState } from "@/app/components/workspace/types";

export default function StatusDot({ state }: { state: HealthState }) {
  const cls =
    state === "online"
      ? "bg-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.55)]"
      : state === "degraded"
        ? "animate-pulse bg-amber-400 shadow-[0_0_10px_rgba(245,158,11,0.45)]"
        : state === "offline"
          ? "bg-rose-400 shadow-[0_0_10px_rgba(251,113,133,0.45)]"
          : "bg-gray-500";

  return <span className={`inline-block h-2 w-2 rounded-full ${cls}`} />;
}
