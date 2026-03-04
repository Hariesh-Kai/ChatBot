"use client";

import { Bot, Code2, MessagesSquare } from "lucide-react";

import type { WorkspaceMode } from "@/app/lib/enterprise-messaging";

interface WorkspaceModeSwitcherProps {
  workspaceMode: WorkspaceMode;
  teamUnreadTotal?: number;
  onChange: (mode: WorkspaceMode) => void;
  showTeamMode?: boolean;
  showPmlEntry?: boolean;
}

export default function WorkspaceModeSwitcher({
  workspaceMode,
  teamUnreadTotal = 0,
  onChange,
  showTeamMode = true,
  showPmlEntry = true,
}: WorkspaceModeSwitcherProps) {
  const isAiMode = workspaceMode === "ai";
  const isTeamMode = workspaceMode === "team";
  const isPmlMode = workspaceMode === "pml";
  const activeClass =
    "bg-white text-black shadow-[inset_0_0_0_1px_rgba(255,255,255,0.2)]";
  const inactiveClass = "text-gray-300 hover:bg-white/10 hover:text-white";

  return (
    <div className="rounded-[12px] border border-white/10 bg-black p-1">
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onChange("ai")}
          className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all duration-200 ${isAiMode ? activeClass : inactiveClass}`}
        >
          <Bot size={13} />
          AI
        </button>

        {showTeamMode && (
          <button
            type="button"
            onClick={() => onChange("team")}
            className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all duration-200 ${isTeamMode ? activeClass : inactiveClass}`}
          >
            <MessagesSquare size={13} />
            Team
            {teamUnreadTotal > 0 && (
              <span className={`rounded-full px-1.5 text-[10px] leading-4 ${
                isTeamMode
                  ? "border border-black/15 bg-black/10 text-black"
                  : "border border-white/20 bg-white/10 text-gray-100"
              }`}>
                {teamUnreadTotal > 9 ? "9+" : teamUnreadTotal}
              </span>
            )}
          </button>
        )}

        {showPmlEntry && (
          <button
            type="button"
            onClick={() => onChange("pml")}
            className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all duration-200 ${isPmlMode ? activeClass : inactiveClass}`}
          >
            <Code2 size={13} />
            PML
          </button>
        )}
      </div>
    </div>
  );
}
