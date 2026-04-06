"use client";

import { useEffect, useMemo, useState } from "react";
import Image from "next/image";
import { X, ZoomIn, ZoomOut, Loader2, AlertCircle, FileText } from "lucide-react";
import { API_BASE } from "@/app/lib/config";

interface Props {
  open: boolean;
  jobId: string | null;
  page: number;
  bbox?: string;
  title?: string;
  subtitle?: string;
  excerpt?: string;
  onClose: () => void;
}

export default function PreprocessingSourceViewerModal({
  open,
  jobId,
  page,
  bbox,
  title,
  subtitle,
  excerpt,
  onClose,
}: Props) {
  const [zoom, setZoom] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const imageUrl = useMemo(() => {
    if (!jobId) return "";
    const query = new URLSearchParams({
      job_id: jobId,
      page: String(page || 1),
    });
    if (bbox) query.set("bbox", bbox);
    return `${API_BASE}/upload/preview/page?${query.toString()}`;
  }, [bbox, jobId, page]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(false);
  }, [imageUrl, open]);

  if (!open || !jobId) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm">
      <div className="relative flex h-[90vh] w-[92vw] max-w-5xl flex-col rounded-xl border border-white/10 bg-[#111] shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-white">
              <FileText className="h-5 w-5 text-blue-400" />
              <span className="font-medium">{title || "Preprocessing Source"}</span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">
              <span className="font-mono text-blue-300">Page {page}</span>
              {subtitle ? <span className="truncate">{subtitle}</span> : null}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}
              className="rounded p-2 text-gray-400 hover:bg-white/10 hover:text-white"
              title="Zoom Out"
            >
              <ZoomOut size={18} />
            </button>
            <span className="w-10 text-center text-xs text-gray-500">
              {Math.round(zoom * 100)}%
            </span>
            <button
              onClick={() => setZoom((value) => Math.min(3, value + 0.25))}
              className="rounded p-2 text-gray-400 hover:bg-white/10 hover:text-white"
              title="Zoom In"
            >
              <ZoomIn size={18} />
            </button>
            <div className="mx-1 h-4 w-px bg-white/10" />
            <button
              onClick={onClose}
              className="rounded p-2 text-gray-400 hover:bg-white/10 hover:text-red-400"
            >
              <X size={20} />
            </button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 gap-0 md:grid-cols-[minmax(0,1fr)_320px]">
          <div className="overflow-auto bg-[#0a0a0a] p-6">
            <div
              className="relative mx-auto overflow-hidden rounded border border-white/10 bg-white shadow-lg"
              style={{ width: `${800 * zoom}px`, minHeight: "300px" }}
            >
              {loading && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#111]">
                  <div className="flex flex-col items-center gap-2 text-gray-400">
                    <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
                    <span className="text-xs">Rendering source page...</span>
                  </div>
                </div>
              )}

              {error && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#111] text-red-400">
                  <div className="flex flex-col items-center gap-2">
                    <AlertCircle className="h-6 w-6" />
                    <span className="text-xs">Failed to load preview page</span>
                  </div>
                </div>
              )}

              <Image
                src={imageUrl}
                alt={`Preview page ${page}`}
                width={800}
                height={1100}
                sizes="100vw"
                unoptimized
                className={`h-auto w-full object-contain ${loading ? "opacity-0" : "opacity-100"}`}
                onLoad={() => setLoading(false)}
                onError={() => {
                  setLoading(false);
                  setError(true);
                }}
              />
            </div>
          </div>

          <aside className="border-l border-white/10 bg-black/40 p-4">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-300/70">
              Extracted Text
            </div>
            <div className="mt-3 max-h-[70vh] overflow-auto rounded-lg border border-white/10 bg-black/50 p-3 text-xs leading-6 text-gray-200 whitespace-pre-wrap">
              {excerpt?.trim() ? excerpt : "No extracted text captured for this item."}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
