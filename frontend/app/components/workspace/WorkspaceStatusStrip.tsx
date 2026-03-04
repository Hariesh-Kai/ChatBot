"use client";

import type { WorkspaceStatusStripData } from "@/app/components/workspace/types";

const labelClass = "text-[10px] font-medium uppercase tracking-[0.14em] text-slate-500";
const valueClass = "text-xs font-semibold text-slate-100";

function statusTone(value: string) {
  const lower = value.toLowerCase();
  if (lower.includes("ready") || lower.includes("online") || lower.includes("healthy")) {
    return { text: "text-emerald-300", dot: "bg-emerald-400" };
  }
  if (lower.includes("failed") || lower.includes("offline") || lower.includes("disconnected")) {
    return { text: "text-rose-300", dot: "bg-rose-400" };
  }
  return { text: "text-amber-300", dot: "bg-amber-400" };
}

export default function WorkspaceStatusStrip({
  data,
  loading,
}: {
  data: WorkspaceStatusStripData;
  loading?: boolean;
}) {
  const revisionTone = statusTone(data.revision);
  const embeddingTone = statusTone(data.embeddings);
  const agentTone = statusTone(data.agent);
  const vectorTone = statusTone(data.vectorDb);

  return (
    <div className="border-b border-white/10 bg-[#111827] px-4 py-2 md:px-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6 md:gap-4">
        <div>
          <div className={labelClass}>Project</div>
          <div className={`${valueClass} truncate`}>{data.project}</div>
        </div>
        <div>
          <div className={labelClass}>Revision</div>
          <div className={`inline-flex items-center gap-1.5 ${valueClass} ${revisionTone.text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${revisionTone.dot}`} />
            {data.revision}
          </div>
        </div>
        <div>
          <div className={labelClass}>Embeddings</div>
          <div className={`inline-flex items-center gap-1.5 ${valueClass} ${embeddingTone.text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${embeddingTone.dot}`} />
            {data.embeddings}
          </div>
        </div>
        <div>
          <div className={labelClass}>Agent</div>
          <div className={`inline-flex items-center gap-1.5 ${valueClass} ${agentTone.text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${agentTone.dot}`} />
            {data.agent}
          </div>
        </div>
        <div>
          <div className={labelClass}>Jobs</div>
          <div className={valueClass}>{data.activeJobs}</div>
        </div>
        <div>
          <div className={labelClass}>DB</div>
          <div className={`inline-flex items-center gap-1.5 ${valueClass} ${vectorTone.text}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${vectorTone.dot}`} />
            {data.vectorDb}
          </div>
        </div>
      </div>
      {loading && (
        <div className="mt-1 text-[11px] text-slate-500">Syncing...</div>
      )}
    </div>
  );
}
