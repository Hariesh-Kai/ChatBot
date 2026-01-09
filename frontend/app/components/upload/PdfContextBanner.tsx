"use client";

import { FileText, AlertTriangle, CheckCircle } from "lucide-react";
import { PdfFile } from "@/app/lib/pdf";
import PdfFileChip from "./PdfFileChip";

/* =========================================================
   TYPES
========================================================= */

export type PdfContextStatus =
  | "uploaded"
  | "waiting_metadata"
  | "processing"
  | "ready"
  | "error";

/* ================= PROPS ================= */

interface PdfContextBannerProps {
  file: PdfFile;
  revision?: number;
  status?: PdfContextStatus;
  message?: string;
}

/* =========================================================
   COMPONENT
========================================================= */

export default function PdfContextBanner({
  file,
  revision,
  status = "uploaded",
  message,
}: PdfContextBannerProps) {
  return (
    <div
      className="
        mb-4 flex items-center gap-3
        rounded-xl border border-white/10
        bg-[#111] px-4 py-3
      "
    >
      {/* ================= FILE CHIP ================= */}
      <PdfFileChip file={file} />

      {/* ================= CONTEXT TEXT ================= */}
      <div className="flex flex-col gap-0.5">
        <div className="flex items-center gap-2 text-xs text-gray-300">
          <FileText className="h-3.5 w-3.5 text-gray-400" />
          <span>
            Active document
            {typeof revision === "number" && (
              <>
                {" "}
                • <span className="text-white">v{revision}</span>
              </>
            )}
          </span>
        </div>

        {/* ================= STATUS ================= */}
        <div className="flex items-center gap-1.5 text-[11px]">
          {status === "waiting_metadata" && (
            <>
              <AlertTriangle className="h-3.5 w-3.5 text-yellow-400" />
              <span className="text-yellow-300">
                Metadata required
              </span>
            </>
          )}

          {status === "processing" && (
            <span className="text-blue-400">
              Processing document…
            </span>
          )}

          {status === "ready" && (
            <>
              <CheckCircle className="h-3.5 w-3.5 text-green-400" />
              <span className="text-green-300">
                Ready for questions
              </span>
            </>
          )}

          {status === "error" && (
            <span className="text-red-400">
              Failed to process document
            </span>
          )}

          {status === "uploaded" && (
            <span className="text-gray-400">
              Uploaded successfully
            </span>
          )}
        </div>

        {/* ================= OPTIONAL MESSAGE ================= */}
        {message && (
          <div className="text-[11px] text-gray-500">
            {message}
          </div>
        )}
      </div>
    </div>
  );
}
