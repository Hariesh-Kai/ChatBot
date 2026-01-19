// frontend/app/page.tsx
"use client";

import { useEffect, useState, useCallback } from "react";
import Sidebar from "@/app/components/sidebar/Sidebar";
import ChatWindow from "@/app/components/chat/ChatWindow";
import { ChatSession, Message } from "@/app/lib/types";
import { KavinModelId } from "@/app/lib/kavin-models";
import { loadChats, saveChats } from "@/app/lib/chat-store";
import { commitUpload } from "@/app/lib/api"; 
import { MetadataRequestField } from "@/app/lib/llm-ui-events";
import { UploadStatus } from "@/app/hooks/useSmartUpload";

/* =========================================================
   HELPER: UUID
========================================================= */
function uuidv4() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0,
      v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export default function Home() {
  /* ================= STATE ================= */
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  
  // ✅ NEW: Sidebar metadata request state (replaces Modal)
  const [sidebarMetadataRequest, setSidebarMetadataRequest] = useState<{
      jobId: string;
      fields: MetadataRequestField[];
      filename: string;
  } | null>(null);

  const [sidebarUploadMsgId, setSidebarUploadMsgId] = useState<string | null>(null);

  // Load state on mount
  useEffect(() => {
    const loaded = loadChats();
    setChats(loaded);
    if (loaded.length > 0) {
      setActiveId(loaded[0].id);
    } else {
      createNewChat();
    }
  }, []);

  // Save state on change
  useEffect(() => {
    if (chats.length > 0) saveChats(chats);
  }, [chats]);

  /* ================= DERIVED STATE ================= */
  const activeChat = chats.find((c) => c.id === activeId);

  const isTyping = activeChat
    ? activeChat.messages.some(
        (m) => m.status === "typing" || m.status === "streaming"
      )
    : false;

  /* ================= ACTIONS ================= */

  const createNewChat = useCallback(() => {
    const newChat: ChatSession = {
      id: uuidv4(),
      title: "New Chat",
      messages: [],
      model: "lite", // Default to lite
      pinned: false,
    };
    setChats((prev) => [newChat, ...prev]);
    setActiveId(newChat.id);
    if (window.innerWidth < 768) setSidebarOpen(false);
  }, []);

  const handleDeleteChat = useCallback(
    (id: string) => {
      setChats((prev) => prev.filter((c) => c.id !== id));
      if (activeId === id) {
        setActiveId(null);
      }
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

  const updateMessages = useCallback(
    (updater: Message[] | ((prev: Message[]) => Message[])) => {
      if (!activeId) return;

      setChats((prev) =>
        prev.map((c) => {
          if (c.id !== activeId) return c;
          const newMessages =
            typeof updater === "function" ? updater(c.messages) : updater;
          return { ...c, messages: newMessages };
        })
      );
    },
    [activeId]
  );

  /* ================= SIDEBAR UPLOAD HANDLERS ================= */

  const handleSidebarUploadStart = () => {
    if (!activeId) return;
    const msgId = uuidv4();
    setSidebarUploadMsgId(msgId);
    
    updateMessages((prev) => [...prev, {
        id: msgId,
        role: "assistant",
        content: "",
        createdAt: Date.now(),
        status: "progress",
        progress: 0,
        progressLabel: "Starting upload...",
    }]);
  };

  const handleSidebarUploadProgress = (status: UploadStatus, percent: number, label: string) => {
    if (!activeId || !sidebarUploadMsgId) return;
    
    updateMessages((prev) => prev.map((m) => 
        m.id === sidebarUploadMsgId 
            ? { ...m, progress: percent, progressLabel: label } 
            : m
    ));
  };

  const handleSidebarUploadSuccess = async (result: any) => {
    if (!activeId) return;

    // WAIT_FOR_METADATA logic
    if (result.next_action === "WAIT_FOR_METADATA") {
        const fields: MetadataRequestField[] = Object.entries(result.metadata).map(
            ([key, meta]: [string, any]) => ({
                key: key,
                label: key.replace(/_/g, " ").toUpperCase(),
                value: meta.value || "",
                placeholder: `Enter ${key}...`,
                reason: "Please verify this field"
            })
        );

        // ✅ Instead of opening Modal, we trigger ChatWindow's inline logic via props
        setSidebarMetadataRequest({
            jobId: result.job_id,
            fields: fields,
            filename: result.filename
        });
        
        // Remove progress bar (Inline Prompt will take over UI focus)
        if (sidebarUploadMsgId) {
            updateMessages(prev => prev.filter(m => m.id !== sidebarUploadMsgId));
            setSidebarUploadMsgId(null);
        }
        return; 
    }

    // READY_TO_COMMIT logic
    if (result.next_action === "READY_TO_COMMIT") {
        try {
            handleSidebarUploadProgress("processing", 95, "Finalizing index...");
            
            await commitUpload({
                job_id: result.job_id,
                metadata: {},
                force: true
            });
            
            finalizeUploadSuccess(result.filename, result.revision_number);
        } catch (err: any) {
            handleSidebarUploadError(err.message || "Failed to commit document.");
        }
    }
  };

  const finalizeUploadSuccess = (filename: string, revision: any) => {
    if (sidebarUploadMsgId) {
        updateMessages((prev) => prev.map(m => m.id === sidebarUploadMsgId ? {
            ...m,
            role: "system",
            status: "done",
            content: `✅ Successfully processed "${filename}" (v${revision}). \n\nYou can now ask questions about this document.`
        } : m));
        setSidebarUploadMsgId(null);
    } else {
        const successMsg: Message = {
            id: uuidv4(),
            role: "system",
            content: `✅ Successfully processed "${filename}" (v${revision}). \n\nYou can now ask questions about this document.`,
            createdAt: Date.now(),
            status: "done",
        };
        updateMessages((prev) => [...prev, successMsg]);
    }

    if (activeChat && activeChat.messages.length === 0) {
        handleRenameChat(activeId!, filename.replace(".pdf", ""));
    }
  };

  const handleSidebarUploadError = (errorMsg: string) => {
    if (!activeId) return;

    if (sidebarUploadMsgId) {
        updateMessages((prev) => prev.map(m => m.id === sidebarUploadMsgId ? {
            ...m,
            status: "error",
            content: `⚠️ **Upload Failed**: ${errorMsg}`
        } : m));
        setSidebarUploadMsgId(null);
    } else {
        const errorMsgBubble: Message = {
            id: uuidv4(),
            role: "assistant",
            content: `⚠️ **Upload Failed**: ${errorMsg}`,
            createdAt: Date.now(),
            status: "error",
        };
        updateMessages((prev) => [...prev, errorMsgBubble]);
    }
  };

  /* ================= RENDER ================= */

  if (!activeChat && chats.length > 0 && !activeId) {
     setActiveId(chats[0].id);
  }

  return (
    <div className="flex h-full w-full bg-black text-white">
      <Sidebar
        chats={chats}
        activeId={activeId}
        sessionId={activeId}
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
        className={`
          flex-1 h-full relative transition-all duration-300 ease-in-out
          ${sidebarOpen ? "md:ml-72" : "md:ml-14"}
        `}
      >
        {activeChat ? (
          <div className="flex h-full flex-col">
            <ChatWindow
              messages={activeChat.messages}
              onUpdateMessages={updateMessages}
              model={activeChat.model}
              sessionId={activeChat.id}
              onRenameSession={(newTitle) => handleRenameChat(activeChat.id, newTitle)}
              
              // ✅ PASS THE SIDEBAR REQUEST DOWN
              externalMetadataRequest={sidebarMetadataRequest}
              onExternalMetadataSubmit={() => setSidebarMetadataRequest(null)}
            />
          </div>
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500">
            <button onClick={createNewChat} className="underline hover:text-white">
                Create a new chat
            </button>
          </div>
        )}
      </main>
    </div>
  );
}