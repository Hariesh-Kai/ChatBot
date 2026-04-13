// frontend/app/page.tsx
"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Bell, FileClock, Users, X } from "lucide-react";
import Sidebar from "@/app/components/sidebar/Sidebar";
import ChatWindow from "@/app/components/chat/ChatWindow";
import TemplateLibraryPanel from "@/app/components/pml/TemplateLibraryPanel";
import EnterpriseMessagingWorkspace from "@/app/components/messaging/EnterpriseMessagingWorkspace";
import WorkspaceModeSwitcher from "@/app/components/workspace/WorkspaceModeSwitcher";
import StartupSplash from "@/app/components/StartupSplash";
import GettingStartedModal from "@/app/components/onboarding/GettingStartedModal";
import ShortcutsModal from "@/app/components/onboarding/ShortcutsModal";
import { ChatSession, Message } from "@/app/lib/types";
import { ChatUIModelId } from "@/app/lib/chat-ui-models";
import { loadChats, saveChats } from "@/app/lib/chat-store";
import { loadPmlChats, savePmlChats } from "@/app/lib/pml-chat-store";
import {
  loadPendingIngestionItems,
  savePendingIngestionItems,
} from "@/app/lib/pending-ingestion-store";
import { authLogout, authMe, cancelUploadJob, updateMetadata } from "@/app/lib/api";
import type { AuthUser, UploadIngestionStatusResponse } from "@/app/lib/api";
import {
  isProjectSystemTeamMessage,
  getTeamMemberId,
  getTotalTeamUnreadCount,
  type TeamMessage,
  type TeamProject,
  type TeamWorkspaceState,
  type WorkspaceMode,
} from "@/app/lib/enterprise-messaging";
import {
  createTeamProject,
  fetchTeamWorkspace,
  getTeamWsUrl,
  markTeamConversationRead,
  sendTeamMessage,
  updateTeamProjectAssignees,
} from "@/app/lib/team-api";
import { MetadataRequestField } from "@/app/lib/llm-ui-events";
import { UploadStatus } from "@/app/hooks/useSmartUpload";
import {
  useIngestionStatusPoller,
  type PendingIngestionPollItem,
} from "@/app/hooks/useIngestionStatusPoller";
import { API_BASE } from "@/app/lib/config";
import {
  deletePmlTemplate as deletePmlTemplateApi,
  fetchPmlTemplates,
  learnPmlTemplate,
  streamPmlChat,
  type PmlTemplate,
} from "@/app/lib/pml-api";
import { getRoleLabel } from "@/app/lib/org-role-catalog";
import type {
  WorkspaceDocumentRow,
} from "@/app/components/workspace/types";

type UploadCancelPhase = "metadata" | "preprocessing" | "ingestion";

type UploadCancelState = {
  chatId: string;
  messageId: string;
  jobId: string;
  label: string;
  phase: UploadCancelPhase;
};

/* =========================================================
   HELPER: UUID
========================================================= */
function uuidv4() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const DOC_PROCESSING_BACKGROUND_TEXT =
  "Document is processing in background. You can keep chatting now; document answers will activate when indexing finishes.";
const DOC_READY_TEXT =
  "Document is ready. Answers now include this document automatically.";
const DOC_ERROR_TEXT_PREFIX = "Document processing failed in background";
const CHAT_READ_KEY_PREFIX = "chat-ui-chat-read-at";
const WELCOME_SEEN_KEY_PREFIX = "chat-ui-welcome-seen";
const WORKSPACE_MODE_KEY_PREFIX = "chat-ui-workspace-mode";
const SIDEBAR_OPEN_KEY_PREFIX = "chat-ui-sidebar-open";

function normalizeRevisionLabel(value?: string | number | null) {
  if (value === null || value === undefined) return "R-";
  const raw = String(value).trim();
  if (!raw) return "R-";
  return raw.toUpperCase().startsWith("R") ? raw.toUpperCase() : `R${raw}`;
}

function buildProjectIdentity(documentId: string) {
  const safe = (documentId || "general").trim();
  const token = safe.split(/[-_]/).filter(Boolean).slice(0, 2).join("-") || "general";
  const id = `project-${token.toLowerCase()}`;
  const label = token
    .split("-")
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
  return {
    id,
    name: label ? `Project ${label}` : "Project General",
  };
}

function getIncomingMessageTimestamp(chat: ChatSession): number {
  return chat.messages.reduce((latest, msg) => {
    if (msg.role === "user") return latest;
    return Math.max(latest, msg.createdAt || 0);
  }, 0);
}

function getIncomingMessageCountSince(chat: ChatSession, sinceTs: number): number {
  return chat.messages.reduce((count, msg) => {
    if (msg.role === "user") return count;
    return msg.createdAt > sinceTs ? count + 1 : count;
  }, 0);
}

function formatRelativeTime(timestamp: number) {
  const diffMs = Date.now() - timestamp;
  const diffMin = Math.max(1, Math.floor(diffMs / 60000));
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  return `${diffDay}d ago`;
}

function deriveWorkspaceDocuments(
  chats: ChatSession[]
): WorkspaceDocumentRow[] {
  const records = new Map<
    string,
    WorkspaceDocumentRow & {
      chunkSet: Set<string>;
    }
  >();

  for (const chat of chats) {
    for (const message of chat.messages) {
      if (!Array.isArray(message.sources)) continue;
      for (const source of message.sources) {
        const documentId =
          (source.company_document_id || source.fileName || "unassigned-document").trim() ||
          "unassigned-document";
        const revision = normalizeRevisionLabel(source.revision_number);
        const project = buildProjectIdentity(documentId);
        const rowId = `${documentId}::${revision}`;
        const existing = records.get(rowId);
        const chunkKey =
          (source.chunk_id || "").trim() ||
          `${source.id || source.fileName || "chunk"}:${source.page || 0}`;

        if (existing) {
          existing.lastUpdated = Math.max(existing.lastUpdated, message.createdAt || 0);
          existing.chunkSet.add(chunkKey);
          continue;
        }

        records.set(rowId, {
          id: rowId,
          projectId: project.id,
          projectName: project.name,
          documentId,
          name: source.fileName || documentId,
          revision,
          chunks: 0,
          status: "Indexed",
          lastUpdated: message.createdAt || Date.now(),
          chunkSet: new Set([chunkKey]),
        });
      }
    }
  }

  return Array.from(records.values())
    .map((item) => ({
      id: item.id,
      projectId: item.projectId,
      projectName: item.projectName,
      documentId: item.documentId,
      name: item.name,
      revision: item.revision,
      chunks: item.chunkSet.size,
      status: item.status,
      lastUpdated: item.lastUpdated,
    }))
    .sort((a, b) => b.lastUpdated - a.lastUpdated);
}

function upsertTeamMessage(
  workspace: TeamWorkspaceState,
  conversationId: string,
  message: TeamMessage
): TeamWorkspaceState {
  const cleanConversationId = (conversationId || "").trim();
  if (!cleanConversationId) return workspace;

  let changed = false;
  const nextConversations = workspace.conversations.map((conversation) => {
    if (conversation.id !== cleanConversationId) return conversation;
    if (conversation.messages.some((item) => item.id === message.id)) {
      return conversation;
    }
    changed = true;
    const nextMessages = [...conversation.messages, message].sort(
      (a, b) => a.createdAt - b.createdAt
    );
    return {
      ...conversation,
      messages: nextMessages,
      updatedAt: Math.max(conversation.updatedAt, message.createdAt),
    };
  });

  return changed ? { ...workspace, conversations: nextConversations } : workspace;
}

function upsertTeamProject(
  workspace: TeamWorkspaceState,
  project: TeamProject
): TeamWorkspaceState {
  const projectExists = workspace.projects.some((item) => item.id === project.id);
  const nextProjects = projectExists
    ? workspace.projects.map((item) => (item.id === project.id ? project : item))
    : [project, ...workspace.projects];

  const nextConversations = workspace.conversations.map((conversation) => {
    if (conversation.id !== project.conversationId) return conversation;
    if (conversation.projectIds.includes(project.id)) return conversation;
    return { ...conversation, projectIds: [...conversation.projectIds, project.id] };
  });

  return { ...workspace, projects: nextProjects, conversations: nextConversations };
}

function updateTeamReadMarker(
  workspace: TeamWorkspaceState,
  conversationId: string,
  memberId: string,
  readAt: number
): TeamWorkspaceState {
  let changed = false;
  const nextConversations = workspace.conversations.map((conversation) => {
    if (conversation.id !== conversationId) return conversation;
    const previous = conversation.lastSeenAt[memberId] || 0;
    const nextValue = Math.max(previous, readAt);
    if (nextValue === previous) return conversation;
    changed = true;
    return {
      ...conversation,
      lastSeenAt: {
        ...conversation.lastSeenAt,
        [memberId]: nextValue,
      },
    };
  });

  return changed ? { ...workspace, conversations: nextConversations } : workspace;
}



/* =========================================================
    NORMALIZE MESSAGES
  ========================================================= */

export default function Home() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [welcomeGateResolved, setWelcomeGateResolved] = useState(false);

  useEffect(() => {
    authMe()
      .then((u) => {
        if (!u) {
          router.replace("/signin");
          return;
        }
        setUser(u);
      })
      .finally(() => setAuthChecked(true));
  }, [router]);

  useEffect(() => {
    if (!authChecked) return;

    if (!user) {
      setWelcomeGateResolved(true);
      return;
    }

    if (typeof window === "undefined") {
      setWelcomeGateResolved(true);
      return;
    }

    const key = `${WELCOME_SEEN_KEY_PREFIX}:${(user.username || user.email || "default")
      .trim()
      .toLowerCase()}`;
    const hasSeenWelcome = Boolean(window.localStorage.getItem(key));

    if (!hasSeenWelcome) {
      window.localStorage.setItem(key, new Date().toISOString());
      router.replace("/welcome");
      return;
    }

    setWelcomeGateResolved(true);
  }, [authChecked, user, router]);

  const handleSignOut = useCallback(async () => {
    try {
      await authLogout();
    } finally {
      setUser(null);
      router.replace("/signin");
    }
  }, [router]);

  if (!authChecked) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-black text-gray-400">
        Loading...
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-black text-gray-400">
        Redirecting...
      </div>
    );
  }

  if (!welcomeGateResolved) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-black text-gray-400">
        Redirecting to workspace...
      </div>
    );
  }

  return <AuthedHome user={user} onSignOut={handleSignOut} />;
}

