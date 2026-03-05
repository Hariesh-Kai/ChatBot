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
    <div className="mode-switcher max-w-full overflow-x-auto rounded-[12px] border border-white/10 bg-black p-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
      <div className="flex min-w-max items-center gap-1">
        <button
          type="button"
          onClick={() => onChange("ai")}
          className={`inline-flex items-center gap-1 rounded-md px-1.5 py-1.5 text-[10px] font-medium transition-all duration-200 min-[361px]:px-2 min-[361px]:text-[11px] sm:gap-1.5 sm:px-2.5 sm:text-xs ${isAiMode ? activeClass : inactiveClass}`}
        >
          <Bot size={13} className="hidden sm:block" />
          AI
        </button>

        {showTeamMode && (
          <button
            type="button"
            onClick={() => onChange("team")}
            className={`inline-flex items-center gap-1 rounded-md px-1.5 py-1.5 text-[10px] font-medium transition-all duration-200 min-[361px]:px-2 min-[361px]:text-[11px] sm:gap-1.5 sm:px-2.5 sm:text-xs ${isTeamMode ? activeClass : inactiveClass}`}
          >
            <MessagesSquare size={13} className="hidden sm:block" />
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
            className={`inline-flex items-center gap-1 rounded-md px-1.5 py-1.5 text-[10px] font-medium transition-all duration-200 min-[361px]:px-2 min-[361px]:text-[11px] sm:gap-1.5 sm:px-2.5 sm:text-xs ${isPmlMode ? activeClass : inactiveClass}`}
          >
            <Code2 size={13} className="hidden sm:block" />
            PML
          </button>
        )}
      </div>
    </div>
  );
}
