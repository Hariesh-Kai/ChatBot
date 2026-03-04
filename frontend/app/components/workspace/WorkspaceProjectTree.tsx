"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FolderKanban, FileText } from "lucide-react";
import type { WorkspaceProjectSummary } from "@/app/components/workspace/types";

export default function WorkspaceProjectTree({
  projects,
  activeProjectId,
  activeRevision,
  onSelect,
}: {
  projects: WorkspaceProjectSummary[];
  activeProjectId: string | null;
  activeRevision: string | null;
  onSelect: (payload: { projectId: string; revision: string }) => void;
}) {
  const [open, setOpen] = useState(true);
  const [expandedProjects, setExpandedProjects] = useState<Record<string, boolean>>({});

  const sorted = useMemo(
    () => [...projects].sort((a, b) => b.lastUpdated - a.lastUpdated),
    [projects]
  );

  if (projects.length === 0) {
    return (
      <div className="rounded-[12px] border border-white/10 bg-black/30 p-3">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-500">
          Projects
        </div>
        <div className="mt-2 text-xs text-gray-500">No projects discovered from indexed revisions.</div>
      </div>
    );
  }

  return (
    <section className="rounded-[12px] border border-white/10 bg-black/30">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between px-3 py-2.5 text-left"
      >
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-gray-400">
          Projects
        </span>
        {open ? <ChevronDown size={14} className="text-gray-500" /> : <ChevronRight size={14} className="text-gray-500" />}
      </button>

      {open && (
        <div className="space-y-1 border-t border-white/10 px-2 pb-2 pt-2">
          {sorted.map((project) => {
            const expanded = expandedProjects[project.id] ?? true;
            const isProjectActive = activeProjectId === project.id;
            return (
              <div key={project.id} className="rounded-lg bg-white/[0.02]">
                <button
                  type="button"
                  onClick={() =>
                    setExpandedProjects((prev) => ({
                      ...prev,
                      [project.id]: !expanded,
                    }))
                  }
                  className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left text-xs ${
                    isProjectActive
                      ? "bg-cyan-500/15 text-cyan-100"
                      : "text-gray-300 hover:bg-white/5"
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-1.5">
                    {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    <FolderKanban size={12} className="text-cyan-300" />
                    <span className="truncate">{project.name}</span>
                  </span>
                  <span className="text-[10px] text-gray-500">{project.totalDocuments}</span>
                </button>

                {expanded && (
                  <div className="space-y-1 pb-2 pl-7 pr-1">
                    {project.documents.map((doc) => {
                      const isActive =
                        project.id === activeProjectId && doc.revision === activeRevision;
                      return (
                        <button
                          key={doc.id}
                          type="button"
                          onClick={() =>
                            onSelect({
                              projectId: project.id,
                              revision: doc.revision,
                            })
                          }
                          className={`flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-[11px] ${
                            isActive
                              ? "border border-cyan-300/35 bg-cyan-500/15 text-cyan-100"
                              : "text-gray-400 hover:bg-white/5 hover:text-gray-200"
                          }`}
                        >
                          <span className="flex min-w-0 items-center gap-1.5">
                            <FileText size={11} className="text-gray-500" />
                            <span className="truncate">{doc.name}</span>
                          </span>
                          <span className="text-[10px]">{doc.revision}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
