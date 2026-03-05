"use client";

import { useEffect, useRef, useState } from "react";
import { ChatSession } from "@/app/lib/types";
import { MoreVertical, Pencil, Trash2, Pin } from "lucide-react";

interface Props {
  chats: ChatSession[];
  activeId: string | null;
  unreadCounts?: Record<string, number>;
  onSelect: (id: string) => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
  onPin: (id: string) => void;
  disabled?: boolean;
}

export default function ChatList({
  chats,
  activeId,
  unreadCounts = {},
  onSelect,
  onRename,
  onDelete,
  onPin,
  disabled = false,
}: Props) {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function handleOutsideClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenuId(null);
      }
    }

    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  const visibleChats = chats;

  if (visibleChats.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-500">
        No chats yet
      </div>
    );
  }

  return (
    <div className={`space-y-1 ${disabled ? "pointer-events-none opacity-50" : ""}`}>
      {visibleChats.map((chat) => {
        const isActive = activeId !== null && chat.id === activeId;
        const isMenuOpen = openMenuId === chat.id;
        const unreadCount = unreadCounts[chat.id] || 0;

        return (
          <div
            key={chat.id}
            className={`
              group relative flex items-center
              rounded-[12px] px-2.5 py-2 text-xs sm:px-3 sm:py-2.5 sm:text-sm
              cursor-pointer
              transition-all duration-200
              ${
                isActive
                  ? "border border-white/20 bg-white/10 text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]"
                  : "text-gray-400 hover:bg-white/5 hover:text-gray-200"
              }
            `}
            onClick={() => {
              setOpenMenuId(null);
              onSelect(chat.id);
            }}
          >
            <span className="flex flex-1 items-center gap-1 truncate">
              {chat.pinned && <Pin size={12} className="shrink-0 text-yellow-300/90" />}
              <span className="truncate">{chat.title || "Untitled chat"}</span>
            </span>

            {unreadCount > 0 && (
              <span className="mr-1 inline-flex min-w-[18px] items-center justify-center rounded-full border border-white/30 bg-white/10 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                {unreadCount > 99 ? "99+" : unreadCount}
              </span>
            )}

            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOpenMenuId(isMenuOpen ? null : chat.id);
              }}
              className="
                ml-2 flex items-center justify-center
                rounded-md p-1
                text-gray-400 hover:text-white hover:bg-white/10
                opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100
              "
              aria-label="Chat options"
            >
              <MoreVertical size={16} />
            </button>

            {isMenuOpen && (
              <div
                ref={menuRef}
                onClick={(e) => e.stopPropagation()}
                className="
                  absolute right-2 top-10 z-50
                  w-40 rounded-md
                  border border-white/10
                  bg-black shadow-xl
                "
              >
                <MenuItem
                  icon={<Pencil size={14} />}
                  label="Rename"
                  onClick={() => {
                    setOpenMenuId(null);
                    onRename(chat.id);
                  }}
                />

                <MenuItem
                  icon={<Pin size={14} />}
                  label={chat.pinned ? "Unpin" : "Pin"}
                  onClick={() => {
                    setOpenMenuId(null);
                    onPin(chat.id);
                  }}
                />

                <MenuItem
                  icon={<Trash2 size={14} />}
                  label="Delete"
                  danger
                  onClick={() => {
                    setOpenMenuId(null);
                    onDelete(chat.id);
                  }}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  danger = false,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`
        flex w-full items-center gap-2
        px-3 py-2 text-xs
        ${danger ? "text-red-400 hover:bg-red-500/10" : "text-gray-300 hover:bg-white/5"}
      `}
    >
      {icon}
      {label}
    </button>
  );
}
