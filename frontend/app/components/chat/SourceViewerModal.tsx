"use client";

import { useEffect, useState } from "react";
import { X, ZoomIn, ZoomOut, FileText, Loader2, AlertCircle } from "lucide-react";
import { API_BASE } from "@/app/lib/config";
import { RagSource } from "@/app/lib/types";

interface Props {
  open: boolean;
  sources: RagSource[];
  onClose: () => void;
}

export default function SourceViewerModal({ open, sources, onClose }: Props) {
  const [zoom, setZoom] = useState(1);

  // Reset zoom on open
  useEffect(() => {
    if (open) setZoom(1);
  }, [open]);

  if (!open || sources.length === 0) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm animate-fade-in">
      
      {/* --- CONTAINER --- */}
      <div className="relative flex h-[90vh] w-[90vw] max-w-5xl flex-col rounded-xl border border-white/10 bg-[#111] shadow-2xl">
        
        {/* --- HEADER --- */}
        <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-blue-400" />
            <span className="font-medium text-white">Source Viewer</span>
            <span className="ml-2 rounded-full bg-white/10 px-2 py-0.5 text-xs text-gray-400">
              {sources.length} Page{sources.length > 1 ? "s" : ""}
            </span>
          </div>

          <div className="flex items-center gap-2">
            {/* Zoom Controls */}
            <button 
                onClick={() => setZoom(z => Math.max(0.5, z - 0.25))} 
                className="p-2 text-gray-400 hover:text-white hover:bg-white/10 rounded"
                title="Zoom Out"
            >
                <ZoomOut size={18} />
            </button>
            <span className="text-xs text-gray-500 w-10 text-center">{Math.round(zoom * 100)}%</span>
            <button 
                onClick={() => setZoom(z => Math.min(3, z + 0.25))} 
                className="p-2 text-gray-400 hover:text-white hover:bg-white/10 rounded"
                title="Zoom In"
            >
                <ZoomIn size={18} />
            </button>
            
            <div className="mx-2 h-4 w-px bg-white/10" />

            <button onClick={onClose} className="p-2 text-gray-400 hover:text-red-400 hover:bg-white/10 rounded">
              <X size={20} />
            </button>
          </div>
        </div>

        {/* --- SCROLLABLE CONTENT --- */}
        <div className="flex-1 overflow-y-auto bg-[#0a0a0a] p-8">
          <div className="flex flex-col items-center gap-8">
            {sources.map((src, idx) => (
              <SourcePage 
                key={`${src.id}-${idx}`} 
                source={src} 
                zoom={zoom} 
              />
            ))}
          </div>
        </div>

      </div>
    </div>
  );
}

/**
 * Internal Component to handle individual image loading states
 */
function SourcePage({ source, zoom }: { source: RagSource, zoom: number }) {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    // 🔥 CRITICAL FIX: Handle legacy data ("page_number") vs new data ("page")
    // This prevents 'undefined' in the URL if you are viewing old chat history
    // It prioritizes 'page' (new backend), falls back to 'page_number' (old backend), or defaults to 1.
    const safePage = source.page ?? (source as any).page_number ?? 1;

    const imageUrl = `${API_BASE}/render/image?file=${encodeURIComponent(source.fileName)}&page=${safePage}&company_doc_id=${source.company_doc_id}&revision=${source.revision}&bbox=${source.bbox || ""}`;

    return (
        <div className="relative group flex flex-col items-center">
            {/* Label */}
            <div className="mb-2 w-full max-w-[800px] flex justify-between text-xs text-gray-400 px-1">
                <span className="truncate max-w-[70%]">{source.fileName}</span>
                <span className="font-mono text-blue-300">Page {safePage}</span>
            </div>

            {/* Image Container */}
            <div 
                className="relative overflow-hidden rounded shadow-lg border border-white/10 bg-white transition-all duration-200"
                style={{ width: `${800 * zoom}px`, minHeight: '300px' }} 
            >
                {/* Loader Overlay */}
                {loading && (
                    <div className="absolute inset-0 flex items-center justify-center bg-[#111] z-10">
                        <div className="flex flex-col items-center gap-2 text-gray-400">
                            <Loader2 className="h-6 w-6 animate-spin text-blue-500" />
                            <span className="text-xs">Rendering page {safePage}...</span>
                        </div>
                    </div>
                )}

                {/* Error Overlay */}
                {error && (
                    <div className="absolute inset-0 flex items-center justify-center bg-[#111] z-10 text-red-400">
                        <div className="flex flex-col items-center gap-2">
                            <AlertCircle className="h-6 w-6" />
                            <span className="text-xs">Failed to load page image</span>
                            <span className="text-[10px] opacity-50">Debug Page: {safePage}</span>
                        </div>
                    </div>
                )}

                {/* The Actual Image */}
                <img
                    src={imageUrl}
                    alt={`Page ${safePage} of ${source.fileName}`}
                    className={`w-full h-auto object-contain ${loading ? 'opacity-0' : 'opacity-100'}`}
                    onLoad={() => setLoading(false)}
                    onError={() => {
                        setLoading(false);
                        setError(true);
                    }}
                />
            </div>
        </div>
    );
}