"use client";

import { useEffect, useState, useCallback } from "react";
import Sidebar from "./components/sidebar/Sidebar";
import ChatWindow from "./components/chat/ChatWindow";
import ChatHeader from "./components/chat/ChatHeader";
import DeleteConfirmModal from "./components/ui/DeleteConfirmModal";
import Disclaimer from "./components/ui/Disclaimer";

/* 🔥 Metadata popup */
import MetadataModal from "./components/chat/MetadataModal";
import { MetadataRequestField } from "@/app/lib/llm-ui-events";

import { ChatSession, Message } from "@/app/lib/types";
import { loadChats, saveChats } from "@/app/lib/chat-store";
import { KavinModelId } from "@/app/lib/kavin-models";

/* =========================================================
   SAFE DEFAULTS
========================================================= */

const DEFAULT_MODEL: KavinModelId = "lite";

/* =========================================================
   HELPERS
========================================================= */

function generateChatTitle(text: string): string {
  return text
    .replace(/[`*_~>#]/g, "")
    .split(/[.!?\n]/)[0]
    .split(" ")
    .slice(0, 5)
    .join(" ")
    .trim();
}

function normalizeChats(raw: any[]): ChatSession[] {
  return raw.map((c) => {
    const model: KavinModelId =
      c.model === "base" || c.model === "lite" || c.model === "net"
        ? c.model
        : DEFAULT_MODEL;

    return {
      id: c.id ?? crypto.randomUUID(),
      title: c.title ?? "",
      model,
      pinned: Boolean(c.pinned),
      messages: Array.isArray(c.messages) ? c.messages : [],
    };
  });
}

/* =========================================================
   PAGE
========================================================= */

export default function Page() {
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [deleteChatId, setDeleteChatId] = useState<string | null>(null);

  /* 🔥 Upload metadata popup state */
  const [uploadMetadata, setUploadMetadata] = useState<{
    jobId: string;
    fields: MetadataRequestField[];
  } | null>(null);

  /* =======================================================
     INITIAL LOAD
  ======================================================= */

  useEffect(() => {
    const stored = normalizeChats(loadChats());

    if (stored.length > 0) {
      setChats(stored);
      setActiveId(stored[0].id);
    } else {
      const chat = createChat();
      setChats([chat]);
      setActiveId(chat.id);
    }
  }, []);

  /* =======================================================
     CHAT HELPERS
  ======================================================= */

  const createChat = useCallback((): ChatSession => {
    return {
      id: crypto.randomUUID(),
      title: "",
      messages: [],
      model: DEFAULT_MODEL,
      pinned: false,
    };
  }, []);

  const ensureActiveChat = useCallback((): string => {
    if (activeId) return activeId;

    const chat = createChat();
    setChats((prev) => {
      const next = [chat, ...prev];
      saveChats(next);
      return next;
    });
    setActiveId(chat.id);
    return chat.id;
  }, [activeId, createChat]);

  function updateMessages(
    updater: Message[] | ((prev: Message[]) => Message[])
  ) {
    const chatId = ensureActiveChat();

    setChats((prev) => {
      const next = prev.map((c) => {
        if (c.id !== chatId) return c;

        const nextMessages =
          typeof updater === "function"
            ? updater(c.messages)
            : updater;

        if (
          !c.title &&
          nextMessages[0]?.role === "user" &&
          typeof nextMessages[0].content === "string"
        ) {
          return {
            ...c,
            title: generateChatTitle(nextMessages[0].content),
            messages: nextMessages,
          };
        }

        return { ...c, messages: nextMessages };
      });

      saveChats(next);
      return next;
    });
  }

  /* =======================================================
     CHAT ACTIONS
  ======================================================= */

  function handleNewChat() {
    const chat = createChat();
    setChats((prev) => {
      const next = [chat, ...prev];
      saveChats(next);
      return next;
    });
    setActiveId(chat.id);
  }

  function renameChat(chatId: string, title: string) {
    if (!title.trim()) return;
    setChats((prev) => {
      const next = prev.map((c) =>
        c.id === chatId ? { ...c, title } : c
      );
      saveChats(next);
      return next;
    });
  }

  function clearChat(chatId: string) {
    setChats((prev) => {
      const next = prev.map((c) =>
        c.id === chatId ? { ...c, messages: [] } : c
      );
      saveChats(next);
      return next;
    });
  }

  function changeChatModel(chatId: string, model: KavinModelId) {
    setChats((prev) => {
      const next = prev.map((c) =>
        c.id === chatId ? { ...c, model } : c
      );
      saveChats(next);
      return next;
    });
  }

  function handleModelSwitch(model: KavinModelId) {
    if (activeId) {
      changeChatModel(activeId, model);
    } else {
      const chat = createChat();
      chat.model = model;
      setChats((prev) => {
        const next = [chat, ...prev];
        saveChats(next);
        return next;
      });
      setActiveId(chat.id);
    }
  }

  function requestDeleteChat(chatId: string) {
    setDeleteChatId(chatId);
  }

  function confirmDeleteChat() {
    if (!deleteChatId) return;
    setChats((prev) => {
      const next = prev.filter((c) => c.id !== deleteChatId);
      saveChats(next);
      return next;
    });
    setActiveId(null);
    setDeleteChatId(null);
  }

  function pinChat(chatId: string) {
    setChats((prev) => {
      const next = [...prev]
        .map((c) =>
          c.id === chatId ? { ...c, pinned: !c.pinned } : c
        )
        .sort((a, b) => Number(b.pinned) - Number(a.pinned));
      saveChats(next);
      return next;
    });
  }

  /* =======================================================
     🔥 UPLOAD HANDLER
  ======================================================= */

  const handleUploadSuccess = useCallback(
    (result: any) => {
      if (activeId) {
        updateMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "system",
            content: `📄 Uploaded "${result.filename}" successfully.`,
            createdAt: Date.now(),
            status: "done",
          },
        ]);
      }

      if (
        result.next_action === "WAIT_FOR_METADATA" &&
        Array.isArray(result.missing_metadata)
      ) {
        const fields: MetadataRequestField[] =
          result.missing_metadata.map((key: string) => ({
            key,
            label: key
              .replace(/_/g, " ")
              .replace(/\b\w/g, (c) => c.toUpperCase()),
            placeholder: `Enter ${key.replace(/_/g, " ")}`,
            reason: "Low confidence extraction",
          }));

        setUploadMetadata({
          jobId: result.job_id,
          fields,
        });
      }
    },
    [activeId]
  );

  /* =======================================================
     RENDER
  ======================================================= */

  const activeChat =
    activeId ? chats.find((c) => c.id === activeId) ?? null : null;

  const isTyping =
    activeChat?.messages.some(
      (m) => m.status === "typing" || m.status === "streaming"
    ) ?? false;

  return (
    <div className="relative h-screen w-screen bg-black text-white overflow-hidden">
      <Sidebar
        chats={chats}
        activeId={activeId}
        isTyping={isTyping}
        onSelect={setActiveId}
        onNew={handleNewChat}
        onRename={renameChat}
        onDelete={requestDeleteChat}
        onPin={pinChat}
        isOpen={sidebarOpen}
        onOpen={() => setSidebarOpen(true)}
        onClose={() => setSidebarOpen(false)}
        sessionId={activeId}
        onUploadStart={() => {}}
        onUploadSuccess={handleUploadSuccess}
        onUploadError={(err) => alert(err)}
      />

      <main
        className={`flex flex-col h-full ${
          sidebarOpen ? "pl-72" : "pl-14"
        }`}
      >
        <ChatHeader
          title={activeChat?.title ?? ""}
          isTyping={isTyping}
          activeModel={activeChat?.model ?? DEFAULT_MODEL}
          onRename={(t) =>
            activeChat && renameChat(activeChat.id, t)
          }
          onClear={() =>
            activeChat && clearChat(activeChat.id)
          }
          onModelChange={handleModelSwitch}
        />

        <ChatWindow
          messages={activeChat?.messages ?? []}
          onUpdateMessages={updateMessages}
          model={activeChat?.model ?? DEFAULT_MODEL}
          sessionId={activeChat?.id ?? null}
        />

        <Disclaimer />
      </main>

      <DeleteConfirmModal
        open={!!deleteChatId}
        onCancel={() => setDeleteChatId(null)}
        onConfirm={confirmDeleteChat}
      />

      {/* 🔥 Metadata Popup */}
      {uploadMetadata && (
        <MetadataModal
          fields={uploadMetadata.fields}
          sessionId={uploadMetadata.jobId}
          onSuccess={() => setUploadMetadata(null)}
          onCancel={() => setUploadMetadata(null)}
        />
      )}
    </div>
  );
}
