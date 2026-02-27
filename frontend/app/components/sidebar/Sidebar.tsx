"use client";

import { useMemo, useRef, useState, useEffect } from "react";
import Brand from "./Brand";
import ChatList from "./ChatList";
import PdfUploadButton from "@/app/components/upload/PdfUploadButton";
import { ChatSession } from "@/app/lib/types";
import {
  BellRing,
  Bot,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
} from "lucide-react";
import { UploadStatus } from "@/app/hooks/useSmartUpload";
import type { AuthUser } from "@/app/lib/api";
import type { WorkspaceMode } from "@/app/lib/enterprise-messaging";

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
  onWorkspaceModeChange: (mode: WorkspaceMode) => void;
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
  onWorkspaceModeChange,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onPin,
  isOpen, onOpen, onClose, isTyping,
  onUploadStart, onUploadSuccess, onUploadError, onUploadProgress //  Destructure
}: SidebarProps) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement | null>(null);
  const isAiMode = workspaceMode === "ai";
  const uploadActive =
    typeof window !== "undefined" && (window as any).__KAVIN_UPLOAD_ACTIVE__;
  const interactionBlocked = isAiMode && (isTyping || uploadActive);
  const uploadDisabled = !isAiMode || !sessionId || isTyping || uploadActive;
  const combinedUnread = totalUnread + teamUnreadTotal;

  const userInitial =
    (user?.username || user?.email || "U").trim().charAt(0).toUpperCase() || "U";
  const canAccessDevtools = user?.role === "admin" || user?.role === "developer";


  const filteredChats = useMemo(() => {
    const visible = chats.filter((c) => c.messages.length > 0);
    if (!query.trim()) return visible;
    const q = query.toLowerCase();
    return visible.filter((c) => (c.title || "").toLowerCase().includes(q) || c.messages.some((m) => m.role !== "system" && (m.content || "").toLowerCase().includes(q)));
  }, [query, chats]);

  useEffect(() => {
    if (isOpen && isAiMode && searchRef.current) {
      searchRef.current.focus();
    }
  }, [isAiMode, isOpen]);

  return (
    <>
      {isOpen &&  <div
      onClick={() =>
        !interactionBlocked &&
        onClose()
      }
            className="fixed inset-0 z-30 bg-black/60 md:hidden"
    />}
      <aside className={`fixed left-0 top-0 z-40 h-screen bg-black border-r border-white/10 transition-all duration-300 ease-in-out ${isOpen ? "w-72" : "w-14"}`}>
        {!isOpen && (
          <div className="flex h-full flex-col items-center">
            <div className="h-14 w-full flex items-center justify-center border-b border-white/10"><Brand iconOnly /></div>
            <div className="mt-4 flex flex-col gap-3">
              <button onClick={onOpen} disabled={interactionBlocked} className="relative p-2 rounded-md text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50">
                <PanelLeftOpen size={18} />
                {combinedUnread > 0 && (
                  <span className="absolute -right-1 -top-1 min-w-[16px] rounded-full bg-emerald-500 px-1 text-center text-[10px] font-semibold text-black">
                    {combinedUnread > 9 ? "9+" : combinedUnread}
                  </span>
                )}
              </button>
              <button
                onClick={() => onWorkspaceModeChange("ai")}
                className={`p-2 rounded-md ${isAiMode ? "bg-white/10 text-white" : "text-gray-400 hover:text-white hover:bg-white/10"}`}
                aria-label="AI assistant mode"
              >
                <Bot size={18} />
              </button>
              <button
                onClick={() => onWorkspaceModeChange("team")}
                className={`relative p-2 rounded-md ${!isAiMode ? "bg-white/10 text-white" : "text-gray-400 hover:text-white hover:bg-white/10"}`}
                aria-label="Team messaging mode"
              >
                <MessagesSquare size={18} />
                {teamUnreadTotal > 0 && (
                  <span className="absolute -right-1 -top-1 min-w-[16px] rounded-full bg-cyan-400 px-1 text-center text-[10px] font-semibold text-black">
                    {teamUnreadTotal > 9 ? "9+" : teamUnreadTotal}
                  </span>
                )}
              </button>
              {isAiMode && (
                <>
                  <button onClick={onNew} disabled={isTyping} className="p-2 rounded-md text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"><Plus size={18} /></button>
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
                  <button onClick={onOpen} disabled={isTyping} className="p-2 rounded-md text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"><Search size={18} /></button>
                </>
              )}
            </div>
          </div>
        )}

        {isOpen && (
          <div className="flex h-full flex-col">
            <div className="relative h-14 border-b border-white/10 flex items-center px-4"><Brand /><button onClick={onClose} disabled={interactionBlocked} className="absolute right-2 rounded-md p-1 text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"><PanelLeftClose size={16} /></button></div>
            <div className="px-4 py-4 space-y-3">
              <div className="grid grid-cols-2 gap-2 rounded-lg border border-white/10 bg-white/5 p-1">
                <button
                  type="button"
                  onClick={() => onWorkspaceModeChange("ai")}
                  className={`rounded-md px-2 py-1.5 text-xs font-semibold transition-colors ${
                    isAiMode ? "bg-white text-black" : "text-gray-300 hover:bg-white/10"
                  }`}
                >
                  AI Assistant
                </button>
                <button
                  type="button"
                  onClick={() => onWorkspaceModeChange("team")}
                  className={`rounded-md px-2 py-1.5 text-xs font-semibold transition-colors ${
                    !isAiMode ? "bg-white text-black" : "text-gray-300 hover:bg-white/10"
                  }`}
                >
                  Team Messaging
                </button>
              </div>

              {isAiMode ? (
                <>
                  <button onClick={onNew} disabled={isTyping} className="w-full rounded-md bg-white px-3 py-2 text-sm font-medium text-black hover:bg-gray-200 disabled:opacity-50">+ New Chat</button>
                  <PdfUploadButton
                    sessionId={sessionId}
                    disabled={uploadDisabled || !sessionId}
                    dataId="sidebar"
                    onUploadStart={onUploadStart}
                    onUploadSuccess={onUploadSuccess}
                    onUploadError={onUploadError}
                    onUploadProgress={onUploadProgress}
                  />
                  <input ref={searchRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats" disabled={isTyping} className="w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm text-white outline-none disabled:opacity-50" />
                </>
              ) : (
                <div className="rounded-lg border border-cyan-400/20 bg-cyan-500/10 px-3 py-3 text-xs text-cyan-100">
                  <div className="font-semibold">Enterprise chat mode is active.</div>
                  <div className="mt-1 text-cyan-100/80">
                    Team messaging is now shown in the main workspace with project assignment tools.
                  </div>
                </div>
              )}
            </div>
            {isAiMode && (
              <div className="px-4 pt-3 pb-3 text-xs text-gray-400 select-none flex items-center justify-between">
                <span>Chats</span>
                {totalUnread > 0 && (
                  <span className="rounded-full bg-emerald-500/20 border border-emerald-500/30 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
                    {totalUnread} new
                  </span>
                )}
              </div>
            )}
            <div className="flex-1 overflow-y-auto px-3 pb-4">
              {isAiMode ? (
                <ChatList
                  chats={filteredChats}
                  activeId={activeId}
                  unreadCounts={unreadCounts}
                  disabled={isTyping}
                  onSelect={(id) => !isTyping && onSelect(id)}
                  onRename={(id) => {
                    const title = prompt("Rename chat");
                    if (!title) return;
                    const clean = title.trim();
                    if (clean) onRename(id, clean);
                  }}
                  onDelete={onDelete}
                  onPin={onPin}
                />
              ) : (
                <div className="space-y-2 rounded-lg border border-white/10 bg-white/5 p-3 text-xs text-gray-300">
                  <div className="font-semibold text-white">Team Messaging</div>
                  <div>Use the main panel to chat with teammates in a WhatsApp-style enterprise workspace.</div>
                  <div className="rounded-md border border-cyan-300/20 bg-cyan-400/10 px-2 py-1 text-cyan-100">
                    {teamUnreadTotal > 0 ? `${teamUnreadTotal} unread team messages` : "No unread team messages"}
                  </div>
                </div>
              )}

            </div>
            <div className="border-t border-white/10 px-4 py-3">
              <div className="flex items-center gap-3">
                <div className="h-9 w-9 shrink-0 rounded-full bg-white/10 border border-white/10 flex items-center justify-center text-sm font-semibold text-white">
                  {userInitial}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="text-sm text-gray-200 truncate">
                    {user?.username ?? "User"}
                  </div>
                  <div className="text-[11px] text-gray-500 truncate">
                    {user?.email ?? ""}
                  </div>
                </div>
              </div>

              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <div className="inline-flex items-center gap-1 text-xs text-gray-400">
                  <BellRing size={12} />
                  {isAiMode
                    ? totalUnread > 0
                      ? `${totalUnread} unread AI chats`
                      : "No new AI chat alerts"
                    : teamUnreadTotal > 0
                      ? `${teamUnreadTotal} unread team messages`
                      : "No new team chat alerts"}
                </div>
                {canAccessDevtools && (
                  <a
                    href="/dashboard"
                    className="text-xs text-gray-400 hover:text-white hover:underline"
                  >
                    Developer dashboard
                  </a>
                )}

                <button
                  type="button"
                  onClick={onSignOut}
                  disabled={interactionBlocked}
                  className="shrink-0 rounded-md border border-white/10 bg-white/5 px-2 py-1 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-50"
                >
                  Sign out
                </button>
              </div>
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
