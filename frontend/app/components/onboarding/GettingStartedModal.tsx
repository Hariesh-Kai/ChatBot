"use client";

import { X, Sparkles, Upload, MessageSquare, Layers, Keyboard } from "lucide-react";

interface GettingStartedModalProps {
  open: boolean;
  onClose: () => void;
  onOpenShortcuts: () => void;
}

export default function GettingStartedModal({
  open,
  onClose,
  onOpenShortcuts,
}: GettingStartedModalProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70">
      <div className="w-full max-w-lg rounded-lg border border-white/10 bg-black shadow-xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
          <div className="flex items-center gap-2 text-sm font-medium text-white">
            <Sparkles size={16} />
            Welcome to KavinBase
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-gray-400 hover:text-white hover:bg-white/10"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="px-4 py-4 space-y-4 text-sm text-gray-300">
          <div className="text-xs uppercase tracking-wider text-gray-500">
            Quick Start
          </div>

          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <Upload size={16} className="mt-0.5 text-blue-400" />
              <div>
                <div className="text-gray-200 font-medium">Upload a PDF</div>
                <div className="text-gray-500 text-xs">
                  Add your document so KavinBase can answer with sources.
                </div>
              </div>
            </div>

            <div className="flex items-start gap-3">
              <MessageSquare size={16} className="mt-0.5 text-emerald-400" />
              <div>
                <div className="text-gray-200 font-medium">Ask questions</div>
                <div className="text-gray-500 text-xs">
                  Type your question and get a grounded answer.
                </div>
              </div>
            </div>

            <div className="flex items-start gap-3">
              <Layers size={16} className="mt-0.5 text-purple-400" />
              <div>
                <div className="text-gray-200 font-medium">Switch models</div>
                <div className="text-gray-500 text-xs">
                  Use Lite for speed, Base for quality, Net for cloud.
                </div>
              </div>
            </div>
          </div>

          <div className="rounded-md border border-white/10 bg-white/5 px-3 py-2 text-xs text-gray-400">
            Tip: Use keyboard shortcuts to move faster.
          </div>
        </div>

        <div className="flex items-center justify-between px-4 py-3 border-t border-white/10">
          <button
            onClick={onOpenShortcuts}
            className="flex items-center gap-2 text-xs text-gray-400 hover:text-white"
          >
            <Keyboard size={14} />
            View shortcuts
          </button>

          <button
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-xs font-medium text-black bg-white hover:bg-gray-200"
          >
            Start
          </button>
        </div>
      </div>
    </div>
  );
}
