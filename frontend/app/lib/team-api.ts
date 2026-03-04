import { API_BASE } from "@/app/lib/config";
import type { ProjectStatus, TeamWorkspaceState } from "@/app/lib/enterprise-messaging";

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data?.detail === "string") return data.detail;
    if (typeof data?.message === "string") return data.message;
  } catch {
    // ignore
  }
  try {
    const text = await res.text();
    return text || "Request failed";
  } catch {
    return "Request failed";
  }
}

export function getTeamWsUrl(): string {
  const url = new URL(API_BASE);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  const basePath = url.pathname.replace(/\/+$/, "");
  url.pathname = `${basePath}/team/ws`;
  url.search = "";
  return url.toString();
}

export async function fetchTeamWorkspace(): Promise<TeamWorkspaceState> {
  const res = await fetch(`${API_BASE}/team/workspace`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function sendTeamMessage(payload: {
  conversationId: string;
  content: string;
  projectId?: string | null;
}) {
  const res = await fetch(`${API_BASE}/team/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: payload.conversationId,
      content: payload.content,
      project_id: payload.projectId || undefined,
    }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function markTeamConversationRead(conversationId: string) {
  const res = await fetch(`${API_BASE}/team/read`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function createTeamProject(payload: {
  conversationId: string;
  code: string;
  name: string;
  description?: string;
  assigneeIds: string[];
  priority?: "low" | "medium" | "high" | "critical";
  dueDate?: string;
}) {
  const res = await fetch(`${API_BASE}/team/projects`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: payload.conversationId,
      code: payload.code,
      project_id: payload.code,
      name: payload.name,
      description: payload.description || "",
      assignee_ids: payload.assigneeIds,
      priority: payload.priority || "medium",
      due_date: payload.dueDate || undefined,
    }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function updateTeamProjectStatus(payload: {
  projectId: string;
  status: ProjectStatus;
}) {
  const res = await fetch(`${API_BASE}/team/projects/${encodeURIComponent(payload.projectId)}/status`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: payload.status }),
  });
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}

export async function updateTeamProjectAssignees(payload: {
  projectId: string;
  assigneeIds: string[];
}) {
  const res = await fetch(
    `${API_BASE}/team/projects/${encodeURIComponent(payload.projectId)}/assignees`,
    {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assignee_ids: payload.assigneeIds }),
    }
  );
  if (!res.ok) throw new Error(await parseError(res));
  return res.json();
}
