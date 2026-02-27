"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BriefcaseBusiness,
  CheckCircle2,
  Clock3,
  MessageSquare,
  SendHorizonal,
  TriangleAlert,
  Users,
} from "lucide-react";
import type { AuthUser } from "@/app/lib/api";
import {
  getConversationUnreadCount,
  getMemberName,
  getTeamMemberId,
  type ProjectPriority,
  type ProjectStatus,
  type TeamConversation,
  type TeamMessage,
  type TeamWorkspaceState,
} from "@/app/lib/enterprise-messaging";

interface EnterpriseMessagingWorkspaceProps {
  user: AuthUser;
  workspace: TeamWorkspaceState;
  onChange: (next: TeamWorkspaceState) => void;
}

const PRIORITY_ORDER: ProjectPriority[] = ["critical", "high", "medium", "low"];
const STATUS_ORDER: ProjectStatus[] = ["planning", "active", "blocked", "done"];

function formatTime(timestamp: number) {
  const value = new Date(timestamp);
  return value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatDate(value?: string) {
  if (!value) return "No due date";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "No due date";
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function initials(name: string) {
  const parts = name.split(" ").filter(Boolean);
  if (parts.length === 0) return "TM";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
}

function priorityClass(priority: ProjectPriority) {
  if (priority === "critical") return "border-red-400/40 bg-red-500/10 text-red-200";
  if (priority === "high") return "border-orange-400/40 bg-orange-500/10 text-orange-200";
  if (priority === "medium") return "border-amber-300/40 bg-amber-400/10 text-amber-100";
  return "border-sky-300/40 bg-sky-500/10 text-sky-100";
}

function statusClass(status: ProjectStatus) {
  if (status === "done") return "text-emerald-300";
  if (status === "blocked") return "text-red-300";
  if (status === "active") return "text-cyan-300";
  return "text-amber-200";
}

export default function EnterpriseMessagingWorkspace({
  user,
  workspace,
  onChange,
}: EnterpriseMessagingWorkspaceProps) {
  const currentMemberId = useMemo(() => getTeamMemberId(user), [user]);
  const [chatSearch, setChatSearch] = useState("");
  const [draftMessage, setDraftMessage] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [projectDueDate, setProjectDueDate] = useState("");
  const [projectPriority, setProjectPriority] = useState<ProjectPriority>("medium");
  const [projectConversationId, setProjectConversationId] = useState<string>("");
  const [projectAssigneeIds, setProjectAssigneeIds] = useState<string[]>([]);
  const [projectError, setProjectError] = useState<string | null>(null);
  const [mobilePane, setMobilePane] = useState<"threads" | "chat" | "projects">("chat");

  const sortedConversations = useMemo(
    () => [...workspace.conversations].sort((a, b) => b.updatedAt - a.updatedAt),
    [workspace.conversations]
  );

  const activeConversationId =
    workspace.activeConversationId || sortedConversations[0]?.id || null;
  const activeConversation =
    sortedConversations.find((conversation) => conversation.id === activeConversationId) ||
    sortedConversations[0] ||
    null;

  const filteredConversations = useMemo(() => {
    const q = chatSearch.trim().toLowerCase();
    if (!q) return sortedConversations;
    return sortedConversations.filter((conversation) => {
      const lastMessage = conversation.messages[conversation.messages.length - 1];
      const haystack = `${conversation.name} ${lastMessage?.content || ""}`.toLowerCase();
      return haystack.includes(q);
    });
  }, [chatSearch, sortedConversations]);

  const projectsByConversation = useMemo(() => {
    const map = new Map<string, number>();
    for (const project of workspace.projects) {
      map.set(project.conversationId, (map.get(project.conversationId) || 0) + 1);
    }
    return map;
  }, [workspace.projects]);

  const activeConversationProjects = useMemo(() => {
    if (!activeConversation) return [];
    return workspace.projects
      .filter((project) => project.conversationId === activeConversation.id)
      .sort((a, b) => {
        const priorityDiff =
          PRIORITY_ORDER.indexOf(a.priority) - PRIORITY_ORDER.indexOf(b.priority);
        if (priorityDiff !== 0) return priorityDiff;
        return b.createdAt - a.createdAt;
      });
  }, [activeConversation, workspace.projects]);

  useEffect(() => {
    if (!projectConversationId && activeConversation?.id) {
      setProjectConversationId(activeConversation.id);
    }
  }, [activeConversation, projectConversationId]);

  function markConversationRead(conversation: TeamConversation) {
    const latestMessageAt =
      conversation.messages[conversation.messages.length - 1]?.createdAt ||
      conversation.updatedAt;
    onChange({
      ...workspace,
      activeConversationId: conversation.id,
      conversations: workspace.conversations.map((item) =>
        item.id === conversation.id
          ? {
              ...item,
              lastSeenAt: {
                ...item.lastSeenAt,
                [currentMemberId]: Math.max(
                  latestMessageAt,
                  item.lastSeenAt[currentMemberId] || 0
                ),
              },
            }
          : item
      ),
    });
  }

  function sendMessage() {
    const text = draftMessage.trim();
    if (!text || !activeConversation) return;

    const now = Date.now();
    const nextMessage: TeamMessage = {
      id: `${now}-${Math.random()}`,
      senderId: currentMemberId,
      content: text,
      createdAt: now,
    };

    onChange({
      ...workspace,
      conversations: workspace.conversations.map((conversation) =>
        conversation.id === activeConversation.id
          ? {
              ...conversation,
              messages: [...conversation.messages, nextMessage],
              updatedAt: now,
              lastSeenAt: { ...conversation.lastSeenAt, [currentMemberId]: now },
            }
          : conversation
      ),
    });
    setDraftMessage("");
  }

  function toggleAssignee(memberId: string) {
    setProjectAssigneeIds((prev) =>
      prev.includes(memberId)
        ? prev.filter((item) => item !== memberId)
        : [...prev, memberId]
    );
  }

  function createProject() {
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
      projectConversationId || activeConversation?.id || workspace.conversations[0]?.id || "";
    if (!targetConversationId) {
      setProjectError("No conversation selected.");
      return;
    }

    const now = Date.now();
    const projectCode = `PRJ-${workspace.projectCounter}`;
    const projectId = `${now}-${Math.random()}`;

    const summaryText = `${projectCode} assigned to ${projectAssigneeIds
      .map((id) => getMemberName(workspace.members, id))
      .join(", ")}.`;

    const nextProject = {
      id: projectId,
      code: projectCode,
      name: cleanName,
      description: projectDescription.trim(),
      conversationId: targetConversationId,
      ownerId: currentMemberId,
      assigneeIds: [...projectAssigneeIds],
      priority: projectPriority,
      status: "planning" as ProjectStatus,
      dueDate: projectDueDate || undefined,
      createdAt: now,
    };

    onChange({
      ...workspace,
      projectCounter: workspace.projectCounter + 1,
      projects: [nextProject, ...workspace.projects],
      activeConversationId: targetConversationId,
      conversations: workspace.conversations.map((conversation) => {
        if (conversation.id !== targetConversationId) return conversation;
        const systemAssignmentMessage: TeamMessage = {
          id: `${now}-assignment`,
          senderId: currentMemberId,
          content: `[Project Setup] ${cleanName}. ${summaryText}`,
          createdAt: now,
          projectId,
        };
        return {
          ...conversation,
          messages: [...conversation.messages, systemAssignmentMessage],
          projectIds: conversation.projectIds.includes(projectId)
            ? conversation.projectIds
            : [...conversation.projectIds, projectId],
          updatedAt: now,
          lastSeenAt: {
            ...conversation.lastSeenAt,
            [currentMemberId]: now,
          },
        };
      }),
    });

    setProjectName("");
    setProjectDescription("");
    setProjectDueDate("");
    setProjectAssigneeIds([]);
    setProjectPriority("medium");
    setProjectError(null);
    setMobilePane("projects");
  }

  function updateProjectStatus(projectId: string, nextStatus: ProjectStatus) {
    onChange({
      ...workspace,
      projects: workspace.projects.map((project) =>
        project.id === projectId ? { ...project, status: nextStatus } : project
      ),
    });
  }

  return (
    <div className="h-full bg-[#0a141b] text-[#e9edef]">
      <div className="border-b border-white/10 bg-[#111b21] px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setMobilePane("threads")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium lg:hidden ${
              mobilePane === "threads"
                ? "bg-emerald-500/20 text-emerald-200"
                : "bg-white/5 text-gray-300"
            }`}
          >
            <MessageSquare size={14} className="mr-1 inline-block" />
            Conversations
          </button>
          <button
            type="button"
            onClick={() => setMobilePane("chat")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium lg:hidden ${
              mobilePane === "chat"
                ? "bg-emerald-500/20 text-emerald-200"
                : "bg-white/5 text-gray-300"
            }`}
          >
            <Users size={14} className="mr-1 inline-block" />
            Messages
          </button>
          <button
            type="button"
            onClick={() => setMobilePane("projects")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium lg:hidden ${
              mobilePane === "projects"
                ? "bg-emerald-500/20 text-emerald-200"
                : "bg-white/5 text-gray-300"
            }`}
          >
            <BriefcaseBusiness size={14} className="mr-1 inline-block" />
            Projects
          </button>
          <div className="ml-auto text-xs text-gray-400">
            Enterprise Messaging and Project Assignment
          </div>
        </div>
      </div>

      <div className="grid h-[calc(100%-57px)] grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)_340px]">
        <aside
          className={`border-r border-white/10 bg-[#111b21] ${
            mobilePane === "threads" ? "block" : "hidden lg:block"
          }`}
        >
          <div className="border-b border-white/10 p-3">
            <input
              value={chatSearch}
              onChange={(event) => setChatSearch(event.target.value)}
              placeholder="Search conversations"
              className="w-full rounded-lg border border-white/10 bg-[#1f2c33] px-3 py-2 text-sm text-white outline-none placeholder:text-gray-500"
            />
          </div>
          <div className="h-[calc(100%-64px)] overflow-y-auto">
            {filteredConversations.map((conversation) => {
              const lastMessage = conversation.messages[conversation.messages.length - 1];
              const unread = getConversationUnreadCount(conversation, currentMemberId);
              const isActive = conversation.id === activeConversation?.id;
              const projectCount = projectsByConversation.get(conversation.id) || 0;
              return (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => {
                    markConversationRead(conversation);
                    setMobilePane("chat");
                  }}
                  className={`w-full border-b border-white/5 px-4 py-3 text-left transition-colors ${
                    isActive ? "bg-white/10" : "hover:bg-white/5"
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div className="h-10 w-10 shrink-0 rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 text-center text-xs font-semibold leading-10 text-white">
                      {initials(conversation.name)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-gray-100">
                          {conversation.name}
                        </span>
                        <span className="shrink-0 text-[11px] text-gray-500">
                          {lastMessage ? formatTime(lastMessage.createdAt) : ""}
                        </span>
                      </div>
                      <div className="mt-1 flex items-center justify-between gap-2">
                        <span className="truncate text-xs text-gray-400">
                          {lastMessage?.content || "No messages yet"}
                        </span>
                        {unread > 0 ? (
                          <span className="inline-flex min-w-[20px] items-center justify-center rounded-full bg-emerald-500 px-1.5 py-0.5 text-[11px] font-semibold text-[#04140f]">
                            {unread > 9 ? "9+" : unread}
                          </span>
                        ) : (
                          <span className="text-[10px] text-gray-500">
                            {projectCount} projects
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
            {filteredConversations.length === 0 && (
              <div className="px-4 py-6 text-center text-sm text-gray-500">
                No conversation matches this search.
              </div>
            )}
          </div>
        </aside>

        <section
          className={`relative flex flex-col bg-[#0b141a] ${
            mobilePane === "chat" ? "flex" : "hidden lg:flex"
          }`}
        >
          {activeConversation ? (
            <>
              <div className="border-b border-white/10 bg-[#202c33] px-5 py-3">
                <div className="text-sm font-semibold text-gray-100">
                  {activeConversation.name}
                </div>
                <div className="mt-1 text-xs text-gray-400">
                  {activeConversation.participantIds
                    .map((id) => getMemberName(workspace.members, id))
                    .join(", ")}
                </div>
              </div>
              <div className="relative flex-1 overflow-y-auto px-4 py-5">
                <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(12,156,128,0.13),transparent_45%),radial-gradient(circle_at_80%_80%,rgba(34,109,161,0.18),transparent_45%)]" />
                <div className="relative mx-auto max-w-3xl space-y-2">
                  {activeConversation.messages.map((message) => {
                    const isMine = message.senderId === currentMemberId;
                    const senderName = getMemberName(workspace.members, message.senderId);
                    return (
                      <div
                        key={message.id}
                        className={`flex ${isMine ? "justify-end" : "justify-start"}`}
                      >
                        <div
                          className={`max-w-[84%] rounded-xl px-3 py-2 shadow-sm ${
                            isMine
                              ? "rounded-br-sm bg-[#005c4b] text-[#e9edef]"
                              : "rounded-bl-sm bg-[#202c33] text-[#e9edef]"
                          }`}
                        >
                          {!isMine && (
                            <div className="mb-1 text-[11px] font-medium text-cyan-300">
                              {senderName}
                            </div>
                          )}
                          <div className="whitespace-pre-wrap text-sm leading-relaxed">
                            {message.content}
                          </div>
                          <div className="mt-1 text-right text-[10px] text-gray-300/80">
                            {formatTime(message.createdAt)}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="border-t border-white/10 bg-[#202c33] px-4 py-3">
                <div className="mx-auto flex max-w-3xl items-end gap-3">
                  <textarea
                    value={draftMessage}
                    onChange={(event) => setDraftMessage(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        sendMessage();
                      }
                    }}
                    placeholder="Type a message to your team..."
                    className="max-h-40 min-h-[42px] flex-1 resize-y rounded-xl border border-white/10 bg-[#0f1e27] px-3 py-2 text-sm text-white outline-none placeholder:text-gray-500"
                  />
                  <button
                    type="button"
                    onClick={sendMessage}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500 text-[#07221b] hover:bg-emerald-400"
                    aria-label="Send message"
                  >
                    <SendHorizonal size={16} />
                  </button>
                </div>
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-gray-500">
              No conversations available.
            </div>
          )}
        </section>

        <aside
          className={`border-l border-white/10 bg-[#0f1f28] ${
            mobilePane === "projects" ? "block" : "hidden lg:block"
          }`}
        >
          <div className="h-full overflow-y-auto px-4 py-4">
            <div className="mb-4 rounded-xl border border-white/10 bg-black/20 p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-100">
                <BriefcaseBusiness size={16} className="text-emerald-300" />
                Project Setup and Assignment
              </div>
              <div className="space-y-2">
                <input
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  placeholder="Project name"
                  className="w-full rounded-lg border border-white/10 bg-[#122734] px-3 py-2 text-sm text-white outline-none placeholder:text-gray-500"
                />
                <textarea
                  value={projectDescription}
                  onChange={(event) => setProjectDescription(event.target.value)}
                  placeholder="Project scope or notes"
                  className="h-20 w-full resize-none rounded-lg border border-white/10 bg-[#122734] px-3 py-2 text-sm text-white outline-none placeholder:text-gray-500"
                />
                <div className="grid grid-cols-2 gap-2">
                  <select
                    value={projectConversationId}
                    onChange={(event) => setProjectConversationId(event.target.value)}
                    className="rounded-lg border border-white/10 bg-[#122734] px-2 py-2 text-xs text-white outline-none"
                  >
                    {sortedConversations.map((conversation) => (
                      <option key={conversation.id} value={conversation.id}>
                        {conversation.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={projectPriority}
                    onChange={(event) =>
                      setProjectPriority(event.target.value as ProjectPriority)
                    }
                    className="rounded-lg border border-white/10 bg-[#122734] px-2 py-2 text-xs text-white outline-none"
                  >
                    {PRIORITY_ORDER.map((priority) => (
                      <option key={priority} value={priority}>
                        {priority.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </div>
                <input
                  type="date"
                  value={projectDueDate}
                  onChange={(event) => setProjectDueDate(event.target.value)}
                  className="w-full rounded-lg border border-white/10 bg-[#122734] px-3 py-2 text-xs text-white outline-none"
                />

                <div className="rounded-lg border border-white/10 bg-[#122734] p-2">
                  <div className="mb-2 text-[11px] font-medium text-gray-300">
                    Assign team members
                  </div>
                  <div className="space-y-1">
                    {workspace.members.map((member) => (
                      <label
                        key={member.id}
                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-gray-200 hover:bg-white/5"
                      >
                        <input
                          type="checkbox"
                          checked={projectAssigneeIds.includes(member.id)}
                          onChange={() => toggleAssignee(member.id)}
                          className="h-3.5 w-3.5 accent-emerald-400"
                        />
                        <span className="truncate">{member.name}</span>
                      </label>
                    ))}
                  </div>
                </div>

                {projectError && (
                  <div className="rounded border border-red-400/30 bg-red-500/10 px-2 py-1 text-xs text-red-200">
                    {projectError}
                  </div>
                )}
                <button
                  type="button"
                  onClick={createProject}
                  className="w-full rounded-lg bg-emerald-500 px-3 py-2 text-sm font-semibold text-[#08241d] hover:bg-emerald-400"
                >
                  Create and Assign Project
                </button>
              </div>
            </div>

            <div className="space-y-3">
              {activeConversationProjects.map((project) => (
                <div
                  key={project.id}
                  className="rounded-xl border border-white/10 bg-black/20 p-3"
                >
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div>
                      <div className="text-xs text-gray-400">{project.code}</div>
                      <div className="text-sm font-semibold text-gray-100">{project.name}</div>
                    </div>
                    <span
                      className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase ${priorityClass(
                        project.priority
                      )}`}
                    >
                      {project.priority}
                    </span>
                  </div>
                  <p className="mb-2 text-xs text-gray-300">
                    {project.description || "No project description provided."}
                  </p>

                  <div className="mb-2 flex items-center justify-between gap-2 text-[11px] text-gray-400">
                    <span>Due: {formatDate(project.dueDate)}</span>
                    <span className={`font-medium ${statusClass(project.status)}`}>
                      {project.status.toUpperCase()}
                    </span>
                  </div>

                  <div className="mb-2 flex flex-wrap gap-1">
                    {project.assigneeIds.map((assigneeId) => (
                      <span
                        key={assigneeId}
                        className="rounded-full border border-white/15 bg-white/5 px-2 py-0.5 text-[10px] text-gray-200"
                      >
                        {getMemberName(workspace.members, assigneeId)}
                      </span>
                    ))}
                  </div>

                  <select
                    value={project.status}
                    onChange={(event) =>
                      updateProjectStatus(project.id, event.target.value as ProjectStatus)
                    }
                    className="w-full rounded border border-white/10 bg-[#122734] px-2 py-1.5 text-xs text-white outline-none"
                  >
                    {STATUS_ORDER.map((status) => (
                      <option key={status} value={status}>
                        {status.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </div>
              ))}

              {activeConversationProjects.length === 0 && (
                <div className="rounded-xl border border-white/10 bg-black/20 p-4 text-center text-xs text-gray-400">
                  No projects assigned for this conversation yet.
                </div>
              )}
            </div>

            <div className="mt-4 rounded-xl border border-white/10 bg-black/20 p-3 text-[11px] text-gray-400">
              <div className="mb-1 flex items-center gap-1 font-medium text-gray-300">
                <Clock3 size={12} />
                Enterprise Workflow Signals
              </div>
              <div className="space-y-1">
                <div className="flex items-center gap-1">
                  <CheckCircle2 size={11} className="text-emerald-300" />
                  Done projects should include handover notes.
                </div>
                <div className="flex items-center gap-1">
                  <TriangleAlert size={11} className="text-red-300" />
                  Blocked status should include dependency owner updates.
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
