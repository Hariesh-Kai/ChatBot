"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import {
  ChevronDown,
  Pencil,
  Eraser,
  Check,
  X,
  Cloud,
  Key,
} from "lucide-react";

import { KAVIN_MODELS, KavinModelId } from "@/app/lib/kavin-models";
import NetKeyModal from "@/app/components/net/NetKeyModal";
import { hasNetApiKey } from "@/app/lib/net-key-store";

interface Props {
  title: string;
  isTyping: boolean;

  activeModel: KavinModelId;
  onModelChange: (model: KavinModelId) => void;

  onRename: (title: string) => void;
  onClear: () => void;
}

/* =========================================================
   SAFE MODEL LIST (NO MUTATION, NO SIDE EFFECTS)
========================================================= */

type ModelItem = {
  id: KavinModelId;
  label: string;
};

// 🔥 FIX 1: Show ALL models. Do not filter out Net.
const ALL_MODELS: ModelItem[] = Object.values(KAVIN_MODELS)
  .filter(
    (m): m is ModelItem =>
      !!m && typeof m.id === "string" && typeof m.label === "string"
  )
  .map((m) => ({
    id: m.id,
    label: m.label,
  }));

export default function ChatHeader({
  title,
  isTyping,
  activeModel,
  onModelChange,
  onRename,
  onClear,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const [modelOpen, setModelOpen] = useState(false);
  const [netModalOpen, setNetModalOpen] = useState(false);

  const modelRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  /* -------------------------------------------------
     Reset local UI state when chat changes
   -------------------------------------------------- */
  useEffect(() => {
    setEditing(false);
    setModelOpen(false);
    setValue(title);
  }, [title]);

  /* ---------------- Focus title input ---------------- */
  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  /* ---------------- Close dropdown on outside click ---------------- */
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (
        modelRef.current &&
        !modelRef.current.contains(e.target as Node)
      ) {
        setModelOpen(false);
      }
    }

    if (modelOpen) {
      document.addEventListener("mousedown", handleClick);
    }

    return () => {
      document.removeEventListener("mousedown", handleClick);
    };
  }, [modelOpen]);

  /* ---------------- Editing helpers ---------------- */
  function startEdit() {
    if (!isTyping) setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setValue(title);
  }

  function saveEdit() {
    if (isTyping) return;

    const trimmed = value.trim();
    if (!trimmed) {
      cancelEdit();
      return;
    }

    onRename(trimmed);
    setEditing(false);
  }

  /* ---------------- Active label ---------------- */
  const activeLabel = useMemo(() => {
    return (
      ALL_MODELS.find((m) => m.id === activeModel)?.label ??
      "Model"
    );
  }, [activeModel]);

  return (
    <>
      <header
        className={`
          sticky top-0 z-40 h-14
          border-b border-white/10 bg-black
          transition-opacity
          ${isTyping ? "opacity-70" : ""}
        `}
      >
        <div className="flex h-full items-center justify-between px-4">

          {/* ================= LEFT — MODEL DROPDOWN ================= */}
          <div ref={modelRef} className="relative">
            <button
              onClick={() => !isTyping && setModelOpen((v) => !v)}
              disabled={isTyping}
              className="
                flex items-center gap-1
                text-xs font-medium text-gray-400
                hover:text-white
                disabled:opacity-50
              "
            >
              {activeLabel}
              {activeModel === "net" && (
                <Cloud size={12} className="text-sky-400" />
              )}
              <ChevronDown size={14} />
            </button>

            {modelOpen && (
              <div
                className="
                  absolute left-0 mt-2 w-52
                  rounded-md border border-white/10
                  bg-black shadow-xl z-50
                "
              >
                {ALL_MODELS.map((m) => (
                  <button
                    key={m.id}
                    onClick={() => {
                      // 🔥 FIX 2: Intercept Net click if no key
                      if (m.id === "net" && !hasNetApiKey()) {
                        setNetModalOpen(true);
                        setModelOpen(false);
                        return;
                      }

                      // Normal switch
                      onModelChange(m.id);
                      setModelOpen(false);
                    }}
                    className={`
                      w-full px-3 py-2 text-left text-sm
                      hover:bg-white/5
                      flex items-center justify-between
                      ${
                        activeModel === m.id
                          ? "text-white bg-white/10"
                          : "text-gray-300"
                      }
                    `}
                  >
                    <span>{m.label}</span>

                    {m.id === "net" && (
                      <span className="flex items-center gap-1 text-xs text-sky-400">
                        <Cloud size={12} />
                        Cloud
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* ================= CENTER — CHAT TITLE ================= */}
          <div className="flex-1 text-center px-4">
            {!editing ? (
              <span className="block truncate text-sm font-medium text-white">
                {title || " "}
              </span>
            ) : (
              <input
                ref={inputRef}
                value={value}
                disabled={isTyping}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveEdit();
                  if (e.key === "Escape") cancelEdit();
                }}
                className="
                  mx-auto w-full max-w-[520px]
                  rounded-md bg-black/40 px-2 py-1
                  text-sm text-white outline-none
                  border border-white/20
                "
              />
            )}
          </div>

          {/* ================= RIGHT — ACTIONS ================= */}
          <div className="flex items-center gap-1">

            {/* Always show key button if Net is active OR if we want to config it */}
            {activeModel === "net" && (
              <button
                onClick={() => setNetModalOpen(true)}
                disabled={isTyping}
                title="Configure KavinBase Net"
                className="
                  rounded-md p-2 text-sky-400
                  hover:bg-white/10 hover:text-sky-300
                  disabled:opacity-50
                "
              >
                <Key size={16} />
              </button>
            )}

            {!editing ? (
              <>
                <button
                  onClick={startEdit}
                  disabled={isTyping}
                  title="Rename chat"
                  className="
                    rounded-md p-2 text-gray-400
                    hover:text-white hover:bg-white/10
                    disabled:opacity-50
                  "
                >
                  <Pencil size={16} />
                </button>

                <button
                  onClick={onClear}
                  disabled={isTyping}
                  title="Clear messages"
                  className="
                    rounded-md p-2 text-gray-400
                    hover:text-white hover:bg-white/10
                    disabled:opacity-50
                  "
                >
                  <Eraser size={16} />
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={saveEdit}
                  disabled={isTyping}
                  title="Save title"
                  className="
                    rounded-md p-2 text-green-400
                    hover:bg-white/10
                    disabled:opacity-50
                  "
                >
                  <Check size={16} />
                </button>

                <button
                  onClick={cancelEdit}
                  title="Cancel"
                  className="
                    rounded-md p-2 text-gray-400
                    hover:text-white hover:bg-white/10
                  "
                >
                  <X size={16} />
                </button>
              </>
            )}
          </div>
        </div>
      </header>

      <NetKeyModal
        open={netModalOpen}
        onClose={() => setNetModalOpen(false)}
        // 🔥 FIX 3: Auto-switch to Net when key is saved
        onSaved={() => onModelChange("net")}
      />
    </>
  );
}