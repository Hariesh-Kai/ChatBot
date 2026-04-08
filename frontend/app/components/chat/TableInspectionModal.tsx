"use client";

import { useEffect, useMemo, useState } from "react";
import Image from "next/image";
import {
  AlertCircle,
  FileText,
  Loader2,
  ScanText,
  TableProperties,
  X,
} from "lucide-react";

import type { PreprocessingPreviewElement } from "@/app/lib/api";
import { API_BASE } from "@/app/lib/config";

interface Props {
  open: boolean;
  jobId: string | null;
  table: PreprocessingPreviewElement | null;
  onClose: () => void;
}

function countColumns(row: HTMLTableRowElement) {
  return Array.from(row.children).reduce((total, cell) => {
    const span = Number(cell.getAttribute("colspan") || 1);
    return total + (Number.isFinite(span) && span > 0 ? span : 1);
  }, 0);
}

export default function TableInspectionModal({
  open,
  jobId,
  table,
  onClose,
}: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const metadata = (table?.metadata || {}) as Record<string, any>;
  const coordinates = (metadata.coordinates || {}) as Record<string, any>;
  const html = String(table?.html || metadata.text_as_html || "").trim();
  const ocrText = String(table?.text || "").trim();
  const normalizedTable = (table?.normalized_table || metadata.normalized_table || null) as
    | Record<string, any>
    | null;
  const normalizedMeta = (normalizedTable?.metadata || metadata.normalized_table_signals || null) as
    | Record<string, any>
    | null;
  const normalizedJson = normalizedTable ? JSON.stringify(normalizedTable, null, 2) : "";

  const imageUrl = !jobId || !table?.bbox
    ? ""
    : (() => {
        const query = new URLSearchParams({
          job_id: jobId,
          page: String(table.page || 1),
          bbox: table.bbox,
          crop: "true",
        });
        return `${API_BASE}/upload/preview/page?${query.toString()}`;
      })();

  const tableStats = useMemo(() => {
    if (!html || typeof DOMParser === "undefined") {
      return { rows: 0, columns: 0, headers: 0 };
    }

    try {
      const doc = new DOMParser().parseFromString(html, "text/html");
      const rows = Array.from(doc.querySelectorAll("tr"));
      const columns = rows.reduce((max, row) => Math.max(max, countColumns(row)), 0);
      const headers = doc.querySelectorAll("th").length;
      return { rows: rows.length, columns, headers };
    } catch {
      return { rows: 0, columns: 0, headers: 0 };
    }
  }, [html]);

  useEffect(() => {
    if (!open) return;
    setLoading(Boolean(imageUrl));
    setError(false);
  }, [imageUrl, open]);

  if (!open || !jobId || !table) return null;

  return (
    <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/85 backdrop-blur-sm">
      <div className="relative flex h-[92vh] w-[95vw] max-w-7xl flex-col rounded-xl border border-white/10 bg-[#101010] shadow-2xl">
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-white">
              <TableProperties className="h-5 w-5 text-blue-400" />
              <span className="font-medium">Table Inspection</span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-400">
              <span className="font-mono text-blue-300">Page {table.page}</span>
              <span>Focused crop for one detected table</span>
            </div>
          </div>

          <button
            onClick={onClose}
            className="rounded p-2 text-gray-400 hover:bg-white/10 hover:text-red-400"
          >
            <X size={20} />
          </button>
        </div>

        <div className="grid min-h-0 flex-1 gap-0 xl:grid-cols-[minmax(0,1.15fr)_420px]">
          <div className="overflow-auto bg-[#080808] p-6">
            <div className="mx-auto max-w-5xl rounded-xl border border-white/10 bg-white/95 p-4 shadow-2xl">
              <div className="mb-3 flex flex-wrap gap-2">
                <div className="rounded-full border border-sky-400/20 bg-sky-500/10 px-3 py-1 text-[11px] font-medium text-sky-700">
                  Table crop
                </div>
                <div className="rounded-full border border-black/10 bg-black/5 px-3 py-1 text-[11px] text-black/70">
                  OCR bbox highlighted in red
                </div>
              </div>

              <div className="relative min-h-[280px] overflow-hidden rounded-lg border border-black/10 bg-white">
                {loading ? (
                  <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/90">
                    <div className="flex flex-col items-center gap-2 text-gray-600">
                      <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
                      <span className="text-xs">Rendering table crop...</span>
                    </div>
                  </div>
                ) : null}

                {error ? (
                  <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/95 text-red-500">
                    <div className="flex flex-col items-center gap-2">
                      <AlertCircle className="h-6 w-6" />
                      <span className="text-xs">Failed to render the focused table image</span>
                    </div>
                  </div>
                ) : null}

                {imageUrl ? (
                  <Image
                    src={imageUrl}
                    alt={`Detected table on page ${table.page}`}
                    width={1400}
                    height={1000}
                    sizes="100vw"
                    unoptimized
                    className={`h-auto w-full object-contain ${loading ? "opacity-0" : "opacity-100"}`}
                    onLoad={() => setLoading(false)}
                    onError={() => {
                      setLoading(false);
                      setError(true);
                    }}
                  />
                ) : (
                  <div className="flex min-h-[280px] items-center justify-center text-sm text-gray-500">
                    No bbox was available for this table.
                  </div>
                )}
              </div>
            </div>
          </div>

          <aside className="overflow-auto border-l border-white/10 bg-black/35 p-4">
            <div className="rounded-xl border border-white/10 bg-black/40 p-4">
              <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/70">
                Extraction Summary
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
                <div className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-xs text-gray-200">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Rows</div>
                  <div className="mt-1 text-sm text-white">{tableStats.rows || "n/a"}</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-xs text-gray-200">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Columns</div>
                  <div className="mt-1 text-sm text-white">{tableStats.columns || "n/a"}</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-xs text-gray-200">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Header Cells</div>
                  <div className="mt-1 text-sm text-white">{tableStats.headers || "n/a"}</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-xs text-gray-200">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Detection Confidence</div>
                  <div className="mt-1 text-sm text-white">
                    {typeof metadata.detection_class_prob === "number"
                      ? metadata.detection_class_prob.toFixed(3)
                      : "n/a"}
                  </div>
                </div>
                <div className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-xs text-gray-200">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Extraction Status</div>
                  <div className="mt-1 text-sm text-white">{String(metadata.is_extracted || "n/a")}</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-black/40 px-3 py-2 text-xs text-gray-200">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Layout Space</div>
                  <div className="mt-1 text-sm text-white">
                    {coordinates.layout_width && coordinates.layout_height
                      ? `${coordinates.layout_width} x ${coordinates.layout_height}`
                      : "n/a"}
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-4 rounded-xl border border-white/10 bg-black/40 p-4">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/70">
                <ScanText className="h-3.5 w-3.5" />
                OCR Text
              </div>
              <div className="mt-3 max-h-[28vh] overflow-auto rounded-lg border border-white/10 bg-black/55 p-3 text-xs leading-6 whitespace-pre-wrap text-gray-200">
                {ocrText || "No OCR text was captured for this table."}
              </div>
            </div>

            <div className="mt-4 rounded-xl border border-white/10 bg-black/40 p-4">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/70">
                <FileText className="h-3.5 w-3.5" />
                Structured Table HTML
              </div>
              <div className="mt-2 text-xs text-gray-400">
                This is the extracted table structure returned by the preprocessing pipeline.
              </div>
              <pre className="mt-3 max-h-[32vh] overflow-auto rounded-lg border border-white/10 bg-black/55 p-3 text-[11px] leading-6 whitespace-pre-wrap text-gray-200">
                {html || "No structured HTML was captured for this table."}
              </pre>
            </div>

            <div className="mt-4 rounded-xl border border-white/10 bg-black/40 p-4">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/70">
                <FileText className="h-3.5 w-3.5" />
                Normalized Table JSON
              </div>
              <div className="mt-2 text-xs text-gray-400">
                Hierarchical columns plus exact row-to-column mapping from the normalization engine.
              </div>
              {normalizedTable?.caption ? (
                <div className="mt-3 rounded-lg border border-emerald-400/15 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
                  Caption: {String(normalizedTable.caption)}
                </div>
              ) : null}
              {normalizedMeta?.units
              || normalizedMeta?.context
              || typeof normalizedMeta?.confidence?.structure === "number"
              || typeof normalizedMeta?.confidence?.headers === "number" ? (
                <div className="mt-3 space-y-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-xs text-gray-200">
                  {normalizedMeta?.units ? <div>Units: {String(normalizedMeta.units)}</div> : null}
                  {normalizedMeta?.context ? <div>Context: {String(normalizedMeta.context)}</div> : null}
                  {typeof normalizedMeta?.confidence?.structure === "number" ? (
                    <div>Structure confidence: {Number(normalizedMeta.confidence.structure).toFixed(3)}</div>
                  ) : null}
                  {typeof normalizedMeta?.confidence?.headers === "number" ? (
                    <div>Header confidence: {Number(normalizedMeta.confidence.headers).toFixed(3)}</div>
                  ) : null}
                </div>
              ) : null}
              <pre className="mt-3 max-h-[32vh] overflow-auto rounded-lg border border-white/10 bg-black/55 p-3 text-[11px] leading-6 whitespace-pre-wrap text-gray-200">
                {normalizedJson || "No normalized table JSON is available for this table."}
              </pre>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
