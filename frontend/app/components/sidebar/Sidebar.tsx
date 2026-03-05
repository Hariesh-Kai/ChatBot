"use client";

import { useMemo, useRef, useState, useEffect } from "react";
import Brand from "./Brand";
import ChatList from "./ChatList";
import PdfUploadButton from "@/app/components/upload/PdfUploadButton";
import { ChatSession } from "@/app/lib/types";
import {
  BellRing,
  Bot,
  Code2,
  FileText,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightOpen,
  Plus,
  Search,
} from "lucide-react";
import { UploadStatus } from "@/app/hooks/useSmartUpload";
import type { AuthUser } from "@/app/lib/api";
import type { TeamProject, WorkspaceMode } from "@/app/lib/enterprise-messaging";
import WorkspaceProjectTree from "@/app/components/workspace/WorkspaceProjectTree";
import type { WorkspaceProjectSummary } from "@/app/components/workspace/types";

interface SidebarProps {
  chats: ChatSession[];
  activeId: string | null;
  sessionId: string | null;
  user: AuthUser | null;
  workspaceMode: WorkspaceMode;
  teamUnreadTotal?: number;
  unreadCounts: Record<string, number>;
  totalUnread: number;
  onSignOut: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onPin: (id: string) => void;
  isOpen: boolean;
  onOpen: () => void;
  onClose: () => void;
  isTyping: boolean;
  
  onUploadStart: (file: File) => void;
  onUploadSuccess: (result: any) => void;
  onUploadError: (error: string) => void;
  //  NEW
  onUploadProgress: (status: UploadStatus, percent: number, label: string) => void;
  showTeamMode?: boolean;
  showUpload?: boolean;
  showPmlEntry?: boolean;
  workspaceProjects?: WorkspaceProjectSummary[];
  activeProjectId?: string | null;
  activeRevision?: string | null;
  onProjectRevisionSelect?: (payload: { projectId: string; revision: string }) => void;
  onProjectSetupClick?: () => void;
  projectSetupActive?: boolean;
  unassignedProjectCount?: number;
  teamProjects?: TeamProject[];
  activeTeamProjectId?: string | null;
  onSelectTeamProject?: (projectId: string) => void;
  onTeamAiClick?: () => void;
  teamAiAssistActive?: boolean;
  pmlCenterTab?: "editor" | "output";
  onPmlCenterTabChange?: (tab: "editor" | "output") => void;
}

