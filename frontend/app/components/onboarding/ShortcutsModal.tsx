"use client";

import { X, Keyboard } from "lucide-react";

type ShortcutItem = {
  keys: string;
  label: string;
};

interface ShortcutsModalProps {
  open: boolean;
  onClose: () => void;
  shortcuts: ShortcutItem[];
}

export default function ShortcutsModal({
  open,
  onClose,
  shortcuts,
}: ShortcutsModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="w-full max-w-lg rounded-lg border border-white/10 bg-black shadow-xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <div className="flex items-center gap-2 text-sm font-medium text-white">
            <Keyboard size={16} />
            Keyboard Shortcuts
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-gray-400 hover:text-white hover:bg-white/10"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="px-4 py-4 space-y-3">
          {shortcuts.map((s) => (
            <div
              key={`${s.keys}-${s.label}`}
              className="flex items-center justify-between rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-300"
            >
              <span>{s.label}</span>
              <span className="font-mono text-gray-200">{s.keys}</span>
            </div>
          ))}
        </div>

        <div className="flex items-center justify-end px-4 py-3 border-t border-white/10">
          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-xs font-medium text-black bg-white hover:bg-gray-200"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
