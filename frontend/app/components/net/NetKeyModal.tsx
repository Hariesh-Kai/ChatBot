"use client";

import { useEffect, useState } from "react";
import {
  X,
  AlertTriangle,
  Key,
  Trash2,
  Loader2,
  CheckCircle,
} from "lucide-react";

import {
  setNetApiKey,
  clearNetApiKey,
  hasNetApiKey,
} from "@/app/lib/net-key-store";

import { API_BASE } from "@/app/lib/config";

interface NetKeyModalProps {
  open: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

export default function NetKeyModal({
  open,
  onClose,
  onSaved,
}: NetKeyModalProps) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verified, setVerified] = useState(false);

  /* 🔥 CRITICAL FIX: reset state every time modal opens */
  useEffect(() => {
    if (open) {
      setValue("");
      setError(null);
      setVerified(false);
      setVerifying(false);
    }
  }, [open]);

  if (!open) return null;

  async function handleVerifyAndSave() {
    if (!value.trim()) {
      setError("API key is required");
      return;
    }

    setVerifying(true);
    setError(null);

    try {
      const res = await fetch(`${API_BASE}/net-key/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: value.trim() }),
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Verification failed");
      }

      //  VERIFIED
      setNetApiKey(value.trim());
      setVerified(true);
      onSaved?.();

      setTimeout(() => {
        onClose();
      }, 600);
    } catch (err: any) {
      setError(err.message || "Failed to verify API key");
      setVerified(false);
    } finally {
      setVerifying(false);
    }
  }

  function handleClear() {
    clearNetApiKey();
    setValue("");
    setVerified(false);
    setError(null);
    onSaved?.();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="w-full max-w-md rounded-lg border border-white/10 bg-black shadow-xl">

        {/* HEADER */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <div className="flex items-center gap-2 text-sm font-medium text-white">
            <Key size={16} />
            KavinBase Net — API Key
          </div>

          <button
            onClick={onClose}
            disabled={verifying}
            className="rounded-md p-1 text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"
          >
            <X size={16} />
          </button>
        </div>

        {/* BODY */}
        <div className="px-4 py-4 space-y-4">

          <div className="flex gap-2 rounded-md border border-yellow-500/20 bg-yellow-500/10 p-3 text-xs text-yellow-300">
            <AlertTriangle size={14} className="mt-[1px]" />
            <p>
              KavinBase Net uses <b>external LLM APIs</b>.<br />
              Billing, rate limits, and usage are controlled by your provider.<br />
              <b>No automatic safeguards are applied.</b>
            </p>
          </div>

          <div className="space-y-1">
            <label className="block text-xs text-gray-400">
              API Key
            </label>

            <input
              type="password"
              placeholder="Paste your API key here"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              disabled={verifying}
              autoFocus
              className="
                w-full rounded-md bg-black px-3 py-2
                text-sm text-white
                border border-white/20
                outline-none
                focus:border-white/40
                disabled:opacity-60
              "
            />
          </div>

          {verifying && (
            <div className="flex items-center gap-2 text-xs text-sky-400">
              <Loader2 size={14} className="animate-spin" />
              Verifying API key…
            </div>
          )}

          {verified && (
            <div className="flex items-center gap-2 text-xs text-green-400">
              <CheckCircle size={14} />
              API key verified successfully
            </div>
          )}

          {error && (
            <div className="text-xs text-red-400">
              {error}
            </div>
          )}
        </div>

        {/* FOOTER */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-white/10">

          <button
            onClick={handleClear}
            disabled={!hasNetApiKey() || verifying}
            className="
              flex items-center gap-1 text-xs text-gray-400
              hover:text-red-400
              disabled:opacity-40
            "
          >
            <Trash2 size={14} />
            Clear key
          </button>

          <div className="flex gap-2">
            <button
              onClick={onClose}
              disabled={verifying}
              className="
                rounded-md px-3 py-1.5
                text-xs text-gray-400
                hover:text-white hover:bg-white/10
                disabled:opacity-50
              "
            >
              Cancel
            </button>

            <button
              onClick={handleVerifyAndSave}
              disabled={verifying}
              className="
                flex items-center gap-1
                rounded-md px-3 py-1.5
                text-xs font-medium text-black
                bg-white hover:bg-gray-200
                disabled:opacity-60
              "
            >
              Verify & Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
