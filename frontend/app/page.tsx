// frontend/app/page.tsx
"use client";

import { useEffect, useState, useCallback, useRef } from "react";
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
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}



/* =========================================================
    NORMALIZE MESSAGES
  ========================================================= */

export default function Home() {
  /* ================= PIPELINE STATE (UPLOAD / SYSTEM) ================= */
  const [uploadPipeline, setUploadPipeline] = useState<{
    percent: number;
    label: string;
  } | null>(null);

  /* ================= STATE ================= */
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const [sidebarMetadataRequest, setSidebarMetadataRequest] = useState<{
    jobId: string;
    fields: MetadataRequestField[];
    filename: string;
  } | null>(null);


  // 🔥 FIX: track upload lifecycle to avoid race
  const uploadSessionRef = useRef<string | null>(null);
  
  
  /* ================= LOAD / SAVE ================= */

  useEffect(() => {
    const loaded = loadChats();
    setChats(loaded);
    if (loaded.length > 0) {
      setActiveId(loaded[0].id);
    } else {
      createNewChat();
    }
  }, []);

  useEffect(() => {
    if (chats.length > 0) saveChats(chats);
  }, [chats]);

  /* ================= DERIVED ================= */

  const activeChat = chats.find((c) => c.id === activeId);

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



  /* ================= ACTIONS ================= */

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

 const updateMessages = useCallback(
  (updater: Message[] | ((prev: Message[]) => Message[])) => {
    if (!activeId) return;

    setChats((prev) =>
      prev.map((c) => {
        if (c.id !== activeId) return c;

        const next =
          typeof updater === "function"
            ? updater(c.messages)
            : updater;

        // ✅ DO NOT NORMALIZE DURING STREAM
        return { ...c, messages: next };
      })
    );
  },
  [activeId]
);


  /* ================= SIDEBAR UPLOAD ================= */

  const handleSidebarUploadStart = () => {
  if (!activeId) return;

  uploadSessionRef.current = uuidv4();

  setUploadPipeline({
    percent: 0,
    label: "Starting upload…",
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
  };

  const handleSidebarUploadSuccess = async (result: any) => {
    if (!activeId) return;
    if (!uploadSessionRef.current) return;
    
    if (result.next_action === "WAIT_FOR_METADATA") {
      setUploadPipeline(null);
      
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

    if (result.next_action === "READY_TO_COMMIT") {
      try {
        handleSidebarUploadProgress("processing", 95, "Finalizing index...");
        await commitUpload({
          job_id: result.job_id,
          metadata: {},
          force: true,
        });

        setUploadPipeline(null);
        finalizeUploadSuccess(result.filename, result.revision_number);
      } catch (err: any) {
        setUploadPipeline(null);
        handleSidebarUploadError(err.message || "Failed to commit document.");
      }
    }
  };

  const finalizeUploadSuccess = (filename: string, revision: any) => {
    uploadSessionRef.current = null;

    

    if (activeChat && activeChat.messages.length === 0) {
      handleRenameChat(activeId!, filename.replace(".pdf", ""));
    }
  };

  const handleSidebarUploadError = (errorMsg: string) => {
    uploadSessionRef.current = null;
    setUploadPipeline(null);

    
  };


  /* ================= RENDER ================= */
  
  useEffect(() => {
    if (!activeChat && chats.length > 0 && !activeId) {
      setActiveId(chats[0].id);
    }
  }, [activeChat, chats, activeId]);

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
        className={`flex-1 h-full relative transition-all duration-300 ease-in-out ${
          sidebarOpen ? "md:ml-72" : "md:ml-14"
        }`}
      >
        {activeChat ? (
          <ChatWindow
            messages={activeChat.messages}
            onUpdateMessages={updateMessages}
            model={activeChat.model}
            sessionId={activeChat.id}
            uploadPipeline={uploadPipeline} 
            onRenameSession={(t) => handleRenameChat(activeChat.id, t)}
            externalMetadataRequest={sidebarMetadataRequest}
            metadataActive={!!sidebarMetadataRequest} 
            onExternalMetadataSubmit={() => {
              setSidebarMetadataRequest(null);
              uploadSessionRef.current = null;
            }}

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
        )}
      </main>
    </div>
  );
}
