// frontend/app/page.tsx
"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import Sidebar from "@/app/components/sidebar/Sidebar";
import ChatWindow from "@/app/components/chat/ChatWindow";
import EnterpriseMessagingWorkspace from "@/app/components/messaging/EnterpriseMessagingWorkspace";
import StartupSplash from "@/app/components/StartupSplash";
import GettingStartedModal from "@/app/components/onboarding/GettingStartedModal";
import ShortcutsModal from "@/app/components/onboarding/ShortcutsModal";
import { ChatSession, Message } from "@/app/lib/types";
import { KavinModelId } from "@/app/lib/kavin-models";
import { loadChats, saveChats } from "@/app/lib/chat-store";
import { authLogout, authMe, updateMetadata } from "@/app/lib/api";
import type { AuthUser, UploadIngestionStatusResponse } from "@/app/lib/api";
import {
  getTeamMemberId,
  getTotalTeamUnreadCount,
  loadTeamWorkspace,
  saveTeamWorkspace,
  type TeamWorkspaceState,
  type WorkspaceMode,
} from "@/app/lib/enterprise-messaging";
import { MetadataRequestField } from "@/app/lib/llm-ui-events";
import { UploadStatus } from "@/app/hooks/useSmartUpload";
import {
  useIngestionStatusPoller,
  type PendingIngestionPollItem,
} from "@/app/hooks/useIngestionStatusPoller";
import { API_BASE } from "@/app/lib/config";

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
const CHAT_READ_KEY_PREFIX = "kavin-chat-read-at";
const WELCOME_SEEN_KEY_PREFIX = "kavin-welcome-seen";
const WORKSPACE_MODE_KEY_PREFIX = "kavin-workspace-mode";

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
        Redirecting to dashboard...
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
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("ai");
  const [teamWorkspace, setTeamWorkspace] = useState<TeamWorkspaceState | null>(null);
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
  const teamMemberId = useMemo(() => getTeamMemberId(user), [user]);
  const [chatReadAt, setChatReadAt] = useState<Record<string, number>>({});
  const [readStateLoaded, setReadStateLoaded] = useState(false);

  const [sidebarMetadataRequest, setSidebarMetadataRequest] = useState<{
    jobId: string;
    fields: MetadataRequestField[];
    filename: string;
  } | null>(null);
  const [pendingIngestion, setPendingIngestion] = useState<
    PendingIngestionPollItem[]
  >([]);

  const [showStartup, setShowStartup] = useState(true);
  const [showGettingStarted, setShowGettingStarted] = useState(false);
  const [showShortcuts, setShowShortcuts] = useState(false);

  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);


  // 🔥 FIX: track upload lifecycle to avoid race
  const uploadSessionRef = useRef<string | null>(null);
  const uploadChatIdRef = useRef<string | null>(null);
  const uploadProgressMsgIdRef = useRef<string | null>(null);
  const uploadFileNameRef = useRef<string | null>(null);
  
  
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
    if (window.innerWidth < 768) setSidebarOpen(false);
  }, []);

  const closeGettingStarted = useCallback(() => {
    setShowGettingStarted(false);
    if (typeof window !== "undefined") {
      window.localStorage.setItem("kavin_onboarding_seen", "1");
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
    const loaded = loadChats();
    setChats(loaded);
    if (loaded.length > 0) {
      setActiveId(loaded[0].id);
    } else {
      createNewChat();
    }
  }, [createNewChat]);

  useEffect(() => {
    if (chats.length > 0) saveChats(chats);
  }, [chats]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = window.localStorage.getItem(workspaceModeKey);
    setWorkspaceMode(raw === "team" ? "team" : "ai");
  }, [workspaceModeKey]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(workspaceModeKey, workspaceMode);
  }, [workspaceMode, workspaceModeKey]);

  useEffect(() => {
    setTeamWorkspace(loadTeamWorkspace(user));
  }, [user]);

  useEffect(() => {
    if (!teamWorkspace) return;
    saveTeamWorkspace(user, teamWorkspace);
  }, [teamWorkspace, user]);

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
    const seen = window.localStorage.getItem("kavin_onboarding_seen");
    if (!seen) setShowGettingStarted(true);
  }, []);

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

  const visibleChatCount = useMemo(
    () => chats.filter((chat) => chat.messages.length > 0).length,
    [chats]
  );

  const activeChatPendingIngestionCount = useMemo(() => {
    if (!activeId) return 0;
    return pendingIngestion.reduce(
      (count, item) => count + (item.chatId === activeId ? 1 : 0),
      0
    );
  }, [activeId, pendingIngestion]);

  const isTyping = Boolean(
    activeChat &&
    !sidebarMetadataRequest &&
    activeChat.messages.some(
      (m) => m.status === "typing" || m.status === "streaming"
    )
  );
  /* ================= RESET UPLOAD ON CHAT CHANGE ================= */
  useEffect(() => {
  if (!activeChat && !sidebarMetadataRequest) {
    setUploadPipeline(null);
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
        if (workspaceMode === "ai") {
          chatInputRef.current?.focus();
        }
        return;
      }

      if (meta && e.shiftKey && key === "n") {
        e.preventDefault();
        if (workspaceMode === "ai") {
          createNewChat();
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

  const handleModelChange = useCallback(
    (id: string, model: KavinModelId) => {
      setChats((prev) =>
        prev.map((c) => (c.id === id ? { ...c, model } : c))
      );
    },
    []
  );

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

  const handleSidebarUploadStart = (file: File) => {
    if (!activeId) return;

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
    
    if (result.next_action === "WAIT_FOR_METADATA") {
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
                  progressLabel: "Waiting for metadata...",
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

      return;
    }

    // Accept either backend string to be robust
    if (
      result.next_action === "READY_FOR_PROCESSING" ||
      result.next_action === "READY_TO_COMMIT" ||
      result.next_action === "READY_FOR_COMMIT"
    ) {
      try {
        const chatId = uploadChatIdRef.current ?? activeId;
        const msgId = uploadProgressMsgIdRef.current;

        setUploadPipeline({ percent: 40, label: "Preparing document..." });
        if (chatId && msgId) {
          updateMessagesForChat(chatId, (prev) =>
            prev.map((m) =>
              m.id === msgId
                ? {
                    ...m,
                    status: "progress",
                    progress: 40,
                    progressLabel: "Preparing document...",
                  }
                : m
            )
          );
        }

        // Streaming commit: shows chunking / embedding / indexing progress
        await updateMetadata(
          { job_id: result.job_id, metadata: {}, force: true },
          (evt) => {
            if (!evt) return;
            const raw = typeof evt.progress === "number" ? evt.progress : 50;
            const pct = mapCommitProgress(raw);
            const lbl = evt.message ?? "Processing document...";

            setUploadPipeline({ percent: pct, label: lbl });

            if (chatId && msgId) {
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
          }
        );

        setUploadPipeline(null);
        finalizeUploadSuccess();

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

          addPendingIngestion({
            jobId: typeof result?.job_id === "string" ? result.job_id : null,
            sessionId: chatId,
            chatId,
            messageId: msgId,
          });
        }
      } catch (err: any) {
        setUploadPipeline(null);
        handleSidebarUploadError(err?.message || "Failed to process document.");
      }
    }

  };

  const finalizeUploadSuccess = () => {
    uploadSessionRef.current = null;
    uploadChatIdRef.current = null;
    uploadProgressMsgIdRef.current = null;
    uploadFileNameRef.current = null;
  };

  const handleSidebarUploadError = (errorMsg: string) => {
    uploadSessionRef.current = null;
    setUploadPipeline(null);
    setSidebarMetadataRequest(null);

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
  // guard: ensure this is the current upload session
  if (!uploadSessionRef.current) return;

  // Build metadata map expected by backend: { key: value, ... }
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
  try {
    const chatId = uploadChatIdRef.current ?? activeId;
    const msgId = uploadProgressMsgIdRef.current;

    // update UI
    setUploadPipeline({ percent: 40, label: "Submitting metadata..." });
    if (chatId && msgId) {
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

    // Streaming commit: shows chunking / embedding / indexing progress
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
      }
    );

    // success flow: reuse your finalize helper
    finalizeUploadSuccess();

    // clear UI state
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

      addPendingIngestion({
        jobId,
        sessionId: chatId,
        chatId,
        messageId: msgId,
      });
    }
  } catch (err: any) {
    // reuse existing error handler
    setUploadPipeline(null);
    uploadSessionRef.current = null;
    handleSidebarUploadError(err?.message || "Failed to submit metadata");
  }
};



  /* ================= RENDER ================= */
  
  useEffect(() => {
    if (!activeChat && chats.length > 0 && !activeId) {
      setActiveId(chats[0].id);
    }
  }, [activeChat, chats, activeId]);

  return (
    <div className="flex h-full w-full bg-black text-white">
      <StartupSplash open={showStartup} onDone={() => setShowStartup(false)} />
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
        chats={chats}
        activeId={activeId}
        sessionId={activeId}
        user={user}
        workspaceMode={workspaceMode}
        teamUnreadTotal={teamUnreadTotal}
        unreadCounts={unreadCounts}
        totalUnread={totalUnread}
        onSignOut={onSignOut}
        onWorkspaceModeChange={setWorkspaceMode}
        onSelect={setActiveId}
        onNew={createNewChat}
        onRename={handleRenameChat}
        onDelete={handleDeleteChat}
        onPin={handlePinChat}
        isOpen={sidebarOpen}
        onOpen={() => setSidebarOpen(true)}
        onClose={() => setSidebarOpen(false)}
        isTyping={isTyping}
        onUploadStart={handleSidebarUploadStart}
        onUploadSuccess={handleSidebarUploadSuccess}
        onUploadError={handleSidebarUploadError}
        onUploadProgress={handleSidebarUploadProgress}
      />

      <main
        className={`flex-1 h-full relative transition-all duration-300 ease-in-out ${
          sidebarOpen ? "md:ml-72" : "md:ml-14"
        }`}
      >
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
              onExternalMetadataSubmit={handleExternalMetadataSubmit}
              inputRefExternal={chatInputRef}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-gray-500">
              <button
                onClick={createNewChat}
                className="underline hover:text-white"
              >
                Create a new chat
              </button>
            </div>
          )
        ) : teamWorkspace ? (
          <EnterpriseMessagingWorkspace
            user={user}
            workspace={teamWorkspace}
            onChange={setTeamWorkspace}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500">
            Loading team workspace...
          </div>
        )}
      </main>
    </div>
  );
}
