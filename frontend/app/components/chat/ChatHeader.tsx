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
  LoaderCircle,
} from "lucide-react";

import { CHAT_UI_MODELS, ChatUIModelId } from "@/app/lib/chat-ui-models";
import NetKeyModal from "@/app/components/net/NetKeyModal";
import { hasNetApiKey } from "@/app/lib/net-key-store";
import Avatar from "../ui/Avatar";
import { getModelAvatar } from "@/app/lib/model-avatars";

interface Props {
  title: string;
  isTyping: boolean;
  ingestionPollingActive?: boolean;
  ingestionPollingCount?: number;

  activeModel: ChatUIModelId;
  onModelChange: (model: ChatUIModelId) => void;
  lockModelSelector?: boolean;
  lockedModelLabel?: string;

  onRename: (title: string) => void;
  onClear: () => void;
}

/* =========================================================
   SAFE MODEL LIST (NO MUTATION, NO SIDE EFFECTS)
========================================================= */

type ModelItem = {
  id: ChatUIModelId;
  label: string;
};

// 🔥 FIX 1: Show ALL models. Do not filter out Net.
const ALL_MODELS: ModelItem[] = Object.values(CHAT_UI_MODELS)
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
  ingestionPollingActive = false,
  ingestionPollingCount = 0,
  activeModel,
  onModelChange,
  lockModelSelector = false,
  lockedModelLabel,
  onRename,
  onClear,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const [modelOpen, setModelOpen] = useState(false);
  const [netModalOpen, setNetModalOpen] = useState(false);

  const modelRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

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
    if (!isTyping) {
      setValue(title);
      setEditing(true);
    }
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
    if (lockModelSelector && lockedModelLabel) return lockedModelLabel;
    return (
      ALL_MODELS.find((m) => m.id === activeModel)?.label ??
      "Model"
    );
  }, [activeModel, lockModelSelector, lockedModelLabel]);

  const avatar = useMemo(() => getModelAvatar(activeModel), [activeModel]);

  return (
    <>
      <header
        className={`
          chat-top-header sticky top-0 z-40 h-14
          border-b border-white/10 bg-black
          transition-opacity
          ${isTyping ? "opacity-70" : ""}
        `}
      >
        <div className="flex h-full items-center justify-between px-2.5 sm:px-4">

          {/* ================= LEFT — MODEL DROPDOWN ================= */}
          <div className="flex min-w-0 items-center gap-1.5 sm:gap-2">
            <Avatar
              role="assistant"
              assistantLabel={avatar.label}
              assistantClassName={avatar.className}
              size="sm"
            />
            <div ref={modelRef} className="relative">
            {lockModelSelector ? (
              <div
                className="
                  flex max-w-[120px] items-center gap-1
                  text-[11px] font-medium text-gray-400 sm:text-xs
                "
              >
                <span className="truncate">{activeLabel}</span>
              </div>
            ) : (
              <button
                onClick={() => !isTyping && setModelOpen((v) => !v)}
                disabled={isTyping}
                className="
                  flex max-w-[120px] items-center gap-1
                  text-[11px] font-medium text-gray-400 sm:text-xs
                  hover:text-white
                  disabled:opacity-50
                "
              >
                <span className="truncate">{activeLabel}</span>
                {activeModel === "net" && (
                  <Cloud size={12} className="text-cyan-300" />
                )}
                <ChevronDown size={14} />
              </button>
            )}

            {!lockModelSelector && modelOpen && (
              <div
                className="
                  absolute left-0 mt-2 w-44 sm:w-52
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
                      <span className="flex items-center gap-1 text-xs text-cyan-300">
                        <Cloud size={12} />
                        Cloud
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
            </div>
          </div>

          {/* ================= CENTER — CHAT TITLE ================= */}
          <div className="max-[360px]:hidden flex-1 px-2 text-center sm:px-4">
            {!editing ? (
              <span className="block truncate text-[13px] font-medium text-white sm:text-sm">
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
            <div className="flex items-center gap-0.5 sm:gap-1">
            {ingestionPollingActive && (
              <div
                title="Document processing is running in background"
                className="
                  mr-1 inline-flex items-center gap-1
                  rounded-full border border-cyan-500/30
                  bg-cyan-500/10 px-2 py-1
                "
              >
                <LoaderCircle size={12} className="animate-spin text-cyan-300" />
                <span className="hidden sm:inline text-[11px] font-medium text-cyan-200">
                  Processing{ingestionPollingCount > 1 ? ` (${ingestionPollingCount})` : ""}
                </span>
              </div>
            )}

            {/* Always show key button if Net is active OR if we want to config it */}
            {activeModel === "net" && (
              <button
                onClick={() => setNetModalOpen(true)}
                disabled={isTyping}
                title="Configure Chat UI Net v1.0"
                className="
                  rounded-md p-1.5 text-cyan-300 sm:p-2
                  hover:bg-white/10 hover:text-cyan-200
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
                    rounded-md p-1.5 text-gray-400 sm:p-2
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
                    rounded-md p-1.5 text-gray-400 sm:p-2
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
                    rounded-md p-1.5 text-cyan-300 sm:p-2
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
                    rounded-md p-1.5 text-gray-400 sm:p-2
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
