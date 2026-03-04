"use client";

import WorkspaceCard from "@/app/components/workspace/WorkspaceCard";
import StatusDot from "@/app/components/workspace/StatusDot";
import type {
  WorkspaceDocumentRow,
  WorkspaceJobSummary,
  WorkspaceProjectSummary,
  WorkspaceSystemHealth,
} from "@/app/components/workspace/types";

function formatTime(ts: number) {
  if (!ts) return "-";
  return new Date(ts).toLocaleString([], {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusPill(status: WorkspaceDocumentRow["status"]) {
  if (status === "Indexed") {
    return "border-emerald-300/20 bg-emerald-400/10 text-emerald-300";
  }
  if (status === "Processing") {
    return "border-amber-300/25 bg-amber-400/10 text-amber-300";
  }
  return "border-rose-300/25 bg-rose-400/10 text-rose-300";
}

export default function WorkspaceOverviewDashboard({
  activeProject,
  documents,
  jobs,
  system,
  onUploadPdf,
  onStartNewChat,
  onReindexDocument,
  onCreateRevision,
  onOpenPmlWorkspace,
  onRetryFailedJob,
}: {
  activeProject: WorkspaceProjectSummary | null;
  documents: WorkspaceDocumentRow[];
  jobs: WorkspaceJobSummary;
  system: WorkspaceSystemHealth;
  onUploadPdf: () => void;
  onStartNewChat: () => void;
  onReindexDocument: () => void;
  onCreateRevision: () => void;
  onOpenPmlWorkspace: () => void;
  onRetryFailedJob?: (jobId: string) => void;
}) {
  const failedJobs = Object.entries(jobs.statuses).filter(
    ([, status]) => String(status || "").toUpperCase() === "ERROR"
  );
  const sortedDocs = [...documents].sort((a, b) => b.lastUpdated - a.lastUpdated).slice(0, 12);

  return (
    <div className="h-full overflow-y-auto bg-[#0f172a] px-5 py-6 md:px-7">
      <div className="mx-auto grid w-full max-w-7xl gap-5 lg:grid-cols-12">
        <WorkspaceCard
          title="Active Project"
          subtitle="Current workspace context"
          className="lg:col-span-6"
        >
          <div className="rounded-[12px] border border-blue-400/20 bg-blue-500/8 p-4">
            <div className="text-xl font-semibold tracking-tight text-white">
              {activeProject?.name || "No active project selected"}
            </div>
            <p className="mt-1 text-sm text-slate-300">
              {activeProject?.description || "Use the project tree to select a project revision."}
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-[12px] border border-white/10 bg-[#111827] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Last Updated</div>
                <div className="mt-1 font-medium text-slate-100">
                  {activeProject ? formatTime(activeProject.lastUpdated) : "-"}
                </div>
              </div>
              <div className="rounded-[12px] border border-white/10 bg-[#111827] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Current Revision</div>
                <div className="mt-1 font-medium text-blue-300">
                  {activeProject?.currentRevision || "R-"}
                </div>
              </div>
              <div className="rounded-[12px] border border-white/10 bg-[#111827] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Documents</div>
                <div className="mt-1 font-medium text-slate-100">
                  {activeProject?.totalDocuments ?? 0}
                </div>
              </div>
              <div className="rounded-[12px] border border-white/10 bg-[#111827] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Workspace</div>
                <div className="mt-1 font-medium text-blue-300">Operational</div>
              </div>
            </div>
          </div>
        </WorkspaceCard>

        <WorkspaceCard title="System Health" subtitle="Live service checks" className="lg:col-span-3">
          <div className="space-y-3 text-sm">
            <div className="flex items-center justify-between text-slate-200">
              <span>Ingestion Agent</span>
              <span className="inline-flex items-center gap-2">
                <StatusDot state={system.ingestionAgent} />
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-200">
              <span>Vector DB</span>
              <span className="inline-flex items-center gap-2">
                <StatusDot state={system.vectorDb} />
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-200">
              <span>Backend API</span>
              <span className="inline-flex items-center gap-2">
                <StatusDot state={system.backendApi} />
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-200">
              <span>Model Loaded</span>
              <span className="inline-flex items-center gap-2">
                <StatusDot state={system.modelLoaded} />
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-200">
              <span>Embedding Model</span>
              <span className="inline-flex items-center gap-2">
                <StatusDot state={system.embeddingModelLoaded} />
              </span>
            </div>
          </div>
        </WorkspaceCard>

        <WorkspaceCard title="Quick Actions" subtitle="Common operations" className="lg:col-span-3">
          <div className="grid grid-cols-1 gap-2.5 text-sm">
            <button
              type="button"
              onClick={onUploadPdf}
              className="rounded-[12px] border border-blue-300/40 bg-[#3b82f6] px-3 py-2 text-left font-medium text-white transition-colors hover:bg-[#2563eb]"
            >
              Upload PDF
            </button>
            <button
              type="button"
              onClick={onStartNewChat}
              className="rounded-[12px] border border-white/15 bg-[#111827] px-3 py-2 text-left text-slate-200 transition-colors hover:border-blue-300/40 hover:text-blue-200"
            >
              Start New Chat
            </button>
            <button
              type="button"
              onClick={onReindexDocument}
              className="rounded-[12px] border border-white/15 bg-[#111827] px-3 py-2 text-left text-slate-200 transition-colors hover:border-blue-300/40 hover:text-blue-200"
            >
              Re-index Document
            </button>
            <button
              type="button"
              onClick={onCreateRevision}
              className="rounded-[12px] border border-white/15 bg-[#111827] px-3 py-2 text-left text-slate-200 transition-colors hover:border-blue-300/40 hover:text-blue-200"
            >
              Create New Revision
            </button>
            <button
              type="button"
              onClick={onOpenPmlWorkspace}
              className="rounded-[12px] border border-white/15 bg-[#111827] px-3 py-2 text-left text-slate-200 transition-colors hover:border-blue-300/40 hover:text-blue-200"
            >
              Open PML Workspace
            </button>
          </div>
        </WorkspaceCard>

        <WorkspaceCard title="Documents" subtitle="Document and revision index" className="lg:col-span-12">
          <div className="overflow-hidden rounded-[12px] border border-white/10 bg-[#111827]">
            <div className="grid grid-cols-[2fr_100px_90px_130px_160px] bg-white/5 px-4 py-2.5 text-[11px] uppercase tracking-wide text-slate-400">
              <span>Document</span>
              <span>Revision</span>
              <span>Chunks</span>
              <span>Status</span>
              <span>Updated</span>
            </div>
            <div className="divide-y divide-white/5">
              {sortedDocs.length === 0 && (
                <div className="px-4 py-8 text-center text-sm text-slate-500">
                  No indexed documents found yet.
                </div>
              )}
              {sortedDocs.map((doc) => (
                <div
                  key={doc.id}
                  className="grid grid-cols-[2fr_100px_90px_130px_160px] items-center px-4 py-3 text-sm text-slate-200 transition-colors hover:bg-white/[0.03]"
                >
                  <span className="truncate">{doc.name}</span>
                  <span className="text-blue-300">{doc.revision}</span>
                  <span>{doc.chunks}</span>
                  <span>
                    <span
                      className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs font-medium ${statusPill(
                        doc.status
                      )}`}
                    >
                      {doc.status}
                    </span>
                  </span>
                  <span className="text-slate-400">{formatTime(doc.lastUpdated)}</span>
                </div>
              ))}
            </div>
          </div>
        </WorkspaceCard>

        <WorkspaceCard title="Processing Jobs" subtitle="Background ingestion pipeline" className="lg:col-span-12">
          <div className="grid gap-4 md:grid-cols-[240px_1fr]">
            <div className="grid grid-cols-3 gap-2 md:grid-cols-1">
              <div className="rounded-[12px] border border-white/10 bg-[#111827] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Running</div>
                <div className="mt-1 text-lg font-semibold text-blue-300">{jobs.running}</div>
              </div>
              <div className="rounded-[12px] border border-white/10 bg-[#111827] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Completed</div>
                <div className="mt-1 text-lg font-semibold text-emerald-300">{jobs.completed}</div>
              </div>
              <div className="rounded-[12px] border border-white/10 bg-[#111827] px-3 py-3">
                <div className="text-[11px] uppercase tracking-wide text-slate-500">Failed</div>
                <div className="mt-1 text-lg font-semibold text-rose-300">{jobs.failed}</div>
              </div>
            </div>

            <div className="space-y-2">
              <div className="max-h-52 space-y-1 overflow-y-auto rounded-[12px] border border-white/10 bg-[#111827] p-2">
                {Object.keys(jobs.statuses).length === 0 && (
                  <div className="px-2 py-3 text-sm text-slate-500">No active ingestion jobs.</div>
                )}
                {Object.entries(jobs.statuses).map(([jobId, status]) => (
                  <div
                    key={jobId}
                    className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-xs hover:bg-white/5"
                  >
                    <span className="truncate text-slate-400">{jobId}</span>
                    <span className="font-medium text-slate-200">{String(status || "").toUpperCase()}</span>
                  </div>
                ))}
              </div>

              {failedJobs.length > 0 && onRetryFailedJob && (
                <button
                  type="button"
                  onClick={() => onRetryFailedJob(failedJobs[0][0])}
                  className="rounded-[12px] border border-blue-300/35 bg-blue-500/12 px-4 py-2 text-sm font-medium text-blue-200 transition-colors hover:bg-blue-500/20"
                >
                  Retry Failed Job
                </button>
              )}
            </div>
          </div>
        </WorkspaceCard>
      </div>
    </div>
  );
}