function AuthedHome({
  user,
  onSignOut,
}: {
  user: AuthUser;
  onSignOut: () => void;
}) {
  /* ================= PIPELINE STATE (UPLOAD / SYSTEM) ================= */
  const [uploadPipeline, setUploadPipeline] = useState<{
    percent: number;
    label: string;
  } | null>(null);

  /* ================= STATE ================= */
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [pmlChats, setPmlChats] = useState<ChatSession[]>([]);
  const [pmlActiveId, setPmlActiveId] = useState<string | null>(null);
  const [aiChatsLoaded, setAiChatsLoaded] = useState(false);
  const [pmlChatsLoaded, setPmlChatsLoaded] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("ai");
  const [teamWorkspace, setTeamWorkspace] = useState<TeamWorkspaceState | null>(null);
  const [teamLoading, setTeamLoading] = useState(false);
  const [teamError, setTeamError] = useState<string | null>(null);
  const [devSettings, setDevSettings] = useState<any>(null);
  const readStateKey = useMemo(
    () =>
      `${CHAT_READ_KEY_PREFIX}:${(user.username || user.email || "default")
        .trim()
        .toLowerCase()}`,
    [user.email, user.username]
  );
  const workspaceModeKey = useMemo(
    () =>
      `${WORKSPACE_MODE_KEY_PREFIX}:${(user.username || user.email || "default")
        .trim()
        .toLowerCase()}`,
    [user.email, user.username]
  );
  const sidebarOpenKey = useMemo(
    () =>
      `${SIDEBAR_OPEN_KEY_PREFIX}:${(user.username || user.email || "default")
        .trim()
        .toLowerCase()}`,
    [user.email, user.username]
  );
  const teamMemberId = useMemo(() => getTeamMemberId(user), [user]);
  const [chatReadAt, setChatReadAt] = useState<Record<string, number>>({});
  const [readStateLoaded, setReadStateLoaded] = useState(false);
  const [sidebarPrefLoaded, setSidebarPrefLoaded] = useState(false);

  const [sidebarMetadataRequest, setSidebarMetadataRequest] = useState<{
    jobId: string;
    fields: MetadataRequestField[];
    filename: string;
  } | null>(null);
  const [uploadCancelState, setUploadCancelState] = useState<UploadCancelState | null>(null);
  const [uploadCancelBusy, setUploadCancelBusy] = useState(false);
  const [pendingIngestion, setPendingIngestion] = useState<
    PendingIngestionPollItem[]
  >([]);

  const [showStartup, setShowStartup] = useState(true);
  const [showGettingStarted, setShowGettingStarted] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);
  const [teamSidePanel, setTeamSidePanel] = useState<
    "none" | "notifications" | "history" | "aiAssist"
  >("none");
  const [teamAiSeed, setTeamAiSeed] = useState<string | null>(null);
  const [teamPanel, setTeamPanel] = useState<"threads" | "chat" | "projects">("chat");
  const [selectedTeamProjectId, setSelectedTeamProjectId] = useState<string | null>(null);
  const [projectSetupRequestId, setProjectSetupRequestId] = useState(0);
  const [pmlCenterTab, setPmlCenterTab] = useState<"editor" | "output">("editor");
  const [showPmlTemplateLibraryMobile, setShowPmlTemplateLibraryMobile] = useState(false);
  const [pmlTemplates, setPmlTemplates] = useState<PmlTemplate[]>([]);
  const [pmlTemplatesLoading, setPmlTemplatesLoading] = useState(false);
  const [pmlTemplatesError, setPmlTemplatesError] = useState<string | null>(null);
  const [pmlTemplateDeletingId, setPmlTemplateDeletingId] = useState<string | null>(null);

  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);
  const pmlChatsRef = useRef<ChatSession[]>([]);
  const teamSocketRef = useRef<WebSocket | null>(null);
  const teamReconnectTimerRef = useRef<number | null>(null);


  // 🔥 FIX: track upload lifecycle to avoid race
  const uploadSessionRef = useRef<string | null>(null);
  const uploadChatIdRef = useRef<string | null>(null);
  const uploadProgressMsgIdRef = useRef<string | null>(null);
  const uploadFileNameRef = useRef<string | null>(null);
  const metadataSubmitControllerRef = useRef<AbortController | null>(null);
  const pendingIngestionRestoredRef = useRef(false);
  
  
  const createNewChat = useCallback(() => {
    const newChat: ChatSession = {
      id: uuidv4(),
      title: "New Chat",
      messages: [],
      model: "lite",
      pinned: false,
    };
    setChats((prev) => [newChat, ...prev]);
    setActiveId(newChat.id);
  }, []);

  const createNewPmlChat = useCallback(() => {
    const newChat: ChatSession = {
      id: uuidv4(),
      title: "New Chat",
      messages: [],
      model: "base",
      pinned: false,
    };
    setPmlChats((prev) => [newChat, ...prev]);
    setPmlActiveId(newChat.id);
    setPmlCenterTab("editor");
  }, []);

  const closeGettingStarted = useCallback(() => {
    setShowGettingStarted(false);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("chat_ui_onboarding_seen", "1");
    }
  }, []);

  const triggerUpload = useCallback(() => {
    const chatInput = document.querySelector(
      'input[type="file"][data-upload-id="chat"]'
    ) as HTMLInputElement | null;

    if (chatInput && !chatInput.disabled) {
      chatInput.click();
      return;
    }

    const sidebarInput = document.querySelector(
      'input[type="file"][data-upload-id="sidebar"]'
    ) as HTMLInputElement | null;

    if (sidebarInput && !sidebarInput.disabled) {
      sidebarInput.click();
    }
  }, []);

  /* ================= LOAD / SAVE ================= */

  useEffect(() => {
    setAiChatsLoaded(false);
    const loaded = loadChats();
    setChats(loaded);
    setActiveId(null);
    setAiChatsLoaded(true);
  }, []);

  useEffect(() => {
    if (!aiChatsLoaded) return;
    saveChats(chats);
  }, [chats, aiChatsLoaded]);

  useEffect(() => {
    if (!aiChatsLoaded || !pendingIngestionRestoredRef.current) return;
    savePendingIngestionItems(pendingIngestion);
  }, [pendingIngestion, aiChatsLoaded]);

  useEffect(() => {
    setPmlChatsLoaded(false);
    const loaded = loadPmlChats();
    setPmlChats(loaded);
    pmlChatsRef.current = loaded;
    if (loaded.length > 0) {
      setPmlActiveId(loaded[0].id);
    } else {
      setPmlActiveId(null);
    }
    setPmlChatsLoaded(true);
  }, []);

  useEffect(() => {
    if (!pmlChatsLoaded) return;
    pmlChatsRef.current = pmlChats;
    savePmlChats(pmlChats);
  }, [pmlChats, pmlChatsLoaded]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = window.localStorage.getItem(workspaceModeKey);
    if (raw === "team") {
      setWorkspaceMode("team");
      return;
    }
    if (raw === "pml") {
      setWorkspaceMode("pml");
      return;
    }
    setWorkspaceMode("ai");
  }, [workspaceModeKey]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const raw = window.localStorage.getItem(sidebarOpenKey);
      if (raw === "1") {
        setSidebarOpen(true);
      } else if (raw === "0") {
        setSidebarOpen(false);
      } else {
        setSidebarOpen(false);
      }
    } finally {
      setSidebarPrefLoaded(true);
    }
  }, [sidebarOpenKey]);

  useEffect(() => {
    if (!sidebarPrefLoaded || typeof window === "undefined") return;
    window.localStorage.setItem(sidebarOpenKey, sidebarOpen ? "1" : "0");
  }, [sidebarOpen, sidebarOpenKey, sidebarPrefLoaded]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(workspaceModeKey, workspaceMode);
  }, [workspaceMode, workspaceModeKey]);

  const loadTeamWorkspaceFromApi = useCallback(async () => {
    setTeamLoading(true);
    setTeamError(null);
    try {
      const workspace = await fetchTeamWorkspace();
      setTeamWorkspace(workspace);
    } catch (err: any) {
      setTeamError(err?.message || "Failed to load team workspace.");
    } finally {
      setTeamLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTeamWorkspaceFromApi();
  }, [loadTeamWorkspaceFromApi, user.username, user.email]);

  useEffect(() => {
    if (!teamWorkspace) {
      setSelectedTeamProjectId(null);
      return;
    }
    if (
      selectedTeamProjectId &&
      teamWorkspace.projects.some((project) => project.id === selectedTeamProjectId)
    ) {
      return;
    }
    const fallbackProject =
      teamWorkspace.projects.find(
        (project) => project.conversationId === teamWorkspace.activeConversationId
      ) || teamWorkspace.projects[0];
    setSelectedTeamProjectId(fallbackProject?.id || null);
  }, [selectedTeamProjectId, teamWorkspace]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    setReadStateLoaded(false);
    try {
      const raw = window.localStorage.getItem(readStateKey);
      const parsed = raw ? JSON.parse(raw) : {};
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        setChatReadAt(parsed as Record<string, number>);
      } else {
        setChatReadAt({});
      }
    } catch {
      setChatReadAt({});
    } finally {
      setReadStateLoaded(true);
    }
  }, [readStateKey]);

  useEffect(() => {
    if (!readStateLoaded || typeof window === "undefined") return;
    window.localStorage.setItem(readStateKey, JSON.stringify(chatReadAt));
  }, [chatReadAt, readStateKey, readStateLoaded]);

  useEffect(() => {
    if (!readStateLoaded) return;
    setChatReadAt((prev) => {
      let changed = false;
      const next = { ...prev };
      const chatIdSet = new Set(chats.map((chat) => chat.id));

      for (const chat of chats) {
        if (typeof next[chat.id] !== "number") {
          next[chat.id] = getIncomingMessageTimestamp(chat);
          changed = true;
        }
      }

      for (const chatId of Object.keys(next)) {
        if (!chatIdSet.has(chatId)) {
          delete next[chatId];
          changed = true;
        }
      }

      return changed ? next : prev;
    });
  }, [chats, readStateLoaded]);

  useEffect(() => {
    const chatIds = new Set(chats.map((c) => c.id));
    setPendingIngestion((prev) => {
      const next = prev.filter((entry) => chatIds.has(entry.chatId));
      return next.length === prev.length ? prev : next;
    });
  }, [chats]);

  useEffect(() => {
    if (!aiChatsLoaded || pendingIngestionRestoredRef.current) return;
    pendingIngestionRestoredRef.current = true;

    const restored = loadPendingIngestionItems();
    if (restored.length === 0) return;

    const chatIds = new Set(chats.map((chat) => chat.id));
    const validItems = restored.filter((item) => chatIds.has(item.chatId));
    if (validItems.length === 0) return;

    setChats((prev) =>
      prev.map((chat) => {
        const chatItems = validItems.filter((item) => item.chatId === chat.id);
        if (chatItems.length === 0) return chat;

        let nextMessages = chat.messages;
        let changed = false;

        for (const item of chatItems) {
          if (nextMessages.some((message) => message.id === item.messageId)) continue;
          changed = true;
          nextMessages = [
            ...nextMessages,
            {
              id: item.messageId,
              role: "assistant",
              content: "",
              createdAt: Date.now(),
              status: "progress",
              progressLabel: DOC_PROCESSING_BACKGROUND_TEXT,
            },
          ];
        }

        return changed ? { ...chat, messages: nextMessages } : chat;
      })
    );

    setPendingIngestion((prev) => {
      const seen = new Set(prev.map((item) => item.id));
      const merged = [...prev];
      for (const item of validItems) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        merged.push(item);
      }
      return merged;
    });
  }, [aiChatsLoaded, chats]);

  /* ================= DEV SETTINGS (ON-DEMAND) ================= */
  const loadDevSettings = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/devtools/settings`, { credentials: "include" });
      if (!res.ok) return;
      const data = await res.json();
      setDevSettings(data);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadDevSettings();
  }, [loadDevSettings]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const seen = window.localStorage.getItem("chat_ui_onboarding_seen");
    if (!seen) setShowGettingStarted(true);
  }, []);

  useEffect(() => {
    if (!aiChatsLoaded) return;

    const preferredItem =
      pendingIngestion.find((item) => item.chatId === activeId) ?? pendingIngestion[0];

    if (!preferredItem?.jobId) {
      setUploadCancelState((prev) =>
        prev && prev.phase === "ingestion" ? null : prev
      );
      return;
    }

    const chat = chats.find((entry) => entry.id === preferredItem.chatId);
    const message = chat?.messages.find((entry) => entry.id === preferredItem.messageId);
    const preferredJobId = preferredItem.jobId;
    const label =
      (message?.progressLabel || "").trim() || DOC_PROCESSING_BACKGROUND_TEXT;

    setUploadCancelState((prev) => {
      if (
        prev &&
        prev.phase === "ingestion" &&
        prev.chatId === preferredItem.chatId &&
        prev.messageId === preferredItem.messageId &&
        prev.jobId === preferredItem.jobId &&
        prev.label === label
      ) {
        return prev;
      }

      return {
        chatId: preferredItem.chatId,
        messageId: preferredItem.messageId,
        jobId: preferredJobId,
        label,
        phase: "ingestion",
      };
    });
  }, [activeId, aiChatsLoaded, chats, pendingIngestion]);

  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === "devtools_settings_updated") {
        loadDevSettings();
      }
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, [loadDevSettings]);

  /* ================= DERIVED ================= */

  const activeChat = chats.find((c) => c.id === activeId);
  const pmlActiveChat = pmlChats.find((c) => c.id === pmlActiveId);
  useEffect(() => {
    if (!readStateLoaded || !activeChat) return;
    const latestIncoming = getIncomingMessageTimestamp(activeChat);
    setChatReadAt((prev) => {
      if ((prev[activeChat.id] || 0) >= latestIncoming) return prev;
      return { ...prev, [activeChat.id]: latestIncoming };
    });
  }, [activeChat, readStateLoaded]);

  const unreadCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const chat of chats) {
      const since = chatReadAt[chat.id] || 0;
      const unread = getIncomingMessageCountSince(chat, since);
      if (unread > 0) counts[chat.id] = unread;
    }
    return counts;
  }, [chats, chatReadAt]);

  const totalUnread = useMemo(
    () => Object.values(unreadCounts).reduce((sum, value) => sum + value, 0),
    [unreadCounts]
  );
  const teamUnreadTotal = useMemo(
    () =>
      teamWorkspace
        ? getTotalTeamUnreadCount(teamWorkspace, teamMemberId)
        : 0,
    [teamMemberId, teamWorkspace]
  );
  const workspaceDocuments = useMemo(
    () => deriveWorkspaceDocuments(chats),
    [chats]
  );

  const teamNotifications = useMemo(() => {
    if (!teamWorkspace) return [];
    const items = teamWorkspace.conversations.flatMap((conversation) => {
      const seenAt = conversation.lastSeenAt[teamMemberId] || 0;
      return conversation.messages
        .filter((message) => message.senderId !== teamMemberId)
        .map((message) => {
          const isSystem = isProjectSystemTeamMessage(message);
          return {
            id: `${conversation.id}:${message.id}`,
            conversationId: conversation.id,
            conversationName: conversation.name,
            content: message.content,
            createdAt: message.createdAt,
            isUnread: message.createdAt > seenAt,
            type: isSystem ? "project" : "message",
          };
        });
    });
    return items
      .sort((a, b) => {
        if (a.isUnread !== b.isUnread) return a.isUnread ? -1 : 1;
        return b.createdAt - a.createdAt;
      })
      .slice(0, 80);
  }, [teamMemberId, teamWorkspace]);
  const teamUnreadNotificationCount = useMemo(
    () => teamNotifications.filter((item) => item.isUnread).length,
    [teamNotifications]
  );
  const documentChangeHistory = useMemo(
    () => [...workspaceDocuments].sort((a, b) => b.lastUpdated - a.lastUpdated).slice(0, 20),
    [workspaceDocuments]
  );

  useEffect(() => {
    if (workspaceMode !== "team") {
      setTeamSidePanel("none");
      setTeamAiSeed(null);
    }
  }, [workspaceMode]);

  useEffect(() => {
    if (workspaceMode !== "pml") {
      setShowPmlTemplateLibraryMobile(false);
    }
  }, [workspaceMode]);

  useEffect(() => {
    if (!aiChatsLoaded) return;
    if (workspaceMode !== "ai" || activeChat) return;
    if (chats.length > 0) {
      setActiveId(chats[0].id);
      return;
    }
    createNewChat();
  }, [workspaceMode, activeChat, chats, createNewChat, aiChatsLoaded]);

  const handleTeamSelectConversation = useCallback(
    async (conversationId: string) => {
      const cleanConversationId = (conversationId || "").trim();
      if (!cleanConversationId) return;

      setTeamWorkspace((prev) => {
        if (!prev) return prev;
        return { ...prev, activeConversationId: cleanConversationId };
      });

      const readResult = await markTeamConversationRead(cleanConversationId);
      setTeamWorkspace((prev) => {
        if (!prev) return prev;
        return updateTeamReadMarker(
          prev,
          cleanConversationId,
          String(readResult.memberId || teamMemberId),
          Number(readResult.readAt || Date.now())
        );
      });
    },
    [teamMemberId]
  );

  const handleTeamProjectSidebarSelect = useCallback(
    (projectId: string) => {
      const cleanProjectId = (projectId || "").trim();
      if (!cleanProjectId || !teamWorkspace) return;
      const project = teamWorkspace.projects.find((item) => item.id === cleanProjectId);
      if (!project) return;

      setWorkspaceMode("team");
      setTeamPanel("chat");
      setTeamSidePanel("none");
      setSelectedTeamProjectId(cleanProjectId);
      void handleTeamSelectConversation(project.conversationId);
    },
    [handleTeamSelectConversation, teamWorkspace]
  );

  const handleTeamSendMessage = useCallback(
    async (payload: { conversationId: string; content: string }) => {
      const result = await sendTeamMessage({
        conversationId: payload.conversationId,
        content: payload.content,
      });
      const message = result?.message as TeamMessage | undefined;
      if (!message) return;

      setTeamWorkspace((prev) => {
        if (!prev) return prev;
        const next = upsertTeamMessage(prev, payload.conversationId, message);
        return {
          ...next,
          activeConversationId: payload.conversationId,
        };
      });
    },
    []
  );

  const handleTeamCreateProject = useCallback(
    async (payload: {
      conversationId: string;
      code: string;
      name: string;
      assigneeIds: string[];
    }) => {
      const result = await createTeamProject({
        conversationId: payload.conversationId,
        code: payload.code,
        name: payload.name,
        assigneeIds: payload.assigneeIds,
      });
      const projectRaw = result?.project as TeamProject | undefined;
      const project = projectRaw
        ? {
            ...projectRaw,
            code: projectRaw.code?.trim() ? projectRaw.code : payload.code,
          }
        : undefined;
      const message = result?.message as TeamMessage | undefined;
      const projectCounter =
        typeof result?.projectCounter === "number" ? result.projectCounter : undefined;

      setTeamWorkspace((prev) => {
        if (!prev || !project) return prev;
        let next = upsertTeamProject(prev, project);
        if (message) {
          next = upsertTeamMessage(next, project.conversationId, message);
        }
        return {
          ...next,
          activeConversationId: project.conversationId,
          projectCounter: projectCounter ?? next.projectCounter,
        };
      });
      if (project) {
        setSelectedTeamProjectId(project.id);
      }
    },
    []
  );

  const handleTeamUpdateProjectAssignees = useCallback(
    async (payload: { projectId: string; assigneeIds: string[] }) => {
      const result = await updateTeamProjectAssignees({
        projectId: payload.projectId,
        assigneeIds: payload.assigneeIds,
      });
      const project = result?.project as TeamProject | undefined;
      const message = result?.message as TeamMessage | undefined;
      if (!project) return;

      setTeamWorkspace((prev) => {
        if (!prev) return prev;
        let next = upsertTeamProject(prev, project);
        if (message) {
          next = upsertTeamMessage(next, project.conversationId, message);
        }
        return {
          ...next,
          activeConversationId: project.conversationId,
        };
      });
      setSelectedTeamProjectId(project.id);
    },
    []
  );

  useEffect(() => {
    let cancelled = false;
    let pingTimer: number | null = null;

    const clearReconnect = () => {
      if (teamReconnectTimerRef.current !== null) {
        window.clearTimeout(teamReconnectTimerRef.current);
        teamReconnectTimerRef.current = null;
      }
    };

    const clearPing = () => {
      if (pingTimer !== null) {
        window.clearInterval(pingTimer);
        pingTimer = null;
      }
    };

    const scheduleReconnect = () => {
      if (cancelled || teamReconnectTimerRef.current !== null) return;
      teamReconnectTimerRef.current = window.setTimeout(() => {
        teamReconnectTimerRef.current = null;
        connect();
      }, 2000);
    };

    const connect = () => {
      if (cancelled) return;
      try {
        const socket = new WebSocket(getTeamWsUrl());
        teamSocketRef.current = socket;

        socket.onopen = () => {
          clearReconnect();
          clearPing();
          setTeamError(null);
          try {
            socket.send(JSON.stringify({ type: "REFRESH_WORKSPACE" }));
          } catch {
            // ignore
          }
          pingTimer = window.setInterval(() => {
            try {
              socket.send(JSON.stringify({ type: "PING" }));
            } catch {
              // ignore
            }
          }, 25000);
        };

        socket.onmessage = (event) => {
          let payload: any = null;
          try {
            payload = JSON.parse(event.data);
          } catch {
            return;
          }
          const eventType = String(payload?.type || "").toUpperCase();
          if (!eventType) return;

          if (eventType === "MESSAGE_CREATED") {
            const conversationId = String(payload.conversationId || "");
            const message = payload.message as TeamMessage | undefined;
            if (!conversationId || !message) return;
            setTeamWorkspace((prev) =>
              prev ? upsertTeamMessage(prev, conversationId, message) : prev
            );
            return;
          }

          if (eventType === "PROJECT_CREATED") {
            const project = payload.project as TeamProject | undefined;
            const message = payload.message as TeamMessage | undefined;
            const counter =
              typeof payload.projectCounter === "number"
                ? payload.projectCounter
                : undefined;
            if (!project) return;
            setTeamWorkspace((prev) => {
              if (!prev) return prev;
              let next = upsertTeamProject(prev, project);
              if (message) {
                next = upsertTeamMessage(next, project.conversationId, message);
              }
              if (counter !== undefined) {
                next = { ...next, projectCounter: counter };
              }
              return next;
            });
            return;
          }

          if (eventType === "PROJECT_UPDATED") {
            const project = payload.project as TeamProject | undefined;
            const message = payload.message as TeamMessage | undefined;
            if (!project) return;
            setTeamWorkspace((prev) => {
              if (!prev) return prev;
              let next = upsertTeamProject(prev, project);
              if (message) {
                next = upsertTeamMessage(next, project.conversationId, message);
              }
              return next;
            });
            return;
          }

          if (eventType === "READ_UPDATED") {
            const conversationId = String(payload.conversationId || "");
            const memberId = String(payload.memberId || "");
            const readAt = Number(payload.readAt || 0);
            if (!conversationId || !memberId || !readAt) return;
            setTeamWorkspace((prev) =>
              prev ? updateTeamReadMarker(prev, conversationId, memberId, readAt) : prev
            );
            return;
          }

          if (eventType === "WORKSPACE_SNAPSHOT") {
            if (payload.workspace) {
              setTeamWorkspace(payload.workspace as TeamWorkspaceState);
            }
            return;
          }

          if (eventType === "ERROR") {
            const message = String(payload.message || "").trim();
            if (message) setTeamError(message);
          }
        };

        socket.onclose = () => {
          clearPing();
          if (!cancelled) {
            scheduleReconnect();
          }
        };

        socket.onerror = () => {
          if (!cancelled) {
            scheduleReconnect();
          }
        };
      } catch {
        scheduleReconnect();
      }
    };

    connect();

    return () => {
      cancelled = true;
      clearReconnect();
      clearPing();
      const socket = teamSocketRef.current;
      teamSocketRef.current = null;
      if (socket && socket.readyState < WebSocket.CLOSING) {
        socket.close();
      }
    };
  }, [user.email, user.username]);

  const visibleChatCount = useMemo(
    () => chats.filter((chat) => chat.messages.length > 0).length,
    [chats]
  );
  const pmlVisibleChatCount = useMemo(
    () => pmlChats.filter((chat) => chat.messages.length > 0).length,
    [pmlChats]
  );

  const activeChatPendingIngestionCount = useMemo(() => {
    if (!activeId) return 0;
    return pendingIngestion.reduce(
      (count, item) => count + (item.chatId === activeId ? 1 : 0),
      0
    );
  }, [activeId, pendingIngestion]);

  const aiIsTyping = Boolean(
    activeChat &&
    !sidebarMetadataRequest &&
    activeChat.messages.some(
      (m) => m.status === "typing" || m.status === "streaming"
    )
  );
  const pmlIsTyping = Boolean(
    pmlActiveChat &&
    pmlActiveChat.messages.some(
      (m) => m.status === "typing" || m.status === "streaming"
    )
  );
  const isTyping = workspaceMode === "pml" ? pmlIsTyping : workspaceMode === "ai" ? aiIsTyping : false;
  /* ================= RESET UPLOAD ON CHAT CHANGE ================= */
useEffect(() => {
  if (!activeChat && !sidebarMetadataRequest) {
    setUploadPipeline(null);
    setUploadCancelState(null);
    uploadSessionRef.current = null;
  }
}, [activeChat, sidebarMetadataRequest]);

  useEffect(() => {
    function isEditableTarget(target: EventTarget | null) {
      if (!target || !(target as HTMLElement).tagName) return false;
      const el = target as HTMLElement;
      const tag = el.tagName.toLowerCase();
      return tag === "input" || tag === "textarea" || el.isContentEditable;
    }

    function onKeyDown(e: KeyboardEvent) {
      if (e.defaultPrevented) return;
      const key = e.key.toLowerCase();
      const meta = e.ctrlKey || e.metaKey;
      const isEditable = isEditableTarget(e.target);

      if (key === "escape") {
        if (showShortcuts) setShowShortcuts(false);
        if (showGettingStarted) closeGettingStarted();
        return;
      }

      if (meta && key === "k") {
        e.preventDefault();
        if (workspaceMode !== "team") {
          chatInputRef.current?.focus();
        }
        return;
      }

      if (meta && e.shiftKey && key === "n") {
        e.preventDefault();
        if (workspaceMode === "ai") {
          createNewChat();
        } else if (workspaceMode === "pml") {
          createNewPmlChat();
        }
        return;
      }

      if (meta && key === "b") {
        e.preventDefault();
        setSidebarOpen((v) => !v);
        return;
      }

      if (meta && e.shiftKey && key === "u") {
        e.preventDefault();
        if (workspaceMode === "ai") {
          triggerUpload();
        }
        return;
      }

      if (!isEditable && key === "?") {
        e.preventDefault();
        setShowShortcuts(true);
        return;
      }

      if (meta && e.shiftKey && key === "h") {
        e.preventDefault();
        setShowGettingStarted(true);
        return;
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    createNewChat,
    createNewPmlChat,
    triggerUpload,
    workspaceMode,
    showShortcuts,
    showGettingStarted,
    closeGettingStarted,
  ]);



  /* ================= ACTIONS ================= */
  const handleDeleteChat = useCallback(
    (id: string) => {
      setChats((prev) => prev.filter((c) => c.id !== id));
      if (activeId === id) setActiveId(null);
    },
    [activeId]
  );

  const handleRenameChat = useCallback((id: string, newTitle: string) => {
    setChats((prev) =>
      prev.map((c) => (c.id === id ? { ...c, title: newTitle } : c))
    );
  }, []);

  const handlePinChat = useCallback((id: string) => {
    setChats((prev) =>
      prev.map((c) => (c.id === id ? { ...c, pinned: !c.pinned } : c))
    );
  }, []);

  const handleDeletePmlChat = useCallback(
    (id: string) => {
      setPmlChats((prev) => {
        const next = prev.filter((chat) => chat.id !== id);
        if (next.length === 0) {
          const fresh: ChatSession = {
            id: uuidv4(),
            title: "New Chat",
            messages: [],
            model: "base",
            pinned: false,
          };
          setPmlActiveId(fresh.id);
          return [fresh];
        }
        if (pmlActiveId === id) setPmlActiveId(next[0].id);
        return next;
      });
    },
    [pmlActiveId]
  );

  const handleRenamePmlChat = useCallback((id: string, newTitle: string) => {
    setPmlChats((prev) =>
      prev.map((chat) => (chat.id === id ? { ...chat, title: newTitle } : chat))
    );
  }, []);

  const handlePinPmlChat = useCallback((id: string) => {
    setPmlChats((prev) =>
      prev.map((chat) => (chat.id === id ? { ...chat, pinned: !chat.pinned } : chat))
    );
  }, []);

  const handleModelChange = useCallback(
    (id: string, model: ChatUIModelId) => {
      setChats((prev) =>
        prev.map((c) => (c.id === id ? { ...c, model } : c))
      );
    },
    []
  );

  const updateMessagesForPmlChat = useCallback(
    (
      chatId: string,
      updater: Message[] | ((prev: Message[]) => Message[])
    ) => {
      setPmlChats((prev) =>
        prev.map((chat) => {
          if (chat.id !== chatId) return chat;
          const next =
            typeof updater === "function" ? updater(chat.messages) : updater;
          return { ...chat, messages: next };
        })
      );
    },
    []
  );

  const updatePmlMessages = useCallback(
    (updater: Message[] | ((prev: Message[]) => Message[])) => {
      if (!pmlActiveId) return;
      updateMessagesForPmlChat(pmlActiveId, updater);
    },
    [pmlActiveId, updateMessagesForPmlChat]
  );

  const handlePmlStreamOverride = useCallback(
    async (
      payload: { session_id: string; question: string; mode: "lite" | "base" | "net" },
      signal?: AbortSignal
    ) => {
      const chat = pmlChatsRef.current.find((c) => c.id === payload.session_id);
      const history = (chat?.messages || [])
        .filter(
          (m) =>
            (m.role === "user" || m.role === "assistant") &&
            (m.content || "").trim()
        )
        .slice(-8)
        .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));

      return streamPmlChat(
        {
          session_id: payload.session_id,
          question: payload.question,
          history,
        },
        signal
      );
    },
    []
  );

  const handlePmlTitleOverride = useCallback(async (question: string) => {
    const trimmed = (question || "").trim();
    if (!trimmed) return "New Chat";
    return trimmed.length > 40 ? `${trimmed.slice(0, 40)}...` : trimmed;
  }, []);

  const loadPmlTemplateLibrary = useCallback(async () => {
    setPmlTemplatesLoading(true);
    setPmlTemplatesError(null);
    try {
      const res = await fetchPmlTemplates(120);
      const list = Array.isArray(res?.templates) ? res.templates : [];
      setPmlTemplates(list);
    } catch (err: any) {
      setPmlTemplatesError(err?.message || "Failed to load templates.");
    } finally {
      setPmlTemplatesLoading(false);
    }
  }, []);

  const handleDeletePmlTemplate = useCallback(
    async (templateId: string) => {
      const cleanId = (templateId || "").trim();
      if (!cleanId) return;

      setPmlTemplateDeletingId(cleanId);
      setPmlTemplatesError(null);
      try {
        await deletePmlTemplateApi(cleanId);
        setPmlTemplates((prev) => prev.filter((item) => item.id !== cleanId));
      } catch (err: any) {
        setPmlTemplatesError(err?.message || "Failed to delete template.");
      } finally {
        setPmlTemplateDeletingId(null);
      }
    },
    []
  );

  const handleSavePmlTemplate = useCallback(
    async (content: string) => {
      const code = (content || "").trim();
      if (!code) {
        throw new Error("No code found to save.");
      }

      const note =
        pmlActiveChat && pmlActiveChat.title && pmlActiveChat.title !== "New Chat"
          ? `chat:${pmlActiveChat.title}`
          : "pml-ui";

      await learnPmlTemplate({ code, note });
      await loadPmlTemplateLibrary();
    },
    [pmlActiveChat, loadPmlTemplateLibrary]
  );

  const handleAiSelectChat = useCallback((id: string) => {
    setActiveId(id);
  }, []);

  const handlePmlSelectChat = useCallback((id: string) => {
    setPmlActiveId(id);
    setPmlCenterTab("editor");
  }, []);

  const handleAiNewChat = useCallback(() => {
    createNewChat();
  }, [createNewChat]);

  const handlePmlNewChat = useCallback(() => {
    createNewPmlChat();
    setPmlCenterTab("editor");
  }, [createNewPmlChat]);

  /* ================= MESSAGE UPDATER ================= */

  const updateMessagesForChat = useCallback(
    (
      chatId: string,
      updater: Message[] | ((prev: Message[]) => Message[])
    ) => {
      setChats((prev) =>
        prev.map((c) => {
          if (c.id !== chatId) return c;

          const next =
            typeof updater === "function" ? updater(c.messages) : updater;

          // ✅ DO NOT NORMALIZE DURING STREAM
          return { ...c, messages: next };
        })
      );
    },
    []
  );

  const updateMessages = useCallback(
    (updater: Message[] | ((prev: Message[]) => Message[])) => {
      if (!activeId) return;
      updateMessagesForChat(activeId, updater);
    },
    [activeId, updateMessagesForChat]
  );

  const addPendingIngestion = useCallback(
    (item: {
      jobId?: string | null;
      sessionId?: string | null;
      chatId: string;
      messageId: string;
    }) => {
      const cleanJobId = (item.jobId || "").trim() || null;
      const cleanSessionId = (item.sessionId || "").trim() || null;
      if (!cleanJobId && !cleanSessionId) return;

      const keyBase = cleanJobId ? `job:${cleanJobId}` : `session:${cleanSessionId}`;
      const id = `${keyBase}:msg:${item.messageId}`;

      setPendingIngestion((prev) => {
        if (prev.some((entry) => entry.id === id)) return prev;
        return [
          ...prev,
          {
            id,
            jobId: cleanJobId,
            sessionId: cleanSessionId,
            chatId: item.chatId,
            messageId: item.messageId,
          },
        ];
      });
    },
    []
  );

  const clearPendingById = useCallback((id: string) => {
    setPendingIngestion((prev) => {
      const next = prev.filter((entry) => entry.id !== id);
      return next.length === prev.length ? prev : next;
    });
  }, []);

  const clearPendingForMessage = useCallback(
    (chatId: string | null, messageId: string | null) => {
      if (!chatId || !messageId) return;
      setPendingIngestion((prev) => {
        const next = prev.filter(
          (entry) => !(entry.chatId === chatId && entry.messageId === messageId)
        );
        return next.length === prev.length ? prev : next;
      });
    },
    []
  );

  const handleIngestionReady = useCallback(
    (
      item: PendingIngestionPollItem,
      _status: UploadIngestionStatusResponse
    ) => {
      void _status;
      clearPendingById(item.id);
      setUploadCancelState((prev) =>
        prev && prev.jobId === (item.jobId || "") ? null : prev
      );
      updateMessagesForChat(item.chatId, (prev) =>
        prev.map((m) =>
          m.id === item.messageId
            ? {
                ...m,
                status: "done",
                content: DOC_READY_TEXT,
                progress: undefined,
                progressLabel: undefined,
              }
            : m
        )
      );
    },
    [clearPendingById, updateMessagesForChat]
  );

  const handleIngestionProgress = useCallback(
    (
      item: PendingIngestionPollItem,
      status: UploadIngestionStatusResponse
    ) => {
      const rawProgress =
        typeof status.progress === "number" ? status.progress : undefined;
      const progress =
        rawProgress === undefined
          ? 55
          : Math.max(0, Math.min(99, Math.round(rawProgress)));
      const label =
        (status.progress_label || "").trim() ||
        (status.message || "").trim() ||
        DOC_PROCESSING_BACKGROUND_TEXT;

      if (item.jobId) {
        setUploadCancelState((prev) => {
          if (!prev || prev.jobId !== item.jobId) return prev;
          return {
            ...prev,
            phase: "ingestion",
            label,
          };
        });
      }

      updateMessagesForChat(item.chatId, (prev) =>
        prev.map((m) =>
          m.id === item.messageId
            ? {
                ...m,
                status: "progress",
                content: "",
                progress,
                progressLabel: label,
              }
            : m
        )
      );
    },
    [updateMessagesForChat]
  );

  const handleIngestionError = useCallback(
    (
      item: PendingIngestionPollItem,
      status: UploadIngestionStatusResponse
    ) => {
      clearPendingById(item.id);
      setUploadCancelState((prev) =>
        prev && prev.jobId === (item.jobId || "") ? null : prev
      );
      const detail = (status.error || status.message || "").trim();
      const message = detail
        ? `${DOC_ERROR_TEXT_PREFIX}: ${detail}`
        : DOC_ERROR_TEXT_PREFIX;

      updateMessagesForChat(item.chatId, (prev) =>
        prev.map((m) =>
          m.id === item.messageId
            ? {
                ...m,
                status: "error",
                content: message,
                progress: undefined,
                progressLabel: undefined,
              }
            : m
        )
      );
    },
    [clearPendingById, updateMessagesForChat]
  );

  useIngestionStatusPoller({
    pending: pendingIngestion,
    onReady: handleIngestionReady,
    onProgress: handleIngestionProgress,
    onError: handleIngestionError,
    intervalMs: 2500,
  });

  const shortcuts = useMemo(
    () => [
      { keys: "Ctrl/Cmd + K", label: "Focus message box" },
      { keys: "Ctrl/Cmd + Shift + N", label: "New chat" },
      { keys: "Ctrl/Cmd + B", label: "Toggle sidebar" },
      { keys: "Ctrl/Cmd + Shift + U", label: "Upload PDF" },
      { keys: "Shift + /", label: "Show shortcuts" },
      { keys: "Ctrl/Cmd + Shift + H", label: "Getting started" },
      { keys: "Esc", label: "Close dialogs" },
    ],
    []
  );


  /* ================= SIDEBAR UPLOAD ================= */

  const mapCommitProgress = (raw: number) => {
    const clamped = Math.min(100, Math.max(0, raw));
    return Math.round(40 + (clamped / 100) * 60);
  };

  const isUploadCancelError = (error: unknown) => {
    const name = String((error as any)?.name || "").trim();
    const message = String((error as any)?.message || "")
      .trim()
      .toLowerCase();
    return (
      name === "AbortError" ||
      message.includes("cancelled by user") ||
      message.includes("upload cancelled") ||
      message.includes("aborted")
    );
  };

  const clearUploadCancelState = useCallback(() => {
    setUploadCancelState(null);
    setUploadCancelBusy(false);
  }, []);

  const markUploadCancelledMessage = useCallback(
    ({
      chatId,
      messageId,
      text,
    }: {
      chatId: string | null;
      messageId: string | null;
      text: string;
    }) => {
      if (!chatId) return;
      updateMessagesForChat(chatId, (prev) =>
        prev.some((m) => m.id === messageId)
          ? prev.map((m) =>
              m.id === messageId
                ? {
                    ...m,
                    role: "assistant",
                    status: "done",
                    content: text,
                    progress: undefined,
                    progressLabel: undefined,
                  }
                : m
            )
          : [
              ...prev,
              {
                id: uuidv4(),
                role: "assistant",
                content: text,
                createdAt: Date.now(),
                status: "done",
              },
            ]
      );
    },
    [updateMessagesForChat]
  );

  const handleCancelActiveUpload = useCallback(async () => {
    if (!uploadCancelState || uploadCancelBusy) return;

    setUploadCancelBusy(true);

    metadataSubmitControllerRef.current?.abort();
    metadataSubmitControllerRef.current = null;

    try {
      const response = await cancelUploadJob({
        job_id: uploadCancelState.jobId,
        session_id: uploadCancelState.chatId,
        purge_saved_data: true,
      });

      clearPendingForMessage(uploadCancelState.chatId, uploadCancelState.messageId);
      setSidebarMetadataRequest(null);
      setUploadPipeline(null);
      uploadSessionRef.current = null;
      uploadChatIdRef.current = null;
      uploadProgressMsgIdRef.current = null;
      uploadFileNameRef.current = null;

      markUploadCancelledMessage({
        chatId: uploadCancelState.chatId,
        messageId: uploadCancelState.messageId,
        text:
          response.message ||
          "Upload cancelled. Partial preprocessing and ingestion data were removed.",
      });
      clearUploadCancelState();
    } catch (err: any) {
      setUploadCancelBusy(false);
      const message = err?.message || "Failed to cancel upload";
      markUploadCancelledMessage({
        chatId: uploadCancelState.chatId,
        messageId: uploadCancelState.messageId,
        text: message,
      });
    }
  }, [
    clearPendingForMessage,
    clearUploadCancelState,
    markUploadCancelledMessage,
    uploadCancelBusy,
    uploadCancelState,
  ]);

  const handleSidebarUploadStart = (file: File) => {
    if (!activeId) return;

    clearUploadCancelState();
    uploadChatIdRef.current = activeId;
    uploadFileNameRef.current = file.name;
    uploadSessionRef.current = uuidv4();

    const progressMsgId = uuidv4();
    uploadProgressMsgIdRef.current = progressMsgId;

    // If this is a fresh chat, name it after the uploaded document.
    if (activeChat && activeChat.id === activeId && activeChat.messages.length === 0) {
      handleRenameChat(activeId, file.name.replace(/\.pdf$/i, ""));
    }

    // ChatGPT-style: user "uploads" + assistant starts processing immediately
    updateMessagesForChat(activeId, (prev) => [
      ...prev,
      {
        id: uuidv4(),
        role: "user",
        content: `Uploaded PDF: ${file.name}`,
        createdAt: Date.now(),
        status: "done",
      },
      {
        id: progressMsgId,
        role: "assistant",
        content: "",
        createdAt: Date.now(),
        status: "progress",
        progress: 0,
        progressLabel: "Uploading PDF...",
      },
    ]);

    setUploadPipeline({
      percent: 0,
      label: "Uploading PDF...",
    });
  };

  const handleSidebarUploadProgress = (
    _status: UploadStatus,
    percent: number,
    label: string
  ) => {
    setUploadPipeline({
      percent,
      label,
    });

    const chatId = uploadChatIdRef.current;
    const msgId = uploadProgressMsgIdRef.current;
    if (!chatId || !msgId) return;

    updateMessagesForChat(chatId, (prev) =>
      prev.map((m) =>
        m.id === msgId
          ? {
              ...m,
              status: "progress",
              progress: percent,
              progressLabel: label,
            }
          : m
      )
    );
  };

  const handleSidebarUploadSuccess = async (result: any) => {
    if (!activeId) return;
    if (!uploadSessionRef.current) {
    handleSidebarUploadError("Upload session mismatch. Please retry the upload.");
    return;
  }
    
    if (
      result.next_action === "WAIT_FOR_METADATA" ||
      result.next_action === "READY_FOR_PROCESSING" ||
      result.next_action === "READY_TO_COMMIT" ||
      result.next_action === "READY_FOR_COMMIT"
    ) {
      setUploadPipeline(null);

      const chatId = uploadChatIdRef.current ?? activeId;
      const msgId = uploadProgressMsgIdRef.current;
      if (chatId && msgId) {
        updateMessagesForChat(chatId, (prev) =>
          prev.map((m) =>
            m.id === msgId
              ? {
                  ...m,
                  status: "progress",
                  progress: 40,
                  progressLabel: "Review preprocessing preview...",
                }
              : m
          )
        );
      }
      
      const fields: MetadataRequestField[] = Object.entries(result.metadata).map(
        ([key, meta]: [string, any]) => ({
          key,
          label: key.replace(/_/g, " ").toUpperCase(),
          value: meta.value || "",
          placeholder: `Enter ${key}...`,
          reason: "Please verify this field",
        })
      );

      setSidebarMetadataRequest({
        jobId: result.job_id,
        fields,
        filename: result.filename,
      });

      if (chatId && msgId) {
        setUploadCancelState({
          chatId,
          messageId: msgId,
          jobId: result.job_id,
          label: "Waiting for metadata details.",
          phase: "metadata",
        });
      }

      return;
    }

  };

  const finalizeUploadSuccess = () => {
    uploadSessionRef.current = null;
    uploadChatIdRef.current = null;
    uploadProgressMsgIdRef.current = null;
    uploadFileNameRef.current = null;
  };

  const handleSidebarUploadError = (errorMsg: string) => {
    metadataSubmitControllerRef.current?.abort();
    metadataSubmitControllerRef.current = null;
    uploadSessionRef.current = null;
    setUploadPipeline(null);
    setSidebarMetadataRequest(null);
    clearUploadCancelState();

    const chatId = uploadChatIdRef.current ?? activeId;
    const msgId = uploadProgressMsgIdRef.current;
    clearPendingForMessage(chatId, msgId);

    if (chatId && msgId) {
      updateMessagesForChat(chatId, (prev) =>
        prev.map((m) =>
          m.id === msgId
            ? {
                ...m,
                role: "assistant",
                status: "error",
                content: errorMsg,
                progress: undefined,
                progressLabel: undefined,
              }
            : m
        )
      );
    } else if (chatId) {
      updateMessagesForChat(chatId, (prev) => [
        ...prev,
        {
          id: uuidv4(),
          role: "system",
          content: errorMsg,
          createdAt: Date.now(),
          status: "done",
        },
      ]);
    }

    uploadChatIdRef.current = null;
    uploadProgressMsgIdRef.current = null;
    uploadFileNameRef.current = null;
  };


  // New: handle submission of metadata from the metadata form
  const handleExternalMetadataSubmit = async (
    jobId: string,
    fields: MetadataRequestField[]
  ) => {
    if (!activeId) return;
    if (!uploadSessionRef.current) return;

    if (!Array.isArray(fields)) {
      handleSidebarUploadError("Metadata fields missing. Please retry upload.");
      console.error("[MetadataSubmit] fields is invalid:", fields);
      return;
    }

    const metadata: Record<string, string> = fields.reduce((acc, f) => {
      acc[f.key] =
        typeof f.value === "string" ? f.value : String(f.value ?? "");
      return acc;
    }, {} as Record<string, string>);

    const chatId = uploadChatIdRef.current ?? activeId;
    const msgId = uploadProgressMsgIdRef.current;
    const controller = new AbortController();

    metadataSubmitControllerRef.current = controller;

    try {
      setUploadPipeline({ percent: 40, label: "Submitting metadata..." });
      if (chatId && msgId) {
        setUploadCancelState({
          chatId,
          messageId: msgId,
          jobId,
          label: "Submitting metadata...",
          phase: "preprocessing",
        });
        updateMessagesForChat(chatId, (prev) =>
          prev.map((m) =>
            m.id === msgId
              ? {
                  ...m,
                  status: "progress",
                  progress: 40,
                  progressLabel: "Submitting metadata...",
                }
              : m
          )
        );
      }

      await updateMetadata(
        { job_id: jobId, metadata, force: true },
        (evt) => {
          if (!evt) return;
          const raw = typeof evt.progress === "number" ? evt.progress : 50;
          const pct = mapCommitProgress(raw);
          const lbl = evt.message ?? "Processing document...";

          setUploadPipeline({
            percent: pct,
            label: lbl,
          });

          if (chatId && msgId) {
            setUploadCancelState({
              chatId,
              messageId: msgId,
              jobId,
              label: lbl,
              phase: "preprocessing",
            });
            updateMessagesForChat(chatId, (prev) =>
              prev.map((m) =>
                m.id === msgId
                  ? {
                      ...m,
                      status: "progress",
                      progress: pct,
                      progressLabel: lbl,
                    }
                  : m
              )
            );
          }
        },
        controller.signal
      );

      finalizeUploadSuccess();

      setSidebarMetadataRequest(null);
      uploadSessionRef.current = null;
      setUploadPipeline(null);

      if (chatId && msgId) {
        updateMessagesForChat(chatId, (prev) =>
          prev.map((m) =>
            m.id === msgId
              ? {
                  ...m,
                  status: "progress",
                  content: "",
                  progress: 45,
                  progressLabel: "Queued for background processing...",
                }
              : m
          )
        );

        setUploadCancelState({
          chatId,
          messageId: msgId,
          jobId,
          label: "Queued for background processing...",
          phase: "ingestion",
        });

        addPendingIngestion({
          jobId,
          sessionId: chatId,
          chatId,
          messageId: msgId,
        });
      }
    } catch (err: any) {
      if (isUploadCancelError(err)) {
        return;
      }
      setUploadPipeline(null);
      uploadSessionRef.current = null;
      handleSidebarUploadError(err?.message || "Failed to submit metadata");
    } finally {
      metadataSubmitControllerRef.current = null;
    }
  };



  /* ================= RENDER ================= */
  
  useEffect(() => {
    if (!pmlChatsLoaded) return;
    if (workspaceMode !== "pml") return;

    if (pmlChats.length === 0) {
      handlePmlNewChat();
      return;
    }

    const hasActive = pmlActiveId ? pmlChats.some((chat) => chat.id === pmlActiveId) : false;
    if (!hasActive) {
      setPmlActiveId(pmlChats[0].id);
    }
  }, [workspaceMode, pmlChats, pmlActiveId, handlePmlNewChat, pmlChatsLoaded]);

  useEffect(() => {
    if (workspaceMode !== "pml") return;
    void loadPmlTemplateLibrary();
  }, [workspaceMode, loadPmlTemplateLibrary]);

  const workspaceName = "Kavin Workspace";
  const unassignedProjectCount = useMemo(() => {
    if (!teamWorkspace?.projects?.length) return 0;
    return teamWorkspace.projects.reduce((count, project) => {
      const assignees = Array.isArray(project.assigneeIds) ? project.assigneeIds : [];
      return assignees.length === 0 ? count + 1 : count;
    }, 0);
  }, [teamWorkspace]);
  const latestPmlOutput =
    [...(pmlActiveChat?.messages || [])]
      .reverse()
      .find((message) => message.role === "assistant" && (message.content || "").trim())?.content ||
    "";
  const teamSidePanelOpen = teamSidePanel !== "none";
  const teamSidePanelTitle =
    teamSidePanel === "notifications"
      ? "Notifications"
      : teamSidePanel === "history"
        ? "Document History"
        : "Team AI Assist";
  const teamSidePanelBody =
    teamSidePanel === "notifications" ? (
      <div className="space-y-2">
        {teamNotifications.length === 0 && (
          <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-4 text-xs text-gray-500">
            No team notifications available.
          </div>
        )}
        {teamNotifications.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              setTeamPanel("chat");
              setTeamSidePanel("none");
              void handleTeamSelectConversation(item.conversationId);
            }}
            className={`w-full rounded-xl border px-3 py-3 text-left transition ${
              item.isUnread
                ? "border-white/25 bg-white/5 hover:bg-white/10"
                : "border-white/10 bg-black/20 hover:bg-white/5"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                <span
                  className={`h-2 w-2 rounded-full ${
                    item.isUnread ? "bg-white" : "bg-gray-600"
                  }`}
                />
                <div className="truncate text-sm font-semibold text-white">
                  {item.type === "project" ? "Project update" : "New team message"}
                </div>
              </div>
              <div className="text-[11px] text-gray-400">{formatRelativeTime(item.createdAt)}</div>
            </div>
            <div className="mt-1 text-xs text-gray-300 line-clamp-2">{item.content}</div>
            <div className="mt-1 text-[11px] text-gray-500">
              {item.conversationName} {item.isUnread ? "- Unread" : "- Read"}
            </div>
          </button>
        ))}
      </div>
    ) : teamSidePanel === "history" ? (
      <div className="space-y-2">
        {documentChangeHistory.length === 0 && (
          <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-4 text-xs text-gray-500">
            No document revision events available.
          </div>
        )}
        {documentChangeHistory.map((row) => (
          <div
            key={row.id}
            className="rounded-xl border border-white/10 bg-black/20 px-3 py-2.5 text-xs transition hover:bg-white/5"
          >
            <div className="truncate text-sm font-medium text-gray-100">{row.name}</div>
            <div className="mt-1 flex items-center justify-between gap-2">
              <span className="text-gray-300">{row.revision}</span>
              <span className="truncate text-gray-500">{row.projectName}</span>
            </div>
            <div className="mt-1 text-[11px] text-gray-500">
              {new Date(row.lastUpdated).toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    ) : (
      <div className="space-y-2">
        <div className="rounded-xl border border-white/15 bg-black/20 px-3 py-3 text-xs text-gray-300">
          Team AI Assist is under development.
        </div>
        <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-3 text-xs text-gray-400">
          This feature will be available in a future update.
        </div>
      </div>
    );

  return (
    <div className="flex h-full w-full bg-black text-white">
      <StartupSplash open={showStartup} onDone={() => setShowStartup(false)} />
      {!showStartup && (
        <>
          <GettingStartedModal
            open={showGettingStarted}
            onClose={closeGettingStarted}
            onOpenShortcuts={() => setShowShortcuts(true)}
          />
          <ShortcutsModal
            open={showShortcuts}
            onClose={() => setShowShortcuts(false)}
            shortcuts={shortcuts}
          />
          <Sidebar
        chats={workspaceMode === "pml" ? pmlChats : chats}
        activeId={workspaceMode === "pml" ? pmlActiveId : activeId}
        sessionId={workspaceMode === "pml" ? pmlActiveId : activeId}
        user={user}
        workspaceMode={workspaceMode}
        teamUnreadTotal={teamUnreadTotal}
        unreadCounts={workspaceMode === "pml" ? {} : unreadCounts}
        totalUnread={workspaceMode === "pml" ? 0 : totalUnread}
        onSignOut={onSignOut}
        onSelect={workspaceMode === "pml" ? handlePmlSelectChat : handleAiSelectChat}
        onNew={workspaceMode === "pml" ? handlePmlNewChat : handleAiNewChat}
        onRename={workspaceMode === "pml" ? handleRenamePmlChat : handleRenameChat}
        onDelete={workspaceMode === "pml" ? handleDeletePmlChat : handleDeleteChat}
        onPin={workspaceMode === "pml" ? handlePinPmlChat : handlePinChat}
        onProjectSetupClick={() => {
          setWorkspaceMode("team");
          setTeamPanel("chat");
          setTeamAiSeed(null);
          setTeamSidePanel("none");
          setProjectSetupRequestId((prev) => prev + 1);
        }}
        projectSetupActive={workspaceMode === "team"}
        unassignedProjectCount={unassignedProjectCount}
        teamProjects={teamWorkspace?.projects || []}
        activeTeamProjectId={selectedTeamProjectId}
        onSelectTeamProject={handleTeamProjectSidebarSelect}
        onTeamAiClick={() => {
          setWorkspaceMode("team");
          setTeamPanel("chat");
          setTeamAiSeed(null);
          setTeamSidePanel("aiAssist");
        }}
        teamAiAssistActive={workspaceMode === "team" && teamSidePanel === "aiAssist"}
        isOpen={sidebarOpen}
        onOpen={() => setSidebarOpen(true)}
        onClose={() => setSidebarOpen(false)}
        isTyping={isTyping}
        onUploadStart={handleSidebarUploadStart}
        onUploadSuccess={handleSidebarUploadSuccess}
        onUploadError={handleSidebarUploadError}
        onUploadProgress={handleSidebarUploadProgress}
        showUpload={workspaceMode === "ai"}
        pmlCenterTab={pmlCenterTab}
        onPmlCenterTabChange={setPmlCenterTab}
        pmlTemplateLibraryOpen={showPmlTemplateLibraryMobile}
        onPmlTemplateLibraryToggle={() =>
          setShowPmlTemplateLibraryMobile((prev) => !prev)
        }
      />

          <main
        className={`app-main-shell relative flex h-full min-w-0 flex-1 flex-col ml-10 min-[361px]:ml-11 md:ml-14 transition-[margin-left,transform] duration-320 ease-[cubic-bezier(0.22,1,0.36,1)] will-change-[margin-left,transform] ${
          sidebarOpen ? "app-main-sidebar-open" : ""
        }`}
      >
        <header className="app-main-header h-14 shrink-0 border-b border-transparent bg-black">
          <div className="app-main-header-inner flex h-full items-center px-2 sm:px-4 md:px-6">
            <div className="flex min-w-0 items-center gap-2 sm:gap-3">
              <span className="truncate text-sm font-semibold tracking-wide text-white">
                <span className="max-[360px]:hidden sm:hidden">KAVIN</span>
                <span className="hidden sm:inline">{workspaceName}</span>
              </span>
              <WorkspaceModeSwitcher
                workspaceMode={workspaceMode}
                teamUnreadTotal={teamUnreadTotal}
                onChange={setWorkspaceMode}
              />
            </div>
            {workspaceMode === "pml" && (
              <button
                type="button"
                onClick={() => setShowPmlTemplateLibraryMobile(true)}
                className="ml-auto inline-flex items-center rounded-md border border-white/20 bg-white/5 px-2.5 py-1.5 text-xs font-medium text-gray-200 transition hover:bg-white/10 lg:hidden"
              >
                Templates
              </button>
            )}
          </div>
        </header>

        <div className="min-h-0 flex-1">
          {workspaceMode === "ai" ? (
            activeChat ? (
              <ChatWindow
                messages={activeChat.messages}
                onUpdateMessages={updateMessages}
                model={activeChat.model}
                sessionId={activeChat.id}
                userLabel={user.username}
                userRole={user.role}
                totalChats={visibleChatCount}
                unreadNotifications={totalUnread}
                devSettings={devSettings}
                onModelChange={(m) => handleModelChange(activeChat.id, m)}
                ingestionPollingActive={activeChatPendingIngestionCount > 0}
                ingestionPollingCount={activeChatPendingIngestionCount}
                uploadPipeline={uploadPipeline}
                onRenameSession={(t) => handleRenameChat(activeChat.id, t)}
                onUploadStart={handleSidebarUploadStart}
                onUploadProgress={handleSidebarUploadProgress}
                onUploadSuccess={handleSidebarUploadSuccess}
                onUploadError={handleSidebarUploadError}
                externalMetadataRequest={sidebarMetadataRequest}
                metadataActive={!!sidebarMetadataRequest}
                uploadCancelState={
                  uploadCancelState && uploadCancelState.chatId === activeChat.id
                    ? {
                        messageId: uploadCancelState.messageId,
                        label: uploadCancelState.label,
                        phase: uploadCancelState.phase,
                      }
                    : null
                }
                cancelUploadBusy={uploadCancelBusy}
                onCancelUpload={handleCancelActiveUpload}
                onExternalMetadataSubmit={handleExternalMetadataSubmit}
                inputRefExternal={chatInputRef}
                emptyStateConfig={{ showPmlEntryCard: false, showSummaryCard: false }}
              />
            ) : (
              <div className="flex h-full items-center justify-center text-gray-500">
                Preparing chat...
              </div>
            )
          ) : workspaceMode === "pml" ? (
            <div className="flex h-full min-h-0">
              <div className="min-h-0 flex-1">
                {pmlCenterTab === "output" ? (
                  <div className="h-full overflow-y-auto bg-black px-3 py-4 sm:px-4 md:px-6 md:py-6">
                    <div className="mx-auto max-w-5xl rounded-[14px] border border-white/10 bg-black/20 p-4 shadow-[0_4px_20px_rgba(0,0,0,0.25)] sm:p-6">
                      <div className="mb-3 flex flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <h3 className="text-sm font-semibold text-white">PML Output Panel</h3>
                        <button
                          type="button"
                          onClick={() => setPmlCenterTab("editor")}
                          className="rounded-md border border-white/30 bg-white px-3 py-1.5 text-xs text-black transition-colors hover:bg-gray-200"
                        >
                          Run in Editor
                        </button>
                      </div>
                      {latestPmlOutput ? (
                        <pre className="max-h-[62vh] overflow-auto rounded-lg border border-white/10 bg-black p-3 text-xs text-gray-200">
                          {latestPmlOutput}
                        </pre>
                      ) : (
                        <div className="rounded-lg border border-white/10 bg-black px-3 py-4 text-xs text-gray-500">
                          No generated output yet. Use the code writer to create PML code.
                        </div>
                      )}
                    </div>
                  </div>
                ) : pmlActiveChat ? (
                  <ChatWindow
                    messages={pmlActiveChat.messages}
                    onUpdateMessages={updatePmlMessages}
                    model={"base"}
                    sessionId={pmlActiveChat.id}
                    userLabel={user.username}
                    userRole={user.role}
                    totalChats={pmlVisibleChatCount}
                    unreadNotifications={0}
                    title={pmlActiveChat.title}
                    onRenameSession={(t) => handleRenamePmlChat(pmlActiveChat.id, t)}
                    onModelChange={() => {}}
                    streamChatOverride={handlePmlStreamOverride}
                    showUpload={false}
                    showSources={false}
                    lockModelSelector
                    lockedModelLabel="PML Code Writer"
                    disableMetadataWorkflow
                    emptyStateConfig={{
                      dashboardTitle: `Welcome to PML Assistant, ${user.username || "User"}`,
                      dashboardSubtitle: `Role: ${getRoleLabel(user.role)} - code writing mode only.`,
                      showSummaryCard: false,
                      heroTitle: "What PML code do you want to build?",
                      heroSubtitle: "Describe the logic, forms, macros, or validations you need.",
                      showPmlEntryCard: false,
                      prompts: [],
                    }}
                    inputPlaceholderText="Describe the PML code you need..."
                    disclaimerText="Generated code can contain mistakes. Validate before production use."
                    generateTitleOverride={handlePmlTitleOverride}
                    inputRefExternal={chatInputRef}
                    onSaveLatestAssistant={handleSavePmlTemplate}
                    saveLatestAssistantLabel="Save as PML Template"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-gray-500">
                    Preparing PML workspace...
                  </div>
                )}
              </div>
              <TemplateLibraryPanel
                templates={pmlTemplates}
                loading={pmlTemplatesLoading}
                error={pmlTemplatesError}
                deletingId={pmlTemplateDeletingId}
                onRefresh={() => {
                  void loadPmlTemplateLibrary();
                }}
                onDelete={handleDeletePmlTemplate}
              />
              {showPmlTemplateLibraryMobile && (
                <>
                  <button
                    type="button"
                    className="fixed inset-0 z-40 bg-black/60 lg:hidden"
                    onClick={() => setShowPmlTemplateLibraryMobile(false)}
                    aria-label="Close template library backdrop"
                  />
                  <aside className="fixed inset-y-14 right-0 z-50 flex w-full max-w-sm flex-col border-l border-transparent bg-black lg:hidden">
                    <TemplateLibraryPanel
                      mode="mobile"
                      templates={pmlTemplates}
                      loading={pmlTemplatesLoading}
                      error={pmlTemplatesError}
                      deletingId={pmlTemplateDeletingId}
                      onRefresh={() => {
                        void loadPmlTemplateLibrary();
                      }}
                      onDelete={handleDeletePmlTemplate}
                      onClose={() => setShowPmlTemplateLibraryMobile(false)}
                    />
                  </aside>
                </>
              )}
            </div>
          ) : (
            <div className="relative flex h-full min-h-0 flex-col">
              <div className="border-b border-transparent bg-black px-2.5 py-2.5 sm:px-4 md:px-6">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="inline-flex items-center gap-2 rounded-lg border border-white/15 bg-white/5 px-2.5 py-1.5 text-xs font-semibold text-white sm:px-3 sm:py-2 sm:text-base">
                    <Users className="h-4 w-4 sm:h-5 sm:w-5" />
                    <span className="sm:hidden">Team</span>
                    <span className="hidden sm:inline">Team Workspace</span>
                  </div>
                  <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        setTeamSidePanel((prev) =>
                          prev === "notifications" ? "none" : "notifications"
                        )
                      }
                      title="Notifications"
                      aria-label="Notifications"
                      className={`relative inline-flex h-9 items-center gap-1.5 rounded-lg border px-2.5 transition sm:h-11 sm:gap-2 sm:px-3 ${
                        teamSidePanel === "notifications"
                          ? "border-white bg-white text-black"
                          : "border-white/15 bg-white/5 text-gray-200 hover:bg-white/10 hover:text-white"
                      }`}
                    >
                      <Bell className="h-4 w-4 sm:h-5 sm:w-5" />
                      <span className="hidden text-sm font-medium sm:inline">Notifications</span>
                      {teamUnreadNotificationCount > 0 && (
                        <span className="absolute -right-1 -top-1 inline-flex min-w-[18px] items-center justify-center rounded-full bg-white px-1 text-[10px] font-semibold text-black">
                          {teamUnreadNotificationCount > 9 ? "9+" : teamUnreadNotificationCount}
                        </span>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setTeamSidePanel((prev) => (prev === "history" ? "none" : "history"))
                      }
                      title="Document History"
                      aria-label="Document History"
                      className={`inline-flex h-9 items-center gap-1.5 rounded-lg border px-2.5 transition sm:h-11 sm:gap-2 sm:px-3 ${
                        teamSidePanel === "history"
                          ? "border-white bg-white text-black"
                          : "border-white/15 bg-white/5 text-gray-200 hover:bg-white/10 hover:text-white"
                      }`}
                    >
                      <FileClock className="h-4 w-4 sm:h-5 sm:w-5" />
                      <span className="hidden text-sm font-medium sm:inline">History</span>
                    </button>
                  </div>
                </div>
              </div>
              <div className="min-h-0 flex-1">
                {teamWorkspace ? (
                  <div className="flex h-full min-h-0 overflow-hidden">
                    <div className="min-h-0 min-w-0 flex-1 overflow-hidden">
                      <EnterpriseMessagingWorkspace
                        user={user}
                        workspace={teamWorkspace}
                        activeProjectId={selectedTeamProjectId}
                        panel={teamPanel}
                        onPanelChange={setTeamPanel}
                        projectSetupRequestId={projectSetupRequestId}
                        aiAssistSeed={teamAiSeed}
                        onAiAssistSeedConsumed={() => setTeamAiSeed(null)}
                        busy={teamLoading}
                        error={teamError}
                        onSelectConversation={handleTeamSelectConversation}
                        onSendMessage={handleTeamSendMessage}
                        onCreateProject={handleTeamCreateProject}
                        onUpdateProjectAssignees={handleTeamUpdateProjectAssignees}
                      />
                    </div>

                    {teamSidePanelOpen && (
                      <aside className="hidden h-full w-[min(36vw,420px)] max-w-[420px] shrink-0 flex-col border-l border-transparent bg-black/40 lg:flex">
                        <div className="flex items-center justify-between border-b border-transparent px-4 py-3">
                          <div className="text-sm font-semibold text-white">{teamSidePanelTitle}</div>
                          <button
                            type="button"
                            onClick={() => setTeamSidePanel("none")}
                            className="rounded-md border border-white/15 bg-white/5 p-1.5 text-gray-200 hover:bg-white/10"
                            aria-label="Close panel"
                          >
                            <X className="h-4 w-4" />
                          </button>
                        </div>
                        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
                          {teamSidePanelBody}
                        </div>
                      </aside>
                    )}
                  </div>
                ) : (
                  <div className="flex h-full items-center justify-center text-gray-500">
                    {teamLoading ? "Loading team workspace..." : teamError || "Team workspace unavailable."}
                  </div>
                )}
              </div>

              {teamSidePanelOpen && (
                <>
                  <button
                    type="button"
                    className="fixed inset-0 z-40 bg-black/60 lg:hidden"
                    onClick={() => setTeamSidePanel("none")}
                    aria-label="Close side panel backdrop"
                  />
                  <aside className="team-mobile-sidepanel fixed inset-y-14 right-0 z-50 flex w-full max-w-sm flex-col border-l border-transparent bg-black lg:hidden">
                    <div className="flex items-center justify-between border-b border-transparent px-4 py-3">
                      <div className="text-sm font-semibold text-white">{teamSidePanelTitle}</div>
                      <button
                        type="button"
                        onClick={() => setTeamSidePanel("none")}
                        className="rounded-md border border-white/15 bg-white/5 p-1.5 text-gray-200 hover:bg-white/10"
                        aria-label="Close panel"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>
                    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
                      {teamSidePanelBody}
                    </div>
                  </aside>
                </>
              )}
            </div>
          )}
        </div>
          </main>
        </>
      )}
    </div>
  );
}





