"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Eye,
  FileSearch,
  ImageIcon,
  LayoutGrid,
  Loader2,
  RefreshCcw,
  Rows4,
  ScanSearch,
  TableProperties,
} from "lucide-react";

import {
  fetchUploadPreprocessingPagePreview,
  type PreprocessingPreviewPageResponse,
} from "@/app/lib/api";
import type {
  PreprocessingPreviewChunk,
  PreprocessingPreviewElement,
  PreprocessingPreviewResponse,
} from "@/app/lib/api";
import PreprocessingSourceViewerModal from "./PreprocessingSourceViewerModal";
import TableInspectionModal from "./TableInspectionModal";

type PreviewTab = "metadata" | "tables" | "images" | "chunks" | "removed";

interface Props {
  jobId: string | null;
  preview: PreprocessingPreviewResponse | null;
  loading: boolean;
  error: string | null;
  onRetry?: () => void;
  onLoadFullPreview?: () => void;
  defaultTab?: PreviewTab;
}

function SummaryCard({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-black/40 px-3 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/60">
        {label}
      </div>
      <div className="mt-2 text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

function formatSegmentationSummary(stage: Record<string, any> | null | undefined) {
  const safe = stage && typeof stage === "object" ? stage : {};
  return `Table ${Number(safe.table || 0)} | Text ${Number(safe.text || 0)} | Image ${Number(safe.image || 0)} | Other ${Number(safe.other || 0)}`;
}

export default function PreprocessingPreviewPanel({
  jobId,
  preview,
  loading,
  error,
  onRetry,
  onLoadFullPreview,
  defaultTab = "metadata",
}: Props) {
  const [activeTab, setActiveTab] = useState<PreviewTab>(defaultTab);
  const [pageLookup, setPageLookup] = useState("");
  const [pagePreview, setPagePreview] = useState<PreprocessingPreviewPageResponse | null>(null);
  const [pagePreviewLoading, setPagePreviewLoading] = useState(false);
  const [pagePreviewError, setPagePreviewError] = useState<string | null>(null);
  const [viewerState, setViewerState] = useState<{
    page: number;
    bbox?: string;
    title?: string;
    subtitle?: string;
    excerpt?: string;
  } | null>(null);
  const [tableInspector, setTableInspector] = useState<PreprocessingPreviewElement | null>(null);

  const removedBreakdown = useMemo(() => {
    if (pagePreview) {
      return [["page_removed_elements", Number(pagePreview.summary?.removed_elements || 0)]] as Array<[string, number]>;
    }
    const report = preview?.summary?.filter_report as Record<string, any> | undefined;
    const breakdown = report?.removed_breakdown;
    if (!breakdown || typeof breakdown !== "object") return [];
    return Object.entries(breakdown) as Array<[string, number]>;
  }, [pagePreview, preview]);

  const segregationSummary = useMemo(() => {
    const raw =
      (pagePreview?.summary?.element_groups?.raw as Record<string, any> | undefined) ||
      (preview?.summary?.element_groups?.raw as Record<string, any> | undefined) ||
      null;
    const filtered =
      (pagePreview?.summary?.element_groups?.filtered as Record<string, any> | undefined) ||
      (preview?.summary?.element_groups?.filtered as Record<string, any> | undefined) ||
      null;
    const removed =
      (pagePreview?.summary?.element_groups?.removed as Record<string, any> | undefined) ||
      (preview?.summary?.element_groups?.removed as Record<string, any> | undefined) ||
      null;
    return { raw, filtered, removed };
  }, [pagePreview, preview]);

  const activeElements =
    pagePreview && activeTab === "metadata"
      ? pagePreview.elements
      : preview?.metadata_evidence || [];
  const activeTables = pagePreview ? pagePreview.tables : preview?.tables || [];
  const activeImages = pagePreview ? pagePreview.images : preview?.images || [];
  const activeChunks = pagePreview ? pagePreview.chunks : preview?.chunks || [];
  const activeRemoved = pagePreview ? pagePreview.removed_elements : preview?.removed_elements || [];
  const sourcePageRenderingAvailable =
    pagePreview?.source_page_rendering_available ??
    preview?.source_page_rendering_available ??
    true;
  const totalPages = Number(preview?.document_stats?.page_count || 0);
  const parsedLookupPage = Number(pageLookup);
  const currentInspectorPage =
    pagePreview?.page ||
    (Number.isFinite(parsedLookupPage) && parsedLookupPage >= 1 ? parsedLookupPage : 1);

  useEffect(() => {
    setActiveTab(defaultTab);
    setPagePreview(null);
    setPagePreviewError(null);
    setPageLookup("");
    setTableInspector(null);
  }, [defaultTab, jobId, preview?.preview_mode]);

  const tabs: Array<{ id: PreviewTab; label: string; icon: any }> = [
    { id: "metadata", label: "OCR Metadata", icon: ScanSearch },
    { id: "tables", label: "Tables", icon: TableProperties },
    { id: "images", label: "Images", icon: ImageIcon },
    { id: "chunks", label: "Chunks", icon: Rows4 },
    { id: "removed", label: "Removed", icon: LayoutGrid },
  ];

  function openViewer(item: {
    page: number;
    bbox?: string;
    title?: string;
    subtitle?: string;
    excerpt?: string;
  }) {
    setViewerState(item);
  }

  async function handleLoadPagePreview(requestedPage?: number) {
    if (!jobId) return;
    const page = requestedPage ?? Number(pageLookup);
    if (!Number.isFinite(page) || page < 1) {
      setPagePreviewError("Enter a valid page number.");
      return;
    }
    if (totalPages > 0 && page > totalPages) {
      setPagePreviewError(`Enter a page between 1 and ${totalPages}.`);
      return;
    }

    setPagePreviewLoading(true);
    setPagePreviewError(null);
    try {
      const result = await fetchUploadPreprocessingPagePreview(
        jobId,
        page,
        (preview?.preview_mode as "quick" | "full" | undefined) || "auto"
      );
      setPagePreview(result);
      setPageLookup(String(result.page || page));
    } catch (err: any) {
      setPagePreviewError(err?.message || "Failed to load page preview.");
    } finally {
      setPagePreviewLoading(false);
    }
  }

  function handleStepPage(direction: -1 | 1) {
    const nextPage = currentInspectorPage + direction;
    if (nextPage < 1) return;
    if (totalPages > 0 && nextPage > totalPages) return;
    setPageLookup(String(nextPage));
    void handleLoadPagePreview(nextPage);
  }

  function renderElementRows(
    elements: PreprocessingPreviewElement[],
    emptyLabel: string,
    options?: { inspectTables?: boolean; sourcePageRenderingAvailable?: boolean }
  ) {
    if (!elements.length) {
      return (
        <div className="rounded-lg border border-dashed border-white/10 bg-black/30 px-4 py-6 text-sm text-gray-400">
          {emptyLabel}
        </div>
      );
    }

    return (
      <div className="space-y-3">
        {elements.map((item, index) => (
          <div
            key={`${item.id || item.type}-${index}`}
            className="rounded-lg border border-white/10 bg-black/40 p-3"
          >
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-400">
              <span className="rounded bg-blue-500/10 px-2 py-1 text-blue-200">
                {item.type || "Element"}
              </span>
              <span className="font-mono text-blue-300">Page {item.page}</span>
              {item.discard_reason ? (
                <span className="rounded bg-red-500/10 px-2 py-1 text-red-200">
                  {item.discard_reason}
                </span>
              ) : null}
            </div>
            <pre className="mt-3 overflow-auto whitespace-pre-wrap rounded-md border border-white/10 bg-black/50 p-3 text-xs leading-6 text-gray-200">
              {item.text || "No text extracted"}
            </pre>
            <div className="mt-3 flex flex-wrap justify-end gap-2">
              {options?.inspectTables ? (
                <button
                  type="button"
                  onClick={() => setTableInspector(item)}
                  className="inline-flex items-center gap-2 rounded-md border border-blue-400/20 bg-blue-500/10 px-3 py-1.5 text-xs text-blue-100 transition hover:bg-blue-500/20"
                >
                  <TableProperties className="h-3.5 w-3.5" />
                  Inspect table
                </button>
              ) : null}
              {options?.sourcePageRenderingAvailable !== false ? (
                <button
                  type="button"
                  onClick={() =>
                    openViewer({
                      page: item.page || 1,
                      bbox: item.bbox,
                      title: item.type,
                      subtitle: `Page ${item.page}`,
                      excerpt: item.text,
                    })
                  }
                  className="inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-gray-200 transition hover:bg-white/10 hover:text-white"
                >
                  <Eye className="h-3.5 w-3.5" />
                  View source
                </button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    );
  }

  function renderChunkRows(chunks: PreprocessingPreviewChunk[]) {
    if (!chunks.length) {
      return (
        <div className="rounded-lg border border-dashed border-white/10 bg-black/30 px-4 py-6 text-sm text-gray-400">
          No chunks generated yet.
        </div>
      );
    }

    return (
      <div className="space-y-3">
        {chunks.map((chunk, index) => (
          <div
            key={`${chunk.id || "chunk"}-${chunk.page || 0}-${index}`}
            className="rounded-lg border border-white/10 bg-black/40 p-3"
          >
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-400">
              <span className="rounded bg-blue-500/10 px-2 py-1 text-blue-200">
                {chunk.chunk_type || "text"}
              </span>
              <span className="font-mono text-blue-300">Page {chunk.page}</span>
              {chunk.section ? (
                <span className="rounded bg-white/10 px-2 py-1 text-gray-300">
                  {chunk.section}
                </span>
              ) : null}
            </div>
            <pre className="mt-3 overflow-auto whitespace-pre-wrap rounded-md border border-white/10 bg-black/50 p-3 text-xs leading-6 text-gray-200">
              {chunk.content || "No chunk content"}
            </pre>
            {sourcePageRenderingAvailable ? (
              <div className="mt-3 flex justify-end">
                <button
                  type="button"
                  onClick={() =>
                    openViewer({
                      page: chunk.page || 1,
                      bbox: chunk.bbox,
                      title: "Chunk Source",
                      subtitle: chunk.section || chunk.chunk_type || "Chunk",
                      excerpt: chunk.content,
                    })
                  }
                  className="inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-gray-200 transition hover:bg-white/10 hover:text-white"
                >
                  <Eye className="h-3.5 w-3.5" />
                  Map to page
                </button>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="mt-5 rounded-xl border border-white/10 bg-black/35 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <FileSearch className="h-4 w-4 text-blue-400" />
            Preprocessing Preview
          </div>
          <p className="mt-1 text-xs text-blue-200/70">
            Review what OCR, table and figure extraction, filtering, and chunking produced before commit.
          </p>
          {preview ? (
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-gray-400">
              <span className="rounded bg-white/10 px-2 py-1 text-gray-200">
                {preview.preview_mode === "quick" ? "Quick Preview" : "Full Preview"}
              </span>
              <span>
                {preview.document_stats?.page_count ?? 0} pages
              </span>
              <span>
                {preview.document_stats?.file_size_mb ?? 0} MB
              </span>
              {preview.preview_mode === "quick" ? (
                <span>
                  Indexed pages: {preview.summary?.indexed_page_count ?? preview.indexed_pages?.length ?? 0}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {preview?.can_load_full && onLoadFullPreview ? (
            <button
              type="button"
              onClick={onLoadFullPreview}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-md border border-blue-400/30 bg-blue-500/10 px-3 py-1.5 text-xs text-blue-100 transition hover:bg-blue-500/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Load Full Preview
            </button>
          ) : null}

          {onRetry ? (
            <button
              type="button"
              onClick={onRetry}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-gray-200 transition hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCcw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              {preview ? "Refresh preview" : "Load preview"}
            </button>
          ) : null}
        </div>
      </div>

      {loading ? (
        <div className="mt-4 rounded-lg border border-blue-500/20 bg-blue-500/10 px-4 py-5 text-sm text-blue-100">
          Building preprocessing preview. This may take a moment for large PDFs.
        </div>
      ) : null}

      {error ? (
        <div className="mt-4 flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-4 text-sm text-red-100">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {!loading && !error && !preview ? (
        <div className="mt-4 rounded-lg border border-white/10 bg-black/30 px-4 py-5 text-sm text-gray-300">
          Load the preprocessing preview on demand if you want to inspect OCR elements, tables, images, and chunks before commit.
        </div>
      ) : null}

      {preview ? (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
            <SummaryCard label="Raw OCR Elements" value={preview.summary.raw_elements ?? 0} />
            <SummaryCard label="Filtered Elements" value={preview.summary.filtered_elements ?? 0} />
            <SummaryCard label="Removed Elements" value={preview.summary.removed_elements ?? 0} />
            <SummaryCard label="Tables" value={preview.summary.tables ?? 0} />
            <SummaryCard label="Images" value={preview.summary.images ?? 0} />
            <SummaryCard label="Chunks" value={preview.summary.chunks ?? 0} />
          </div>

          {(segregationSummary.raw || segregationSummary.filtered || segregationSummary.removed) ? (
        <div className="mt-4 rounded-lg border border-white/10 bg-black/40 p-3">
              <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/60">
                Element Segregation
              </div>
              <div className="mt-3 grid gap-3 md:grid-cols-3 text-xs text-gray-300">
                <div className="rounded-md border border-white/10 bg-black/40 px-3 py-3">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Raw</div>
                  <div className="mt-2 leading-6">{formatSegmentationSummary(segregationSummary.raw)}</div>
                </div>
                <div className="rounded-md border border-white/10 bg-black/40 px-3 py-3">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Filtered</div>
                  <div className="mt-2 leading-6">{formatSegmentationSummary(segregationSummary.filtered)}</div>
                </div>
                <div className="rounded-md border border-white/10 bg-black/40 px-3 py-3">
                  <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">Removed</div>
                  <div className="mt-2 leading-6">{formatSegmentationSummary(segregationSummary.removed)}</div>
                </div>
              </div>
            </div>
          ) : null}

          <div className="mt-4 rounded-lg border border-white/10 bg-black/40 p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/60">
                  Page Inspector
                </div>
                <div className="mt-1 text-xs text-gray-400">
                  Load any page on demand without preprocessing the whole document first.
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleStepPage(-1)}
                  disabled={pagePreviewLoading || !jobId || currentInspectorPage <= 1}
                  className="inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/5 px-3 py-2 text-xs text-gray-200 transition hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </button>
                <input
                  type="number"
                  min={1}
                  max={preview.document_stats?.page_count || undefined}
                  value={pageLookup}
                  onChange={(e) => setPageLookup(e.target.value)}
                  placeholder="Page"
                  className="w-24 rounded-md border border-white/10 bg-black/50 px-3 py-2 text-xs text-white outline-none focus:border-blue-400/50"
                />
                <button
                  type="button"
                  onClick={() => {
                    void handleLoadPagePreview();
                  }}
                  disabled={pagePreviewLoading || !jobId}
                  className="inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/5 px-3 py-2 text-xs text-gray-200 transition hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {pagePreviewLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                  Load Page
                </button>
                <button
                  type="button"
                  onClick={() => handleStepPage(1)}
                  disabled={
                    pagePreviewLoading
                    || !jobId
                    || (totalPages > 0 && currentInspectorPage >= totalPages)
                  }
                  className="inline-flex items-center gap-2 rounded-md border border-white/15 bg-white/5 px-3 py-2 text-xs text-gray-200 transition hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
                {pagePreview ? (
                  <button
                    type="button"
                    onClick={() => {
                      setPagePreview(null);
                      setPagePreviewError(null);
                    }}
                    className="rounded-md border border-white/10 bg-black/30 px-3 py-2 text-xs text-gray-300 transition hover:bg-white/10 hover:text-white"
                  >
                    Clear
                  </button>
                ) : null}
              </div>
            </div>
            {pagePreviewError ? (
              <div className="mt-3 text-xs text-red-300">{pagePreviewError}</div>
            ) : null}
            {!sourcePageRenderingAvailable ? (
              <div className="mt-3 text-xs text-amber-300">
                Source page rendering is unavailable for this job because the local PDF was cleaned up after ingestion. Cached OCR elements, tables, images, and chunks are still available.
              </div>
            ) : null}
            {pagePreview ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-gray-300">
                <span className="rounded bg-blue-500/10 px-2 py-1 text-blue-200">
                  Page {pagePreview.page}
                </span>
                {totalPages > 0 ? (
                  <span className="rounded bg-white/10 px-2 py-1">
                    {pagePreview.page} / {totalPages}
                  </span>
                ) : null}
                <span className="rounded bg-white/10 px-2 py-1">
                  Source: {pagePreview.source_scope || "unknown"}
                </span>
                {!pagePreview.available_in_scope ? (
                  <span className="text-gray-400">
                    Loaded on demand because this page was outside the current preview window.
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              const active = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-xs transition ${
                    active
                      ? "bg-blue-600 text-white"
                      : "border border-white/10 bg-black/40 text-gray-300 hover:bg-white/10 hover:text-white"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tab.label}
                </button>
              );
            })}
          </div>

          <div className="mt-4">
            {activeTab === "metadata" ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-white/10 bg-black/40 p-3">
                  <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/60">
                    {pagePreview ? "Page Elements" : "Extracted Candidates"}
                  </div>
                  {pagePreview ? (
                    <div className="mt-3 text-xs text-gray-300">
                      Showing filtered OCR elements for page {pagePreview.page}.
                    </div>
                  ) : (
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      {Object.entries(preview.metadata_candidates || {}).map(([key, value]) => (
                        <div key={key} className="rounded-md border border-white/10 bg-black/40 px-3 py-3">
                          <div className="text-[10px] uppercase tracking-[0.16em] text-gray-500">
                            {key.replace(/_/g, " ")}
                          </div>
                          <div className="mt-2 text-sm text-white">
                            {String(value?.value || "Not detected")}
                          </div>
                          <div className="mt-1 text-[11px] text-blue-300/70">
                            Confidence: {typeof value?.confidence === "number" ? value.confidence.toFixed(2) : "n/a"}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {renderElementRows(
                  activeElements,
                  pagePreview
                    ? "No OCR elements were captured for this page."
                    : "No metadata OCR evidence was captured.",
                  { sourcePageRenderingAvailable }
                )}
              </div>
            ) : null}

            {activeTab === "tables" ? (
              renderElementRows(
                activeTables,
                pagePreview
                  ? "No tables were extracted for this page."
                  : "No tables were extracted from this document.",
                {
                  inspectTables: true,
                  sourcePageRenderingAvailable,
                }
              )
            ) : null}

            {activeTab === "images" ? (
              renderElementRows(
                activeImages,
                pagePreview
                  ? "No images or figure captions were retained for this page."
                  : "No images or figure captions were retained from this document.",
                { sourcePageRenderingAvailable }
              )
            ) : null}

            {activeTab === "chunks" ? (
              renderChunkRows(activeChunks)
            ) : null}

            {activeTab === "removed" ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-white/10 bg-black/40 p-3">
                  <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-300/60">
                    Removal Summary
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {removedBreakdown.length ? (
                      removedBreakdown.map(([reason, count]) => (
                        <div
                          key={reason}
                          className="rounded-md border border-white/10 bg-black/50 px-3 py-2 text-xs text-gray-200"
                        >
                          <span className="text-red-300">{reason}</span>: {count}
                        </div>
                      ))
                    ) : (
                      <div className="text-xs text-gray-400">
                        No removed elements were recorded.
                      </div>
                    )}
                  </div>
                </div>
                {renderElementRows(
                  activeRemoved,
                  pagePreview
                    ? "No removed elements were recorded for this page."
                    : "No removed elements recorded. If you expected header/footer filtering, this PDF may not have been labeled clearly by OCR.",
                  { sourcePageRenderingAvailable }
                )}
              </div>
            ) : null}
          </div>
        </>
      ) : null}

      <PreprocessingSourceViewerModal
        open={Boolean(viewerState)}
        jobId={jobId}
        page={viewerState?.page || 1}
        bbox={viewerState?.bbox}
        title={viewerState?.title}
        subtitle={viewerState?.subtitle}
        excerpt={viewerState?.excerpt}
        onClose={() => setViewerState(null)}
      />

      <TableInspectionModal
        open={Boolean(tableInspector)}
        jobId={jobId}
        table={tableInspector}
        sourcePageRenderingAvailable={sourcePageRenderingAvailable}
        onClose={() => setTableInspector(null)}
      />
    </div>
  );
}
