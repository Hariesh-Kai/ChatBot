"use client";

import { useEffect, useRef, useState } from "react";
import { ChatSession } from "@/app/lib/types";
import { MoreVertical, Pencil, Trash2, Pin } from "lucide-react";

interface Props {
  chats: ChatSession[];
  activeId: string | null; // 🔥 allow HOME state
  onSelect: (id: string) => void;
  onRename: (id: string) => void;
  onDelete: (id: string) => void;
  onPin: (id: string) => void;
  disabled?: boolean;
}

export default function ChatList({
  chats,
  activeId,
  onSelect,
  onRename,
  onDelete,
  onPin,
  disabled = false,
}: Props) {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  /* ================= CLOSE MENU ON OUTSIDE CLICK ================= */
  useEffect(() => {
    function handleOutsideClick(e: MouseEvent) {
      if (
        menuRef.current &&
        !menuRef.current.contains(e.target as Node)
      ) {
        setOpenMenuId(null);
      }
    }

    document.addEventListener("mousedown", handleOutsideClick);
    return () =>
      document.removeEventListener("mousedown", handleOutsideClick);
  }, []);

  /* ================= ONLY SHOW CHATS WITH MESSAGES ================= */
  const visibleChats = chats.filter(
    (chat) => chat.messages.length > 0
  );

  /* ================= EMPTY STATE ================= */
  if (visibleChats.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-500">
        No chats yet
      </div>
    );
  }

  /* ================= CHAT LIST ================= */
  return (
    <div
      className={`space-y-1 ${
        disabled ? "pointer-events-none opacity-50" : ""
      }`}
    >
      {visibleChats.map((chat) => {
        const isActive =
          activeId !== null && chat.id === activeId;

        const isMenuOpen = openMenuId === chat.id;

        return (
          <div
            key={chat.id}
            className={`
              group relative flex items-center
              rounded-md px-3 py-2 text-sm
              cursor-pointer
              transition-colors
              ${
                isActive
                  ? "bg-white/10 text-white"
                  : "text-gray-400 hover:bg-white/5"
              }
            `}
            onClick={() => {
              setOpenMenuId(null);
              onSelect(chat.id);
            }}
          >
            {/* ================= TITLE ================= */}
            <span className="flex-1 truncate">
              {chat.pinned && "📌 "}
              {chat.title || "Untitled chat"}
            </span>

            {/* ================= 3 DOTS (HOVER ONLY) ================= */}
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
                opacity-0 group-hover:opacity-100
                transition-opacity
              "
              aria-label="Chat options"
            >
              <MoreVertical size={16} />
            </button>

            {/* ================= DROPDOWN MENU ================= */}
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

/* ================= MENU ITEM ================= */
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
        ${
          danger
            ? "text-red-400 hover:bg-red-500/10"
            : "text-gray-300 hover:bg-white/5"
        }
      `}
    >
      {icon}
      {label}
    </button>
  );
}
