"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  FolderPlus,
  MessageSquare,
  SendHorizonal,
  Sparkles,
  X,
} from "lucide-react";
import type { AuthUser } from "@/app/lib/api";
import {
  getConversationUnreadCount,
  getMemberName,
  getTeamMemberId,
  isProjectSystemTeamMessage,
  type TeamWorkspaceState,
} from "@/app/lib/enterprise-messaging";
import { normalizeRoleId } from "@/app/lib/org-role-catalog";

interface EnterpriseMessagingWorkspaceProps {
  user: AuthUser;
  workspace: TeamWorkspaceState;
  activeProjectId?: string | null;
  panel?: "threads" | "chat" | "projects";
  onPanelChange?: (panel: "threads" | "chat" | "projects") => void;
  projectSetupRequestId?: number;
  aiAssistSeed?: string | null;
  onAiAssistSeedConsumed?: () => void;
  busy?: boolean;
  error?: string | null;
  onSelectConversation: (conversationId: string) => Promise<void> | void;
  onSendMessage: (payload: { conversationId: string; content: string }) => Promise<void> | void;
  onCreateProject: (payload: {
    conversationId: string;
    code: string;
    name: string;
    assigneeIds: string[];
  }) => Promise<void> | void;
  onUpdateProjectAssignees: (payload: {
    projectId: string;
    assigneeIds: string[];
  }) => Promise<void> | void;
}

interface ConversationNavItem {
  id: string;
  name: string;
  subtitle?: string;
  targetConversationId: string;
  isVirtual?: boolean;
}

interface ConversationNavSection {
  id: string;
  label: string;
  items: ConversationNavItem[];
}