export default function Sidebar({
  chats,
  activeId,
  sessionId,
  user,
  workspaceMode,
  teamUnreadTotal = 0,
  unreadCounts,
  totalUnread,
  onSignOut,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onPin,
  isOpen, onOpen, onClose, isTyping,
  onUploadStart, onUploadSuccess, onUploadError, onUploadProgress, //  Destructure
  showTeamMode = true,
  showUpload = true,
  showPmlEntry = true,
  workspaceProjects = [],
  activeProjectId = null,
  activeRevision = null,
  onProjectRevisionSelect,
  onProjectSetupClick,
  projectSetupActive = false,
  unassignedProjectCount = 0,
  teamProjects = [],
  activeTeamProjectId = null,
  onSelectTeamProject,
  onTeamAiClick,
  teamAiAssistActive = false,
  pmlCenterTab = "editor",
  onPmlCenterTabChange,
}: SidebarProps) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement | null>(null);
  const resolvedWorkspaceMode: WorkspaceMode =
    workspaceMode === "team" && !showTeamMode
      ? "ai"
      : workspaceMode === "pml" && !showPmlEntry
        ? "ai"
        : workspaceMode;
  const isTeamMode = resolvedWorkspaceMode === "team";
  const isPmlMode = resolvedWorkspaceMode === "pml";
  const isAiMode = resolvedWorkspaceMode === "ai";
  const isPmlEditor = pmlCenterTab === "editor";
  const isPmlOutput = pmlCenterTab === "output";
  const isChatMode = !isTeamMode;
  const uploadActive =
    typeof window !== "undefined" && (window as any).__KAVIN_UPLOAD_ACTIVE__;
  const interactionBlocked = isChatMode && (isTyping || (isAiMode && uploadActive));
  const uploadDisabled = !isAiMode || !sessionId || isTyping || uploadActive;
  const combinedUnread = totalUnread + teamUnreadTotal;

  const userInitial =
    (user?.username || user?.email || "U").trim().charAt(0).toUpperCase() || "U";


  const filteredChats = useMemo(() => {
    const visible = chats;
    if (!query.trim()) return visible;
    const q = query.toLowerCase();
    return visible.filter((c) => (c.title || "").toLowerCase().includes(q) || c.messages.some((m) => m.role !== "system" && (m.content || "").toLowerCase().includes(q)));
  }, [query, chats]);

  const filteredTeamProjects = useMemo(() => {
    const sortedProjects = [...teamProjects].sort((a, b) => b.createdAt - a.createdAt);
    if (!query.trim()) return sortedProjects;
    const q = query.toLowerCase();
    return sortedProjects.filter((project) => {
      const text = `${project.code} ${project.name} ${project.description || ""}`.toLowerCase();
      return text.includes(q);
    });
  }, [query, teamProjects]);

  useEffect(() => {
    if (isOpen && (isChatMode || isTeamMode) && searchRef.current) {
      searchRef.current.focus();
    }
  }, [isChatMode, isOpen, isTeamMode]);

  useEffect(() => {
    setQuery("");
  }, [resolvedWorkspaceMode]);

  return (
    <>
      {isOpen &&  <div
      onClick={() =>
        !interactionBlocked &&
        onClose()
      }
            className="fixed inset-0 z-30 bg-black/60 md:hidden"
    />}
      <aside
        className={`app-sidebar-shell fixed left-0 top-0 z-40 h-[100dvh] overflow-hidden border-r border-white/10 bg-black transition-all duration-300 ease-in-out ${
          isOpen
            ? "app-sidebar-open-state w-[78vw] max-w-[18.5rem] sm:w-72"
            : "app-sidebar-closed-state w-10 min-[361px]:w-11 sm:w-14"
        }`}
      >
        {!isOpen && (
          <div className="app-sidebar-collapsed flex h-full flex-col items-center overflow-y-auto">
            <div className="app-sidebar-collapsed-header flex h-12 w-full items-center justify-center border-b border-white/10 sm:h-14"><Brand iconOnly /></div>
            <div className="mt-3 flex flex-col gap-2.5 sm:mt-4 sm:gap-3">
              <button onClick={onOpen} disabled={interactionBlocked} className="relative rounded-md p-1.5 text-gray-400 hover:bg-white/10 hover:text-white disabled:opacity-50 sm:p-2">
                <PanelLeftOpen size={18} />
                {combinedUnread > 0 && (
                  <span className="absolute -right-1 -top-1 min-w-[16px] rounded-full bg-white px-1 text-center text-[10px] font-semibold text-black">
                    {combinedUnread > 9 ? "9+" : combinedUnread}
                  </span>
                )}
              </button>
              {isTeamMode && (
                <button
                  type="button"
                  onClick={onProjectSetupClick}
                  className={`relative rounded-md bg-white p-1.5 text-black transition hover:bg-gray-200 sm:p-2 ${
                    projectSetupActive ? "ring-1 ring-black/20" : ""
                  }`}
                  title="Project Setup & Assignment"
                  aria-label="Project Setup & Assignment"
                >
                  <FileText size={18} />
                  {unassignedProjectCount > 0 && (
                    <span className="absolute -right-1 -top-1 min-w-[16px] rounded-full bg-black px-1 text-center text-[10px] font-semibold text-white">
                      {unassignedProjectCount > 9 ? "9+" : unassignedProjectCount}
                    </span>
                  )}
                </button>
              )}
              {isTeamMode && (
                <button
                  type="button"
                  onClick={onTeamAiClick}
                  className={`rounded-md p-1.5 transition sm:p-2 ${
                    teamAiAssistActive
                      ? "bg-white text-black"
                      : "bg-white text-black hover:bg-gray-200"
                  }`}
                  title="Team AI Assist"
                  aria-label="Team AI Assist"
                >
                  <Bot size={18} />
                </button>
              )}
              {isChatMode && (
                <>
                  <button onClick={onNew} disabled={isTyping} className="rounded-md p-1.5 text-gray-400 hover:bg-white/10 hover:text-white disabled:opacity-50 sm:p-2"><Plus size={18} /></button>
                  {showUpload && isAiMode && (
                    <PdfUploadButton
                      sessionId={sessionId}
                      iconOnly
                      disabled={uploadDisabled || !sessionId}
                      dataId="sidebar"
                      onUploadStart={onUploadStart}
                      onUploadSuccess={onUploadSuccess}
                      onUploadError={onUploadError}
                      onUploadProgress={onUploadProgress}
                    />
                  )}
                  <button onClick={onOpen} disabled={isTyping} className="rounded-md p-1.5 text-gray-400 hover:bg-white/10 hover:text-white disabled:opacity-50 sm:p-2"><Search size={18} /></button>
                </>
              )}
              {isPmlMode && (
                <>
                  <button
                    type="button"
                    onClick={() => onPmlCenterTabChange?.("editor")}
                    className={`rounded-md p-1.5 transition sm:p-2 ${
                      isPmlEditor ? "bg-white text-black" : "text-gray-400 hover:bg-white/10 hover:text-white"
                    }`}
                    title="PML Code Writer"
                  >
                    <Code2 size={16} />
                  </button>
                  <button
                    type="button"
                    onClick={() => onPmlCenterTabChange?.("output")}
                    className={`rounded-md p-1.5 transition sm:p-2 ${
                      isPmlOutput ? "bg-white text-black" : "text-gray-400 hover:bg-white/10 hover:text-white"
                    }`}
                    title="PML Output Panel"
                  >
                    <PanelRightOpen size={16} />
                  </button>
                </>
              )}
            </div>
          </div>
        )}

        {isOpen && (
          <div className="app-sidebar-open flex h-full min-h-0 flex-col overflow-hidden">
            <div className="app-sidebar-open-header flex h-12 items-center justify-between border-b border-white/10 px-3 sm:h-14 sm:px-4">
              <Brand iconOnly />
              <div className="flex items-center gap-1">
                <button onClick={onClose} disabled={interactionBlocked} className="rounded-md p-1 text-gray-400 hover:bg-white/10 hover:text-white disabled:opacity-50"><PanelLeftClose size={16} /></button>
              </div>
            </div>
            <div className="app-sidebar-controls space-y-3 px-2.5 py-3 sm:px-4 sm:py-4">
              {isTeamMode && (
                <button
                  type="button"
                  onClick={onProjectSetupClick}
                  className={`flex w-full items-center gap-2 rounded-md bg-white px-3 py-2 text-sm font-medium text-black transition hover:bg-gray-200 ${
                    projectSetupActive ? "ring-1 ring-black/20" : ""
                  }`}
                >
                  <FileText size={14} />
                  Project Setup & Assignment
                  {unassignedProjectCount > 0 && (
                    <span className="ml-auto inline-flex min-w-[18px] items-center justify-center rounded-full bg-black px-1.5 py-0.5 text-[10px] font-semibold text-white">
                      {unassignedProjectCount > 9 ? "9+" : unassignedProjectCount}
                    </span>
                  )}
                </button>
              )}
              {isTeamMode && (
                <button
                  type="button"
                  onClick={onTeamAiClick}
                  className={`flex w-full items-center gap-2 rounded-md border border-white/20 bg-white/5 px-3 py-2 text-sm font-medium text-gray-100 transition hover:bg-white/10 ${
                    teamAiAssistActive ? "ring-1 ring-white/30" : ""
                  }`}
                >
                  <Bot size={14} />
                  Team AI Assist
                </button>
              )}
              {isChatMode && (
                <>
                  <button onClick={onNew} disabled={isTyping} className="w-full rounded-md bg-white px-3 py-2 text-xs font-medium text-black hover:bg-gray-200 disabled:opacity-50 sm:text-sm">+ New Chat</button>
                  {showUpload && isAiMode && (
                    <PdfUploadButton
                      sessionId={sessionId}
                      disabled={uploadDisabled || !sessionId}
                      dataId="sidebar"
                      onUploadStart={onUploadStart}
                      onUploadSuccess={onUploadSuccess}
                      onUploadError={onUploadError}
                      onUploadProgress={onUploadProgress}
                    />
                  )}
                  <input ref={searchRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats" disabled={isTyping} className="w-full rounded-md border border-white/25 bg-transparent px-3 py-2 text-xs text-white outline-none disabled:opacity-50 sm:text-sm" />
                </>
              )}
              {isTeamMode && (
                <input
                  ref={searchRef}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search projects"
                  className="w-full rounded-md border border-white/25 bg-transparent px-3 py-2 text-xs text-white outline-none sm:text-sm"
                />
              )}

              {isPmlMode && (
                <div className="rounded-lg border border-white/10 bg-white/5 p-2">
                  <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                    PML Views
                  </div>
                  <div className="space-y-1">
                    <button
                      type="button"
                      onClick={() => onPmlCenterTabChange?.("editor")}
                      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-xs font-medium transition ${
                        isPmlEditor
                          ? "bg-white text-black"
                          : "text-gray-200 hover:bg-white/10 hover:text-white"
                      }`}
                    >
                      <Code2 size={14} />
                      Code Writer
                    </button>
                    <button
                      type="button"
                      onClick={() => onPmlCenterTabChange?.("output")}
                      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-xs font-medium transition ${
                        isPmlOutput
                          ? "bg-white text-black"
                          : "text-gray-200 hover:bg-white/10 hover:text-white"
                      }`}
                    >
                      <PanelRightOpen size={14} />
                      Output Panel
                    </button>
                  </div>
                </div>
              )}
            </div>
            {isChatMode && (
              <div className="flex select-none items-center justify-between px-2.5 pb-2.5 pt-3 text-xs text-gray-400 sm:px-4 sm:pb-3">
                <span>Chats</span>
                {totalUnread > 0 && (
                  <span className="rounded-full border border-white/30 bg-white/10 px-2 py-0.5 text-[10px] font-semibold text-white">
                    {totalUnread} new
                  </span>
                )}
              </div>
            )}
            {isTeamMode && (
              <div className="flex select-none items-center justify-between px-2.5 pb-2.5 pt-3 text-xs text-gray-400 sm:px-4 sm:pb-3">
                <span>Projects</span>
                <span className="rounded-full border border-white/30 bg-white/10 px-2 py-0.5 text-[10px] font-semibold text-white">
                  {filteredTeamProjects.length}
                </span>
              </div>
            )}
            <div className="app-sidebar-list min-h-0 flex-1 overflow-y-auto px-2.5 pb-3 sm:px-3 sm:pb-4">
              {isChatMode ? (
                <div className="space-y-3">
                  {isAiMode && workspaceProjects.length > 0 && onProjectRevisionSelect && (
                    <WorkspaceProjectTree
                      projects={workspaceProjects}
                      activeProjectId={activeProjectId}
                      activeRevision={activeRevision}
                      onSelect={onProjectRevisionSelect}
                    />
                  )}
                  <ChatList
                    chats={filteredChats}
                    activeId={activeId}
                    unreadCounts={unreadCounts}
                    disabled={isTyping}
                    onSelect={(id) => {
                      if (isTyping) return;
                      onSelect(id);
                      if (typeof window !== "undefined" && window.innerWidth < 768) {
                        onClose();
                      }
                    }}
                    onRename={(id) => {
                      const title = prompt("Rename chat");
                      if (!title) return;
                      const clean = title.trim();
                      if (clean) onRename(id, clean);
                    }}
                    onDelete={onDelete}
                    onPin={onPin}
                  />
                </div>
              ) : (
                <div className="space-y-2">
                  {filteredTeamProjects.map((project) => {
                    const isActive = activeTeamProjectId === project.id;
                    const assigneeCount = Array.isArray(project.assigneeIds) ? project.assigneeIds.length : 0;
                    return (
                      <button
                        key={project.id}
                        type="button"
                        onClick={() => {
                          onSelectTeamProject?.(project.id);
                          if (typeof window !== "undefined" && window.innerWidth < 768) {
                            onClose();
                          }
                        }}
                        className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                          isActive
                            ? "border-white/30 bg-white/10"
                            : "border-white/10 bg-white/5 hover:bg-white/10"
                        }`}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                            {project.code}
                          </span>
                          <span className="text-[10px] text-gray-500">{project.status.toUpperCase()}</span>
                        </div>
                        <div className="truncate text-xs font-medium text-gray-100 sm:text-sm">{project.name}</div>
                        <div className="mt-1 text-[11px] text-gray-500">
                          {assigneeCount > 0
                            ? `${assigneeCount} assignee${assigneeCount > 1 ? "s" : ""}`
                            : "Unassigned"}
                        </div>
                      </button>
                    );
                  })}
                  {filteredTeamProjects.length === 0 && (
                    <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-3 text-xs text-gray-400">
                      No projects available.
                    </div>
                  )}
                </div>
              )}

            </div>
            <div className="app-sidebar-footer border-t border-white/10 px-2.5 py-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] sm:px-4 sm:pb-3">
              <div className="flex items-center gap-2.5 sm:gap-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/10 text-xs font-semibold text-white sm:h-9 sm:w-9 sm:text-sm">
                  {userInitial}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs text-gray-200 sm:text-sm">
                    {user?.username ?? "User"}
                  </div>
                  <div className="sidebar-user-email max-[430px]:hidden truncate text-[11px] text-gray-500">
                    {user?.email ?? ""}
                  </div>
                </div>
              </div>

              <div className="mt-2.5 flex flex-col gap-2">
                <div className="sidebar-alert-line inline-flex max-w-full items-center gap-1 truncate text-[11px] text-gray-400">
                  <BellRing size={12} />
                  {isTeamMode
                    ? teamUnreadTotal > 0
                      ? `${teamUnreadTotal} unread team messages`
                      : "No new team chat alerts"
                    : isPmlMode
                      ? "PML code writer mode"
                      : totalUnread > 0
                        ? `${totalUnread} unread AI chats`
                        : "No new AI chat alerts"}
                </div>
                <div className="flex items-center justify-between gap-2">
                  <a
                    href="/dashboard"
                    className="sidebar-dashboard-link truncate text-[11px] text-gray-400 hover:text-white hover:underline max-[360px]:hidden"
                  >
                    Developer dashboard
                  </a>
                  <button
                    type="button"
                    onClick={onSignOut}
                    disabled={interactionBlocked}
                    className="ml-auto shrink-0 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-[11px] text-gray-200 hover:bg-white/10 disabled:opacity-50"
                  >
                    Sign out
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
