"use client";

import { useEffect, useState } from "react";
import {
  KAVIN_MODELS,
  KavinModelId,
  getVisibleModels,
  isNetEnabled,
} from "@/app/lib/kavin-models";
import { getNetStatus } from "@/app/lib/netStatus";

interface Props {
  value: KavinModelId;
  onChange: (model: KavinModelId) => void;
}

/**
 * ModelSelector
 *
 * Responsibilities:
 * - Show available models (Lite / Base / Net)
 * - Reflect Net availability & rate-limit
 * - Disable Net when unavailable or rate-limited
 * - Auto-fallback Net → Base when blocked
 *
 * RULES:
 * - Stateless beyond UI hints
 * - Parent owns selected model
 */
export default function ModelSelector({ value, onChange }: Props) {
  const [netRateLimited, setNetRateLimited] = useState(false);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);

  /* ================= NET STATUS ================= */

  useEffect(() => {
    let mounted = true;

    async function poll() {
      try {
        const status = await getNetStatus();

        if (!mounted) return;

        setNetRateLimited(Boolean(status.rateLimited));
        setRetryAfter(status.retryAfterSec ?? null);

        // 🔁 Auto-fallback: Net → Base
        if (
          value === "net" &&
          (!status.available || status.rateLimited)
        ) {
          onChange("base");
        }
      } catch {
        if (!mounted) return;
        setNetRateLimited(false);
        setRetryAfter(null);
      }
    }

    poll();
    const id = setInterval(poll, 30_000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, [value, onChange]);

  /* ================= MODELS ================= */

  const models = getVisibleModels();

  /* ================= RENDER ================= */

  return (
    <div className="flex items-center gap-2">
      {models.map((m) => {
        const isNet = m.id === "net";
        const disabled =
          isNet && (!isNetEnabled() || netRateLimited);

        const tooltip =
          isNet && netRateLimited && retryAfter
            ? `Retry in ${retryAfter}s`
            : isNet && !isNetEnabled()
            ? "Net unavailable"
            : m.description;

        return (
          <button
            key={m.id}
            disabled={disabled}
            title={tooltip}
            onClick={() => onChange(m.id)}
            className={`
              px-3 py-1.5 rounded-full text-xs font-medium transition
              ${
                value === m.id
                  ? "bg-white text-black"
                  : "bg-white/10 text-gray-300 hover:bg-white/20"
              }
              ${disabled ? "opacity-40 cursor-not-allowed" : ""}
            `}
          >
            {m.label}
            {isNet && netRateLimited && " ⏳"}
          </button>
        );
      })}
    </div>
  );
}