function formatTime(timestamp: number) {
  const value = new Date(timestamp);
  return value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function initials(name: string) {
  const parts = name.split(" ").filter(Boolean);
  if (parts.length === 0) return "TM";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
}

function normalizeToken(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function getSubDepartment(title: string) {
  const value = title.trim().toLowerCase();
  if (!value) return "General";
  if (value.includes("lead")) return "Leadership";
  if (value.includes("architect")) return "Architecture";
  if (value.includes("engineer")) return "Engineering";
  if (value.includes("manager")) return "Management";
  if (value.includes("qa") || value.includes("quality")) return "Quality";
  if (value.includes("product")) return "Product";
  return "General";
}

export default function EnterpriseMessagingWorkspace({
  user,
  workspace,
  activeProjectId = null,
  panel = "chat",
  onPanelChange,
  projectSetupRequestId = 0,
  aiAssistSeed = null,
  onAiAssistSeedConsumed,
  busy = false,
  error = null,
  onSelectConversation,
  onSendMessage,
  onCreateProject,
  onUpdateProjectAssignees,
}: EnterpriseMessagingWorkspaceProps) {
  const currentMemberId = useMemo(() => getTeamMemberId(user), [user]);
  const normalizedRole = useMemo(() => normalizeRoleId(user.role), [user.role]);
  const canManageProjects = useMemo(
    () =>
      normalizedRole === "piping_admin" ||
      normalizedRole === "pipe_lead" ||
      normalizedRole === "pipe_stress_engineer",
    [normalizedRole]
  );
  const [chatSearch, setChatSearch] = useState("");
  const [draftMessage, setDraftMessage] = useState("");
  const [projectCode, setProjectCode] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectConversationId, setProjectConversationId] = useState<string>("");
  const [projectAssigneeIds, setProjectAssigneeIds] = useState<string[]>([]);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [sendBusy, setSendBusy] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [showMemberModal, setShowMemberModal] = useState(false);
  const [memberAssigneeIds, setMemberAssigneeIds] = useState<string[]>([]);
  const [memberModalBusy, setMemberModalBusy] = useState(false);
  const [memberModalError, setMemberModalError] = useState<string | null>(null);
  const [showAiHints, setShowAiHints] = useState(false);
  const [activeNavItemId, setActiveNavItemId] = useState<string | null>(null);
  const handledProjectSetupRequestRef = useRef(0);

  const sortedConversations = useMemo(
    () => [...workspace.conversations].sort((a, b) => b.updatedAt - a.updatedAt),
    [workspace.conversations]
  );
  const activeProject = useMemo(
    () =>
      workspace.projects.find((project) => project.id === activeProjectId) || null,
    [activeProjectId, workspace.projects]
  );

  const activeConversationId =
    workspace.activeConversationId || sortedConversations[0]?.id || null;
  const activeConversation =
    sortedConversations.find((conversation) => conversation.id === activeConversationId) ||
    sortedConversations[0] ||
    null;
  const defaultProjectConversationId =
    activeProject?.conversationId || activeConversation?.id || sortedConversations[0]?.id || "";
  const activeProjectMembers = useMemo(() => {
    if (!activeProject) return [];
    const ids = new Set<string>([activeProject.ownerId, ...activeProject.assigneeIds]);
    return workspace.members.filter((member) => ids.has(member.id));
  }, [activeProject, workspace.members]);
  const conversationSections = useMemo<ConversationNavSection[]>(() => {
    const q = chatSearch.trim().toLowerCase();
    const matches = (item: ConversationNavItem) => {
      if (!q) return true;
      const haystack = `${item.name} ${item.subtitle || ""}`.toLowerCase();
      return haystack.includes(q);
    };

    if (!activeProject) {
      const items = sortedConversations
        .map((conversation) => {
          const lastMessage = conversation.messages[conversation.messages.length - 1];
          const lastUserFacingMessage =
            [...conversation.messages]
              .reverse()
              .find((message) => !isProjectSystemTeamMessage(message)) || lastMessage;
          return {
            id: conversation.id,
            name: conversation.name,
            subtitle: lastUserFacingMessage?.content || "No messages yet",
            targetConversationId: conversation.id,
          } satisfies ConversationNavItem;
        })
        .filter(matches);

      return [{ id: "all-conversations", label: "Conversations", items }];
    }

    const targetConversationId =
      activeProject.conversationId || activeConversation?.id || sortedConversations[0]?.id || "";
    if (!targetConversationId) return [];

    const members =
      activeProjectMembers.length > 0
        ? activeProjectMembers
        : workspace.members.filter(
            (member) =>
              member.id === activeProject.ownerId ||
              activeProject.assigneeIds.includes(member.id)
          );
    if (members.length === 0) return [];

    const commonItems: ConversationNavItem[] = [
      {
        id: `project-${activeProject.id}-common`,
        name: "Common Group",
        subtitle: activeProject.name,
        targetConversationId,
        isVirtual: true,
      },
    ];

    const byDepartment = new Map<string, typeof members>();
    for (const member of members) {
      const key = member.department.trim() || "General";
      if (!byDepartment.has(key)) byDepartment.set(key, []);
      byDepartment.get(key)?.push(member);
    }
    const departmentItems = [...byDepartment.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([department, groupMembers]) => ({
        id: `project-${activeProject.id}-dept-${normalizeToken(department)}`,
        name: `${department} Group`,
        subtitle: `${groupMembers.length} member${groupMembers.length > 1 ? "s" : ""}`,
        targetConversationId,
        isVirtual: true,
      }));

    const bySubDepartment = new Map<string, typeof members>();
    for (const member of members) {
      const subDepartment = getSubDepartment(member.title);
      if (!bySubDepartment.has(subDepartment)) bySubDepartment.set(subDepartment, []);
      bySubDepartment.get(subDepartment)?.push(member);
    }
    const subDepartmentItems = [...bySubDepartment.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([subDepartment, groupMembers]) => ({
        id: `project-${activeProject.id}-sub-${normalizeToken(subDepartment)}`,
        name: `${subDepartment} Sub-Department`,
        subtitle: `${groupMembers.length} member${groupMembers.length > 1 ? "s" : ""}`,
        targetConversationId,
        isVirtual: true,
      }));

    const sameDepartmentItems: ConversationNavItem[] = [];
    const crossDepartmentItems: ConversationNavItem[] = [];
    for (let left = 0; left < members.length; left += 1) {
      for (let right = left + 1; right < members.length; right += 1) {
        const first = members[left];
        const second = members[right];
        const isSameDepartment = first.department === second.department;
        const item: ConversationNavItem = {
          id: `project-${activeProject.id}-pair-${normalizeToken(first.id)}-${normalizeToken(second.id)}`,
          name: `${first.name} <> ${second.name}`,
          subtitle: isSameDepartment
            ? `${first.department} | Same department`
            : `${first.department} <> ${second.department}`,
          targetConversationId,
          isVirtual: true,
        };
        if (isSameDepartment) {
          sameDepartmentItems.push(item);
        } else {
          crossDepartmentItems.push(item);
        }
      }
    }

    const sections: ConversationNavSection[] = [
      { id: "common", label: "Common Group", items: commonItems },
      { id: "department", label: "Department Groups", items: departmentItems },
      { id: "sub-department", label: "Sub-Department Groups", items: subDepartmentItems },
      { id: "one-to-one-same", label: "1:1 Same Department", items: sameDepartmentItems },
      { id: "one-to-one-cross", label: "1:1 Cross Department", items: crossDepartmentItems },
    ];

    return sections
      .map((section) => ({
        ...section,
        items: section.items.filter(matches),
      }))
      .filter((section) => section.items.length > 0);
  }, [
    activeConversation?.id,
    activeProject,
    activeProjectMembers,
    chatSearch,
    sortedConversations,
    workspace.members,
  ]);
  const allConversationNavItems = useMemo(
    () => conversationSections.flatMap((section) => section.items),
    [conversationSections]
  );
  useEffect(() => {
    if (allConversationNavItems.length === 0) {
      setActiveNavItemId(null);
      return;
    }
    setActiveNavItemId((prev) => {
      if (prev && allConversationNavItems.some((item) => item.id === prev)) return prev;
      if (activeProject) {
        const projectCommonId = `project-${activeProject.id}-common`;
        const matched = allConversationNavItems.find((item) => item.id === projectCommonId);
        return matched?.id || allConversationNavItems[0].id;
      }
      const matched = allConversationNavItems.find(
        (item) => item.targetConversationId === activeConversationId && !item.isVirtual
      );
      return matched?.id || allConversationNavItems[0].id;
    });
  }, [activeConversationId, activeProject, allConversationNavItems]);
  const activeNavItem = useMemo(
    () =>
      (activeNavItemId
        ? allConversationNavItems.find((item) => item.id === activeNavItemId)
        : null) ||
      allConversationNavItems[0] ||
      null,
    [activeNavItemId, allConversationNavItems]
  );
  const visibleConversationId =
    activeNavItem?.targetConversationId || activeConversation?.id || sortedConversations[0]?.id || null;
  const visibleConversation =
    sortedConversations.find((conversation) => conversation.id === visibleConversationId) ||
    sortedConversations[0] ||
    null;
  const visibleParticipantNames = (visibleConversation?.participantIds || [])
    .map((id) => getMemberName(workspace.members, id))
    .join(", ");
  const activeConversationTitle =
    activeProject?.name || activeNavItem?.name || visibleConversation?.name || "Team Workspace";
  const activeConversationSubtitle =
    activeProject && activeNavItem?.name
      ? `${activeNavItem.name}${visibleParticipantNames ? ` | ${visibleParticipantNames}` : ""}`
      : visibleParticipantNames;
  const canSendTeamMessage =
    Boolean(visibleConversation) && !sendBusy && draftMessage.trim().length > 0;

  const openProjectModal = useCallback(() => {
    if (!canManageProjects) {
      setActionError("Only admin, pipe lead, or stress engineer can create and assign projects.");
      return;
    }
    setProjectConversationId(defaultProjectConversationId);
    setProjectName("");
    setProjectAssigneeIds([]);
    if (!projectCode.trim()) {
      setProjectCode(`PRJ-${workspace.projectCounter}`);
    }
    setProjectError(null);
    setShowProjectModal(true);
  }, [canManageProjects, defaultProjectConversationId, projectCode, workspace.projectCounter]);

  useEffect(() => {
    if (!projectConversationId && defaultProjectConversationId) {
      setProjectConversationId(defaultProjectConversationId);
    }
  }, [defaultProjectConversationId, projectConversationId]);

  useEffect(() => {
    if (!aiAssistSeed) return;
    setDraftMessage(aiAssistSeed);
    setShowAiHints(true);
    onPanelChange?.("chat");
    onAiAssistSeedConsumed?.();
  }, [aiAssistSeed, onAiAssistSeedConsumed, onPanelChange]);

  useEffect(() => {
    if (!projectSetupRequestId) return;
    if (projectSetupRequestId === handledProjectSetupRequestRef.current) return;
    handledProjectSetupRequestRef.current = projectSetupRequestId;
    openProjectModal();
  }, [openProjectModal, projectSetupRequestId]);

  async function selectConversationItem(item: ConversationNavItem) {
    setActionError(null);
    setActiveNavItemId(item.id);
    try {
      await Promise.resolve(onSelectConversation(item.targetConversationId));
    } catch (err: any) {
      setActionError(err?.message || "Failed to open conversation.");
    }
  }

  async function sendMessage() {
    const text = draftMessage;
    if (!text.trim() || !visibleConversation) return;
    setActionError(null);
    setSendBusy(true);
    try {
      await Promise.resolve(
        onSendMessage({ conversationId: visibleConversation.id, content: text })
      );
      setDraftMessage("");
      setShowAiHints(false);
    } catch (err: any) {
      setActionError(err?.message || "Failed to send message.");
    } finally {
      setSendBusy(false);
    }
  }

  function toggleAssignee(memberId: string) {
    setProjectAssigneeIds((prev) =>
      prev.includes(memberId)
        ? prev.filter((item) => item !== memberId)
        : [...prev, memberId]
    );
  }

  function toggleMemberAssignee(memberId: string) {
    setMemberAssigneeIds((prev) =>
      prev.includes(memberId)
        ? prev.filter((item) => item !== memberId)
        : [...prev, memberId]
    );
  }

  function closeProjectModal() {
    setProjectError(null);
    setShowProjectModal(false);
  }

  function openMemberModal() {
    if (!canManageProjects) {
      setActionError("Only admin, pipe lead, or stress engineer can update project assignments.");
      return;
    }
    if (!activeProject) {
      setActionError("Select a project to manage assignment.");
      return;
    }
    setActionError(null);
    setMemberModalError(null);
    setMemberAssigneeIds([...(activeProject.assigneeIds || [])]);
    setShowMemberModal(true);
  }

  function closeMemberModal() {
    setMemberModalError(null);
    setShowMemberModal(false);
  }

  async function saveMemberAssignments() {
    if (!activeProject) {
      setMemberModalError("Select a project first.");
      return;
    }
    if (!canManageProjects) {
      setMemberModalError("You do not have permission to update project assignment.");
      return;
    }
    if (memberAssigneeIds.length === 0) {
      setMemberModalError("Assign at least one team member.");
      return;
    }

    setMemberModalError(null);
    setActionError(null);
    setMemberModalBusy(true);
    try {
      await Promise.resolve(
        onUpdateProjectAssignees({
          projectId: activeProject.id,
          assigneeIds: [...memberAssigneeIds],
        })
      );
      closeMemberModal();
    } catch (err: any) {
      setMemberModalError(err?.message || "Failed to update team assignment.");
    } finally {
      setMemberModalBusy(false);
    }
  }

  async function createProject() {
    if (!canManageProjects) {
      setProjectError("You do not have permission to create or assign projects.");
      return;
    }
    const cleanCode = projectCode.trim();
    if (!cleanCode) {
      setProjectError("Project ID is required.");
      return;
    }
    const cleanName = projectName.trim();
    if (!cleanName) {
      setProjectError("Project name is required.");
      return;
    }
    if (projectAssigneeIds.length === 0) {
      setProjectError("Assign at least one team member.");
      return;
    }

    const targetConversationId =
      projectConversationId || visibleConversation?.id || workspace.conversations[0]?.id || "";
    if (!targetConversationId) {
      setProjectError("No conversation selected.");
      return;
    }

    setProjectError(null);
    setActionError(null);
    setCreateBusy(true);
    try {
      await Promise.resolve(
        onCreateProject({
          conversationId: targetConversationId,
          code: cleanCode,
          name: cleanName,
          assigneeIds: [...projectAssigneeIds],
        })
      );
      setProjectCode("");
      setProjectName("");
      setProjectAssigneeIds([]);
      closeProjectModal();
      onPanelChange?.("chat");
    } catch (err: any) {
      setActionError(err?.message || "Failed to create project.");
    } finally {
      setCreateBusy(false);
    }
  }

  function applyAiHint(type: "trace" | "queue" | "handover") {
    if (type === "trace") {
      setDraftMessage("AI assist: trace the root cause in this thread and propose a fix path.");
      return;
    }
    if (type === "queue") {
      setDraftMessage("AI assist: queue next actions with owners and deadlines for this conversation.");
      return;
    }
    setDraftMessage("AI assist: prepare handover summary with blockers, decisions, and next milestones.");
  }

  return (
    <div className="team-workspace-shell flex h-full min-h-0 flex-col overflow-hidden bg-black text-[#e9edef]">
      <div className="team-workspace-top shrink-0 border-b border-white/10 bg-black px-3 py-2.5 sm:px-4 sm:py-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="ml-auto text-xs text-gray-400">
            {busy ? "Syncing team workspace..." : "Team messaging"}
          </div>
        </div>
        {(error || actionError) && (
          <div className="mt-2 rounded border border-red-400/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
            {error || actionError}
          </div>
        )}
      </div>

      <div className="team-workspace-grid min-h-0 flex-1 grid grid-cols-1 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside
          className={`team-workspace-nav h-full min-h-0 flex-col overflow-hidden border-r border-white/10 bg-black ${
            panel === "chat" ? "hidden lg:flex" : "flex"
          }`}
        >
          <div className="team-workspace-search border-b border-white/10 p-2.5 sm:p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                {activeProject ? "Project Groups" : "Conversations"}
              </span>
            </div>
            <input
              value={chatSearch}
              onChange={(event) => setChatSearch(event.target.value)}
              placeholder={activeProject ? "Search groups" : "Search conversations"}
              className="w-full rounded-lg border border-white/25 bg-black/30 px-3 py-2 text-xs text-white outline-none placeholder:text-gray-500 sm:text-sm"
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {conversationSections.map((section) => (
              <div key={section.id} className="border-b border-white/5">
                {activeProject ? (
                  <div className="px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-gray-500">
                    {section.label}
                  </div>
                ) : null}
                {section.items.map((item) => {
                  const targetConversation = sortedConversations.find(
                    (conversation) => conversation.id === item.targetConversationId
                  );
                  const lastMessage =
                    targetConversation?.messages[targetConversation.messages.length - 1];
                  const lastUserFacingMessage =
                    targetConversation &&
                    ([...targetConversation.messages]
                      .reverse()
                      .find((message) => !isProjectSystemTeamMessage(message)) || lastMessage);
                  const unread =
                    !item.isVirtual && targetConversation
                      ? getConversationUnreadCount(targetConversation, currentMemberId)
                      : 0;
                  const isActive = item.id === activeNavItem?.id;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => {
                        void selectConversationItem(item);
                        onPanelChange?.("chat");
                      }}
                      className={`w-full border-b border-white/5 px-2.5 py-2.5 text-left transition-colors sm:px-4 sm:py-3 ${
                        isActive ? "bg-white/10" : "hover:bg-white/5"
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        <div className="h-9 w-9 shrink-0 rounded-full border border-white/20 bg-white/10 text-center text-[11px] font-semibold leading-9 text-white sm:h-10 sm:w-10 sm:text-xs sm:leading-10">
                          {initials(item.name)}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs font-medium text-gray-100 sm:text-sm">
                              {item.name}
                            </span>
                            <span className="shrink-0 text-[11px] text-gray-500">
                              {lastMessage ? formatTime(lastMessage.createdAt) : ""}
                            </span>
                          </div>
                          <div className="mt-1 flex items-center justify-between gap-2">
                            <span className="truncate text-[11px] text-gray-400">
                              {item.subtitle || lastUserFacingMessage?.content || "No messages yet"}
                            </span>
                            {unread > 0 ? (
                              <span className="inline-flex min-w-[20px] items-center justify-center rounded-full bg-white px-1.5 py-0.5 text-[11px] font-semibold text-black">
                                {unread > 9 ? "9+" : unread}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            ))}
            {allConversationNavItems.length === 0 && (
              <div className="px-4 py-6 text-center text-sm text-gray-500">
                No conversation matches this search.
              </div>
            )}
          </div>
        </aside>

        <section
          className={`team-workspace-chat relative h-full min-h-0 flex-col overflow-hidden bg-black ${
            panel === "chat" ? "flex" : "hidden lg:flex"
          }`}
        >
          {visibleConversation ? (
            <>
              <div className="shrink-0 border-b border-white/10 bg-black/20 px-3 py-2.5 sm:px-5 sm:py-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-xs font-semibold text-gray-100 sm:text-sm">{activeConversationTitle}</div>
                    <div className="mt-1 text-[11px] text-gray-400 sm:text-xs">{activeConversationSubtitle}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    {activeProject && (
                      <button
                        type="button"
                        onClick={openMemberModal}
                        disabled={!canManageProjects}
                        className="inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/5 px-2 py-1 text-[10px] text-gray-200 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60 sm:text-[11px]"
                      >
                        Manage Members
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => onPanelChange?.("threads")}
                      className="inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/5 px-2 py-1 text-[10px] text-gray-200 transition hover:bg-white/10 sm:text-[11px] lg:hidden"
                    >
                      <MessageSquare size={12} />
                      <span className="sm:hidden">Chats</span>
                      <span className="hidden sm:inline">Conversations</span>
                    </button>
                  </div>
                </div>
              </div>

              <div className="team-chat-scroll relative min-h-0 flex-1 overflow-y-auto px-2.5 py-3 sm:px-4 sm:py-5">
                <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(255,255,255,0.05),transparent_45%),radial-gradient(circle_at_80%_80%,rgba(255,255,255,0.04),transparent_45%)]" />
                <div className="relative mx-auto max-w-3xl space-y-2">
                  {visibleConversation.messages.map((message) => {
                    const isMine = message.senderId === currentMemberId;
                    const senderName = getMemberName(workspace.members, message.senderId);
                    return (
                      <div
                        key={message.id}
                        className={`flex ${isMine ? "justify-end" : "justify-start"}`}
                      >
                        <div
                          className={`max-w-[90%] rounded-xl px-3 py-2 shadow-sm sm:max-w-[84%] ${
                            isMine
                              ? "rounded-br-sm border border-white/20 bg-[#1f1f1f] text-[#e9edef]"
                              : "rounded-bl-sm border border-white/10 bg-black/20 text-[#e9edef]"
                          }`}
                        >
                          {!isMine && (
                            <div className="mb-1 text-[11px] font-medium text-gray-300">{senderName}</div>
                          )}
                          <div className="whitespace-pre-wrap text-[13px] leading-relaxed sm:text-sm">{message.content}</div>
                          <div className="mt-1 text-right text-[10px] text-gray-300/80">
                            {formatTime(message.createdAt)}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="team-chat-input-area shrink-0 border-t border-white/10 bg-black/20 px-2.5 py-2.5 pb-[calc(0.625rem+env(safe-area-inset-bottom))] sm:px-4 sm:py-3">
                <div className="mx-auto max-w-3xl">
                  <div className="team-chat-hints mb-2 flex flex-wrap items-center gap-1.5 sm:gap-2">
                    <button
                      type="button"
                      onClick={() => setShowAiHints((prev) => !prev)}
                      className="inline-flex items-center gap-1 rounded-md border border-white/20 bg-white/5 px-2 py-1 text-[10px] text-gray-200 transition hover:bg-white/10 sm:text-[11px]"
                    >
                      <Sparkles size={12} />
                      AI Assist
                    </button>
                    {showAiHints && (
                      <>
                        <button
                          type="button"
                          onClick={() => applyAiHint("trace")}
                          className="rounded-md border border-white/15 bg-black/30 px-2 py-1 text-[10px] text-gray-200 hover:bg-white/10 sm:text-[11px]"
                        >
                          Trace
                        </button>
                        <button
                          type="button"
                          onClick={() => applyAiHint("queue")}
                          className="rounded-md border border-white/15 bg-black/30 px-2 py-1 text-[10px] text-gray-200 hover:bg-white/10 sm:text-[11px]"
                        >
                          Queue Plan
                        </button>
                        <button
                          type="button"
                          onClick={() => applyAiHint("handover")}
                          className="rounded-md border border-white/15 bg-black/30 px-2 py-1 text-[10px] text-gray-200 hover:bg-white/10 sm:text-[11px]"
                        >
                          Handover
                        </button>
                      </>
                    )}
                  </div>

                  <div className="flex items-end gap-2.5 rounded-2xl border border-white/25 bg-[#1a1a1a] px-3 py-2.5 shadow-md transition focus-within:ring-1 focus-within:ring-white/20 sm:gap-3 sm:rounded-xl sm:py-3">
                    <div className="mb-1 rounded-md border border-white/10 bg-white/5 p-1.5 text-gray-300 sm:p-2">
                      <Bot size={14} />
                    </div>
                    <textarea
                      value={draftMessage}
                      spellCheck={false}
                      onChange={(event) => setDraftMessage(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey) {
                          event.preventDefault();
                          if (canSendTeamMessage) void sendMessage();
                        }
                       }}
                       placeholder="Type a message to your team..."
                       className="max-h-40 min-h-[38px] flex-1 resize-y bg-transparent py-2 text-[13px] text-white outline-none placeholder:text-gray-500 sm:min-h-[40px] sm:text-sm"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        if (canSendTeamMessage) void sendMessage();
                      }}
                      disabled={!canSendTeamMessage}
                      className={`flex h-8 w-8 items-center justify-center rounded-xl transition sm:h-9 sm:w-9 sm:rounded-lg ${
                        canSendTeamMessage
                          ? "bg-white text-black hover:bg-gray-200"
                          : "cursor-not-allowed bg-white/10 text-gray-500"
                      }`}
                      aria-label="Send message"
                    >
                      <SendHorizonal size={16} />
                    </button>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-gray-500">
              No conversations available.
            </div>
          )}
        </section>
      </div>

      {showProjectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            className="absolute inset-0 bg-black/70"
            onClick={closeProjectModal}
            aria-label="Close project setup"
          />
          <div className="relative w-full max-w-xl rounded-xl border border-white/15 bg-[#0f0f0f] shadow-[0_16px_48px_rgba(0,0,0,0.5)]">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-white">
                <FolderPlus size={16} className="text-gray-200" />
                Project Setup and Assignment
              </div>
              <button
                type="button"
                onClick={closeProjectModal}
                className="rounded-md border border-white/15 bg-white/5 p-1.5 text-gray-300 transition hover:bg-white/10"
                aria-label="Close"
              >
                <X size={14} />
              </button>
            </div>

            <div className="space-y-3 p-4">
              <label className="block text-xs font-medium text-gray-300">
                Project ID
                <input
                  value={projectCode}
                  onChange={(event) => setProjectCode(event.target.value)}
                  placeholder="PRJ-1201"
                  className="mt-1 w-full rounded-lg border border-white/25 bg-black/30 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-500"
                />
              </label>
              <label className="block text-xs font-medium text-gray-300">
                Project Name
                <input
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  placeholder="Pipeline Compliance Revamp"
                  className="mt-1 w-full rounded-lg border border-white/25 bg-black/30 px-3 py-2 text-sm text-white outline-none placeholder:text-gray-500"
                />
              </label>
              <div className="rounded-lg border border-white/20 bg-black/30 px-3 py-2 text-xs text-gray-400">
                Conversation context: {visibleConversation?.name || "Auto-selected"}
              </div>

              {canManageProjects ? (
                <div className="rounded-lg border border-white/25 bg-black/30 p-2">
                  <div className="mb-2 text-[11px] font-medium text-gray-300">Team Assign</div>
                  <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                    {workspace.members.map((member) => {
                      const selected = projectAssigneeIds.includes(member.id);
                      return (
                        <button
                          key={member.id}
                          type="button"
                          onClick={() => toggleAssignee(member.id)}
                          className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[11px] transition ${
                            selected
                              ? "border-white bg-white text-black"
                              : "border-white/15 bg-black/20 text-gray-200 hover:bg-white/10"
                          }`}
                        >
                          <span
                            className={`h-1.5 w-1.5 rounded-full ${
                              selected ? "bg-black" : "bg-gray-500"
                            }`}
                          />
                          <span className="truncate">{member.name}</span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="mt-2 text-[11px] text-gray-500">
                    Members can be adjusted later from project assignment.
                  </div>
                </div>
              ) : (
                <div className="rounded-lg border border-white/20 bg-black/30 px-3 py-2 text-xs text-gray-400">
                  Project assignment is restricted to admin, pipe lead, and stress engineer roles.
                </div>
              )}

              {projectError && (
                <div className="rounded border border-red-400/30 bg-red-500/10 px-2 py-1 text-xs text-red-200">
                  {projectError}
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={closeProjectModal}
                  className="rounded-md border border-white/25 bg-black/30 px-3 py-1.5 text-xs font-medium text-white hover:bg-white/10"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void createProject();
                  }}
                  disabled={createBusy || !canManageProjects}
                  className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-black transition hover:bg-gray-200 disabled:opacity-60"
                >
                  {createBusy ? "Assigning..." : "Assign Project"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showMemberModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <button
            type="button"
            className="absolute inset-0 bg-black/70"
            onClick={closeMemberModal}
            aria-label="Close team assignment editor"
          />
          <div className="relative w-full max-w-xl rounded-xl border border-white/15 bg-[#0f0f0f] shadow-[0_16px_48px_rgba(0,0,0,0.5)]">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
              <div className="text-sm font-semibold text-white">Add Member Later - Team Assignment</div>
              <button
                type="button"
                onClick={closeMemberModal}
                className="rounded-md border border-white/15 bg-white/5 p-1.5 text-gray-300 transition hover:bg-white/10"
                aria-label="Close"
              >
                <X size={14} />
              </button>
            </div>

            <div className="space-y-3 p-4">
              <div className="rounded-lg border border-white/20 bg-black/30 px-3 py-2 text-xs text-gray-400">
                {activeProject
                  ? `Project: ${activeProject.code} | ${activeProject.name}`
                  : "Project not selected"}
              </div>
              <div className="rounded-lg border border-white/25 bg-black/30 p-2">
                <div className="mb-2 text-[11px] font-medium text-gray-300">Assigned Members</div>
                <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                  {workspace.members.map((member) => {
                    const selected = memberAssigneeIds.includes(member.id);
                    return (
                      <button
                        key={member.id}
                        type="button"
                        onClick={() => toggleMemberAssignee(member.id)}
                        className={`flex items-center gap-1.5 rounded-md border px-2 py-1.5 text-[11px] transition ${
                          selected
                            ? "border-white bg-white text-black"
                            : "border-white/15 bg-black/20 text-gray-200 hover:bg-white/10"
                        }`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            selected ? "bg-black" : "bg-gray-500"
                          }`}
                        />
                        <span className="truncate">{member.name}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {memberModalError && (
                <div className="rounded border border-red-400/30 bg-red-500/10 px-2 py-1 text-xs text-red-200">
                  {memberModalError}
                </div>
              )}

              <div className="flex items-center justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={closeMemberModal}
                  className="rounded-md border border-white/25 bg-black/30 px-3 py-1.5 text-xs font-medium text-white hover:bg-white/10"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => {
                    void saveMemberAssignments();
                  }}
                  disabled={memberModalBusy || !canManageProjects || !activeProject}
                  className="rounded-md bg-white px-3 py-1.5 text-xs font-semibold text-black transition hover:bg-gray-200 disabled:opacity-60"
                >
                  {memberModalBusy ? "Saving..." : "Save Assignment"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
