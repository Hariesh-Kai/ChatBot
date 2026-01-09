import { ChatSession } from "@/app/lib/types";
import { Pencil, Trash2 } from "lucide-react";

interface Props {
  chat: ChatSession;
  isActive?: boolean;
}

export default function ChatItem({ chat, isActive = false }: Props) {
  return (
    <div
      className={`
        group flex items-center justify-between
        rounded-lg px-3 py-2 text-sm cursor-pointer
        ${
          isActive
            ? "bg-white/10 text-white"
            : "text-gray-300 hover:bg-white/5"
        }
      `}
    >
      <span className="truncate">{chat.title}</span>

      <div className="hidden gap-2 group-hover:flex">
        <Pencil size={14} className="text-gray-400 hover:text-white" />
        <Trash2 size={14} className="text-gray-400 hover:text-red-400" />
      </div>
    </div>
  );
}
