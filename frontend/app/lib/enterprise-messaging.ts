import type { AuthUser } from "@/app/lib/api";

export type WorkspaceMode = "ai" | "pml" | "team";

export type ProjectPriority = "low" | "medium" | "high" | "critical";
export type ProjectStatus = "planning" | "active" | "blocked" | "done";

export interface TeamMember {
  id: string;
  name: string;
  title: string;
  department: string;
  color: string;
}

export interface TeamMessage {
  id: string;
  senderId: string;
  content: string;
  createdAt: number;
  projectId?: string;
}

export interface TeamConversation {
  id: string;
  name: string;
  participantIds: string[];
  projectIds: string[];
  messages: TeamMessage[];
  updatedAt: number;
  lastSeenAt: Record<string, number>;
}

export interface TeamProject {
  id: string;
  code: string;
  name: string;
  description: string;
  conversationId: string;
  ownerId: string;
  assigneeIds: string[];
  priority: ProjectPriority;
  status: ProjectStatus;
  dueDate?: string;
  createdAt: number;
}

export interface TeamWorkspaceState {
  members: TeamMember[];
  conversations: TeamConversation[];
  projects: TeamProject[];
  activeConversationId: string | null;
  projectCounter: number;
}

const STORAGE_PREFIX = "kavin-enterprise-messaging-v1";

function uuidv4() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const rand = (Math.random() * 16) | 0;
    const value = char === "x" ? rand : (rand & 0x3) | 0x8;
    return value.toString(16);
  });
}

function sanitizeKey(input: string) {
  const value = input.trim().toLowerCase();
  if (!value) return "default";
  return value.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "default";
}

function minutesAgo(now: number, minutes: number) {
  return now - minutes * 60 * 1000;
}

