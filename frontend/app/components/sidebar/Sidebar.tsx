"use client";

import { useMemo, useRef, useState, useEffect } from "react";
import Brand from "./Brand";
import ChatList from "./ChatList";
import PdfUploadButton from "@/app/components/upload/PdfUploadButton";
import { ChatSession } from "@/app/lib/types";
import { Search, Plus, PanelLeftOpen, PanelLeftClose } from "lucide-react";
import { UploadStatus } from "@/app/hooks/useSmartUpload";

interface SidebarProps {
  chats: ChatSession[];
  activeId: string | null;
  sessionId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onPin: (id: string) => void;
  isOpen: boolean;
  onOpen: () => void;
  onClose: () => void;
  isTyping: boolean;
  
  onUploadStart: () => void;
  onUploadSuccess: (result: any) => void;
  onUploadError: (error: string) => void;
  //  NEW
  onUploadProgress: (status: UploadStatus, percent: number, label: string) => void;
}

export default function Sidebar({
  chats, activeId, sessionId, onSelect, onNew, onRename, onDelete, onPin,
  isOpen, onOpen, onClose, isTyping,
  onUploadStart, onUploadSuccess, onUploadError, onUploadProgress //  Destructure
}: SidebarProps) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement | null>(null);
  const uploadDisabled =
  !sessionId ||
  isTyping ||
  (typeof window !== "undefined" && (window as any).__KAVIN_UPLOAD_ACTIVE__);


  const filteredChats = useMemo(() => {
    const visible = chats.filter((c) => c.messages.length > 0);
    if (!query.trim()) return visible;
    const q = query.toLowerCase();
    return visible.filter((c) => (c.title || "").toLowerCase().includes(q) || c.messages.some((m) => m.role !== "system" && (m.content || "").toLowerCase().includes(q)));
  }, [query, chats]);

  useEffect(() => { if (isOpen && searchRef.current) searchRef.current.focus(); }, [isOpen]);

  return (
    <>
      {isOpen &&  <div
      onClick={() =>
        !isTyping &&
        !(typeof window !== "undefined" && (window as any).__KAVIN_UPLOAD_ACTIVE__) &&
        onClose()
      }
            className="fixed inset-0 z-30 bg-black/60 md:hidden"
    />}
      <aside className={`fixed left-0 top-0 z-40 h-screen bg-black border-r border-white/10 transition-all duration-300 ease-in-out ${isOpen ? "w-72" : "w-14"}`}>
        {!isOpen && (
          <div className="flex h-full flex-col items-center">
            <div className="h-14 w-full flex items-center justify-center border-b border-white/10"><Brand iconOnly /></div>
            <div className="mt-4 flex flex-col gap-3">
              <button onClick={onOpen} disabled={isTyping} className="p-2 rounded-md text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"><PanelLeftOpen size={18} /></button>
              <button onClick={onNew} disabled={isTyping} className="p-2 rounded-md text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"><Plus size={18} /></button>
              
              <PdfUploadButton 
                sessionId={sessionId} iconOnly disabled={uploadDisabled || !sessionId}
                onUploadStart={onUploadStart}
                onUploadSuccess={onUploadSuccess}
                onUploadError={onUploadError}
                onUploadProgress={onUploadProgress} //  Pass it down
              />
              
              <button onClick={onOpen} disabled={isTyping} className="p-2 rounded-md text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"><Search size={18} /></button>
            </div>
          </div>
        )}

        {isOpen && (
          <div className="flex h-full flex-col">
            <div className="relative h-14 border-b border-white/10 flex items-center px-4"><Brand /><button onClick={onClose} disabled={isTyping} className="absolute right-2 rounded-md p-1 text-gray-400 hover:text-white hover:bg-white/10 disabled:opacity-50"><PanelLeftClose size={16} /></button></div>
            <div className="px-4 py-4 space-y-3">
              <button onClick={onNew} disabled={isTyping} className="w-full rounded-md bg-white px-3 py-2 text-sm font-medium text-black hover:bg-gray-200 disabled:opacity-50">+ New Chat</button>
              
              <PdfUploadButton 
                sessionId={sessionId} disabled={uploadDisabled || !sessionId}
                onUploadStart={onUploadStart}
                onUploadSuccess={onUploadSuccess}
                onUploadError={onUploadError}
                onUploadProgress={onUploadProgress} //  Pass it down
              />

              <input ref={searchRef} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search chats" disabled={isTyping} className="w-full rounded-md border border-white/10 bg-transparent px-3 py-2 text-sm text-white outline-none disabled:opacity-50" />
            </div>
            <div className="px-4 pt-3 pb-3 text-xs text-gray-400 select-none">Chats</div>
            <div className="flex-1 overflow-y-auto px-3 pb-4">
              <ChatList
                chats={filteredChats}
                activeId={activeId}
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

            </div>
            <div className="border-t border-white/10 px-4 py-3 text-xs text-gray-500">© KAVIN</div>
          </div>
        )}
      </aside>
    </>
  );
}