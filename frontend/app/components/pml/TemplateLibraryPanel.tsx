"use client";

import { RefreshCcw, Trash2, X } from "lucide-react";
import type { PmlTemplate } from "@/app/lib/pml-api";

interface TemplateLibraryPanelProps {
  templates: PmlTemplate[];
  loading: boolean;
  error: string | null;
  deletingId?: string | null;
  onRefresh: () => void;
  onDelete: (templateId: string) => Promise<void> | void;
  mode?: "desktop" | "mobile";
  onClose?: () => void;
}

function formatTimestamp(value?: string): string {
  const raw = (value || "").trim();
  if (!raw) return "Unknown time";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString();
}

function firstCodeLine(code: string): string {
  const line = (code || "")
    .split("\n")
    .map((item) => item.trim())
    .find((item) => item.length > 0);
  return line || "(empty)";
}

export default function TemplateLibraryPanel({
  templates,
  loading,
  error,
  deletingId,
  onRefresh,
  onDelete,
  mode = "desktop",
  onClose,
}: TemplateLibraryPanelProps) {
  const isMobileMode = mode === "mobile";

  return (
    <aside
      className={
        isMobileMode
          ? "flex h-full w-full flex-col bg-black"
          : "hidden h-full w-[320px] shrink-0 border-l border-white/10 bg-black/30 lg:flex lg:flex-col"
      }
    >
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-white">Template Library</div>
          <div className="text-[11px] text-gray-500">{templates.length} saved</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/5 px-2 py-1 text-[11px] text-gray-200 transition hover:bg-white/10 disabled:opacity-50"
          >
            <RefreshCcw size={12} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
          {isMobileMode && (
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-white/20 bg-white/5 text-gray-200 transition hover:bg-white/10"
              aria-label="Close template library"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && (
          <div className="mb-2 rounded-md border border-red-500/30 bg-red-500/10 px-2.5 py-2 text-[11px] text-red-200">
            {error}
          </div>
        )}

        {loading && templates.length === 0 && (
          <div className="rounded-md border border-white/10 bg-black/20 px-3 py-3 text-xs text-gray-500">
            Loading templates...
          </div>
        )}

        {!loading && templates.length === 0 && (
          <div className="rounded-md border border-white/10 bg-black/20 px-3 py-3 text-xs text-gray-500">
            No templates yet. Save a generated PML answer to create one.
          </div>
        )}

        <div className="space-y-2">
          {templates.map((template) => {
            const templateId = template.id;
            const busy = deletingId === templateId;
            return (
              <div
                key={templateId}
                className="rounded-lg border border-white/10 bg-black/30 px-3 py-2.5"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate text-xs font-semibold text-white">
                      {template.note?.trim() || `Template ${templateId.slice(0, 8)}`}
                    </div>
                    <div className="mt-1 truncate text-[11px] text-gray-400">
                      {firstCodeLine(template.code || "")}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      if (!templateId) return;
                      if (!window.confirm("Delete this template?")) return;
                      void onDelete(templateId);
                    }}
                    disabled={busy}
                    className="rounded-md border border-white/15 bg-white/5 p-1.5 text-gray-300 transition hover:bg-red-500/15 hover:text-red-200 disabled:opacity-50"
                    title="Delete template"
                    aria-label="Delete template"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                <div className="mt-1 text-[10px] text-gray-500">
                  Updated: {formatTimestamp(template.updated_at || template.created_at)}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </aside>
  );
}