function daysFromNow(now: number, days: number) {
  return new Date(now + days * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

function getDisplayName(user: AuthUser) {
  const name = (user.username || "").trim();
  if (name) return name;
  const email = (user.email || "").trim();
  if (!email) return "You";
  return email.split("@")[0] || "You";
}

export function getTeamMemberId(user: AuthUser) {
  return `member-${sanitizeKey(user.username || user.email || "default")}`;
}

function getStorageKey(user: AuthUser) {
  return `${STORAGE_PREFIX}:${sanitizeKey(user.username || user.email || "default")}`;
}

function createDefaultWorkspace(user: AuthUser): TeamWorkspaceState {
  const now = Date.now();
  const currentMemberId = getTeamMemberId(user);
  const currentName = getDisplayName(user);

  const members: TeamMember[] = [
    {
      id: currentMemberId,
      name: currentName,
      title: "Team Member",
      department: "Operations",
      color: "from-emerald-500 to-teal-500",
    },
    {
      id: "member-mia-chen",
      name: "Mia Chen",
      title: "Delivery Lead",
      department: "Program Office",
      color: "from-cyan-500 to-blue-500",
    },
    {
      id: "member-rahul-shah",
      name: "Rahul Shah",
      title: "Solutions Architect",
      department: "Engineering",
      color: "from-indigo-500 to-sky-500",
    },
    {
      id: "member-nora-ibrahim",
      name: "Nora Ibrahim",
      title: "Product Manager",
      department: "Product",
      color: "from-orange-500 to-amber-500",
    },
    {
      id: "member-liam-walker",
      name: "Liam Walker",
      title: "QA Lead",
      department: "Quality",
      color: "from-pink-500 to-rose-500",
    },
  ];

  const conversationOpsId = uuidv4();
  const conversationLaunchId = uuidv4();
  const projectAId = uuidv4();
  const projectBId = uuidv4();

  const conversationOps: TeamConversation = {
    id: conversationOpsId,
    name: "Operations Command",
    participantIds: [currentMemberId, "member-mia-chen", "member-rahul-shah"],
    projectIds: [projectAId],
    updatedAt: minutesAgo(now, 12),
    lastSeenAt: {
      [currentMemberId]: minutesAgo(now, 20),
      "member-mia-chen": minutesAgo(now, 5),
      "member-rahul-shah": minutesAgo(now, 6),
    },
    messages: [
      {
        id: uuidv4(),
        senderId: "member-mia-chen",
        content: "Security sign-off is done. We can start vendor onboarding cutover today.",
        createdAt: minutesAgo(now, 34),
      },
      {
        id: uuidv4(),
        senderId: currentMemberId,
        content: "Great. I will coordinate with engineering and confirm the deployment slot.",
        createdAt: minutesAgo(now, 26),
      },
      {
        id: uuidv4(),
        senderId: "member-rahul-shah",
        content: "I have prepared the rollout checklist. Need one owner for UAT sign-off.",
        createdAt: minutesAgo(now, 12),
        projectId: projectAId,
      },
    ],
  };

  const conversationLaunch: TeamConversation = {
    id: conversationLaunchId,
    name: "Launch Readiness Pod",
    participantIds: [currentMemberId, "member-nora-ibrahim", "member-liam-walker"],
    projectIds: [projectBId],
    updatedAt: minutesAgo(now, 52),
    lastSeenAt: {
      [currentMemberId]: minutesAgo(now, 9),
      "member-nora-ibrahim": minutesAgo(now, 8),
      "member-liam-walker": minutesAgo(now, 8),
    },
    messages: [
      {
        id: uuidv4(),
        senderId: "member-nora-ibrahim",
        content: "Need project owners to validate the enterprise release notes by tomorrow.",
        createdAt: minutesAgo(now, 73),
        projectId: projectBId,
      },
      {
        id: uuidv4(),
        senderId: currentMemberId,
        content: "Assigning QA and customer enablement in the new project setup panel now.",
        createdAt: minutesAgo(now, 52),
        projectId: projectBId,
      },
    ],
  };

  const projects: TeamProject[] = [
    {
      id: projectAId,
      code: "PRJ-1042",
      name: "Vendor Onboarding Portal",
      description: "Complete enterprise onboarding workflow and UAT sign-off.",
      conversationId: conversationOpsId,
      ownerId: "member-mia-chen",
      assigneeIds: [currentMemberId, "member-rahul-shah"],
      priority: "high",
      status: "active",
      dueDate: daysFromNow(now, 3),
      createdAt: minutesAgo(now, 1400),
    },
    {
      id: projectBId,
      code: "PRJ-1065",
      name: "Q2 Rollout Playbook",
      description: "Prepare launch readiness plan for enterprise rollout.",
      conversationId: conversationLaunchId,
      ownerId: currentMemberId,
      assigneeIds: ["member-nora-ibrahim", "member-liam-walker"],
      priority: "medium",
      status: "planning",
      dueDate: daysFromNow(now, 10),
      createdAt: minutesAgo(now, 2900),
    },
  ];

  return {
    members,
    conversations: [conversationOps, conversationLaunch],
    projects,
    activeConversationId: conversationOpsId,
    projectCounter: 1066,
  };
}

function normalizeWorkspace(raw: unknown, user: AuthUser): TeamWorkspaceState {
  const fallback = createDefaultWorkspace(user);
  if (!raw || typeof raw !== "object") return fallback;

  const candidate = raw as Partial<TeamWorkspaceState>;
  const members = Array.isArray(candidate.members)
    ? candidate.members.filter(Boolean).map((member) => ({
        id: typeof member.id === "string" ? member.id : uuidv4(),
        name: typeof member.name === "string" ? member.name : "Member",
        title: typeof member.title === "string" ? member.title : "Team Member",
        department: typeof member.department === "string" ? member.department : "General",
        color: typeof member.color === "string" ? member.color : "from-slate-500 to-slate-600",
      }))
    : fallback.members;

  const memberIds = new Set(members.map((member) => member.id));
  const conversations = Array.isArray(candidate.conversations)
    ? candidate.conversations
        .filter(Boolean)
        .map((conversation) => {
          const safeMessages = Array.isArray(conversation.messages)
            ? conversation.messages
                .filter(Boolean)
                .map((message) => ({
                  id: typeof message.id === "string" ? message.id : uuidv4(),
                  senderId:
                    typeof message.senderId === "string" && memberIds.has(message.senderId)
                      ? message.senderId
                      : getTeamMemberId(user),
                  content: typeof message.content === "string" ? message.content : "",
                  createdAt:
                    typeof message.createdAt === "number" ? message.createdAt : Date.now(),
                  projectId: typeof message.projectId === "string" ? message.projectId : undefined,
                }))
            : [];

          const lastSeenAtRaw =
            conversation.lastSeenAt && typeof conversation.lastSeenAt === "object"
              ? conversation.lastSeenAt
              : {};
          const safeLastSeenAt: Record<string, number> = {};
          for (const id of Object.keys(lastSeenAtRaw)) {
            const value = (lastSeenAtRaw as Record<string, unknown>)[id];
            if (typeof value === "number") {
              safeLastSeenAt[id] = value;
            }
          }

          return {
            id: typeof conversation.id === "string" ? conversation.id : uuidv4(),
            name: typeof conversation.name === "string" ? conversation.name : "Team Chat",
            participantIds: Array.isArray(conversation.participantIds)
              ? conversation.participantIds.filter((id): id is string => typeof id === "string")
              : [getTeamMemberId(user)],
            projectIds: Array.isArray(conversation.projectIds)
              ? conversation.projectIds.filter((id): id is string => typeof id === "string")
              : [],
            messages: safeMessages.sort((a, b) => a.createdAt - b.createdAt),
            updatedAt:
              typeof conversation.updatedAt === "number"
                ? conversation.updatedAt
                : safeMessages[safeMessages.length - 1]?.createdAt || Date.now(),
            lastSeenAt: safeLastSeenAt,
          };
        })
        .filter((conversation) => conversation.participantIds.length > 0)
    : fallback.conversations;

  const conversationIdSet = new Set(conversations.map((conversation) => conversation.id));

  const projects = Array.isArray(candidate.projects)
    ? candidate.projects
        .filter(Boolean)
        .map((project) => ({
          id: typeof project.id === "string" ? project.id : uuidv4(),
          code: typeof project.code === "string" ? project.code : "PRJ-0000",
          name: typeof project.name === "string" ? project.name : "Untitled Project",
          description: typeof project.description === "string" ? project.description : "",
          conversationId:
            typeof project.conversationId === "string" && conversationIdSet.has(project.conversationId)
              ? project.conversationId
              : conversations[0]?.id || "",
          ownerId:
            typeof project.ownerId === "string" && memberIds.has(project.ownerId)
              ? project.ownerId
              : getTeamMemberId(user),
          assigneeIds: Array.isArray(project.assigneeIds)
            ? project.assigneeIds.filter((id): id is string => typeof id === "string" && memberIds.has(id))
            : [],
          priority:
            project.priority === "critical" ||
            project.priority === "high" ||
            project.priority === "low" ||
            project.priority === "medium"
              ? project.priority
              : "medium",
          status:
            project.status === "planning" ||
            project.status === "active" ||
            project.status === "blocked" ||
            project.status === "done"
              ? project.status
              : "planning",
          dueDate: typeof project.dueDate === "string" ? project.dueDate : undefined,
          createdAt: typeof project.createdAt === "number" ? project.createdAt : Date.now(),
        }))
        .filter((project) => Boolean(project.conversationId))
    : fallback.projects;

  const activeConversationId =
    typeof candidate.activeConversationId === "string" &&
    conversationIdSet.has(candidate.activeConversationId)
      ? candidate.activeConversationId
      : conversations[0]?.id || null;

  return {
    members: members.length > 0 ? members : fallback.members,
    conversations: conversations.length > 0 ? conversations : fallback.conversations,
    projects,
    activeConversationId,
    projectCounter:
      typeof candidate.projectCounter === "number" && candidate.projectCounter > 0
        ? Math.floor(candidate.projectCounter)
        : fallback.projectCounter,
  };
}

export function loadTeamWorkspace(user: AuthUser): TeamWorkspaceState {
  if (typeof window === "undefined") {
    return createDefaultWorkspace(user);
  }
  const key = getStorageKey(user);
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return createDefaultWorkspace(user);
    return normalizeWorkspace(JSON.parse(raw), user);
  } catch {
    return createDefaultWorkspace(user);
  }
}

export function saveTeamWorkspace(user: AuthUser, state: TeamWorkspaceState) {
  if (typeof window === "undefined") return;
  const key = getStorageKey(user);
  window.localStorage.setItem(key, JSON.stringify(state));
}

export function getMemberName(members: TeamMember[], memberId: string) {
  const member = members.find((item) => item.id === memberId);
  return member?.name || "Unknown Member";
}

export function isProjectSystemTeamMessage(message: TeamMessage | undefined) {
  if (!message) return false;
  if (message.projectId) return true;
  const text = (message.content || "").trim().toLowerCase();
  return text.startsWith("[project setup]") || text.startsWith("[project status]");
}

export function getConversationUnreadCount(
  conversation: TeamConversation,
  memberId: string
) {
  const seenAt = conversation.lastSeenAt[memberId] || 0;
  return conversation.messages.reduce((count, message) => {
    if (message.senderId === memberId) return count;
    if (isProjectSystemTeamMessage(message)) return count;
    if (message.createdAt > seenAt) return count + 1;
    return count;
  }, 0);
}

export function getTotalTeamUnreadCount(state: TeamWorkspaceState, memberId: string) {
  return state.conversations.reduce(
    (sum, conversation) => sum + getConversationUnreadCount(conversation, memberId),
    0
  );
}
