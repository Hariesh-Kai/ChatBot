"use client";

import { useState, useRef } from "react";
import PromptCard from "./chat/PromptCard";
import { Send, LayoutDashboard, BellRing, MessageSquareText, FileText, Sparkles } from "lucide-react";
import PdfUploadButton from "./upload/PdfUploadButton";
import { UploadStatus } from "@/app/hooks/useSmartUpload";
import { getRoleLabel } from "@/app/lib/org-role-catalog";

export type EmptyPrompt = {
  title: string;
  description: string;
  prompt: string;
};

interface Props {
  onSend: (text?: string) => void;
  disabled?: boolean;
  sessionId: string | null;
  userLabel?: string;
  userRole?: string;
  totalChats?: number;
  unreadNotifications?: number;
  onUploadStart?: (file: File) => void;
  onUploadSuccess?: (result: any) => void;
  onUploadError?: (error: string) => void;
  onUploadProgress?: (status: UploadStatus, percent: number, label: string) => void;
  showUploadButton?: boolean;
  showPmlEntryCard?: boolean;
  showSummaryCard?: boolean;
  dashboardTitle?: string;
  dashboardSubtitle?: string;
  heroTitle?: string;
  heroSubtitle?: string;
  prompts?: EmptyPrompt[];
}

export default function EmptyState({
  onSend,
  disabled = false,
  sessionId,
  userLabel,
  userRole,
  totalChats = 0,
  unreadNotifications = 0,
  onUploadStart,
  onUploadSuccess,
  onUploadError,
  onUploadProgress,
  showUploadButton = true,
  showPmlEntryCard = true,
  showSummaryCard = true,
  dashboardTitle,
  dashboardSubtitle,
  heroTitle,
  heroSubtitle,
  prompts,
}: Props) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const canSend = !disabled && value.trim().length > 0;

  const defaultPrompts: EmptyPrompt[] = [
    {
      title: "Summarize a PDF",
      description: "Upload a document and get a concise summary",
      prompt: "Summarize this PDF",
    },
    {
      title: "Ask about requirements",
      description: "Clarify design specs, scope, or constraints",
      prompt: "What are the requirements for this?",
    },
    {
      title: "Extract key points",
      description: "Pull tables, bullets, or highlights",
      prompt: "Extract the key points from this",
    },
    {
      title: "Explain technical sections",
      description: "Understand complex engineering content",
      prompt: "Explain the technical sections",
    },
  ];
  const effectivePrompts = prompts ?? defaultPrompts;
  const verticalNudgeClass = showSummaryCard ? "" : "md:-translate-y-4";

  function handleSubmit(text?: string) {
    if (disabled) return;
    const finalText = (text ?? value).trim();
    if (!finalText) return;
    onSend(finalText);
    setValue("");
  }

  return (
    <div className={`empty-state-shell flex h-full w-full items-center justify-center ${verticalNudgeClass}`}>
      <div className="empty-state-content w-full max-w-3xl px-2.5 text-center animate-fade-in sm:px-4">
        {showSummaryCard && (
          <div className="empty-state-summary mb-6 rounded-2xl border border-white/10 bg-[radial-gradient(circle_at_top_right,#1f2937_0%,#0f172a_35%,#0a0a0a_100%)] p-4 text-left shadow-lg">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-[11px] uppercase tracking-[0.18em] text-cyan-300">User Dashboard</div>
                <h2 className="mt-1 text-lg font-semibold text-white">
                  {dashboardTitle || `Welcome to Kavin, ${userLabel || "User"}`}
                </h2>
                <p className="mt-1 text-xs text-gray-300">
                  {dashboardSubtitle ||
                    `Role: ${getRoleLabel(userRole)} - start a conversation or upload a project PDF.`}
                </p>
              </div>
              <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-cyan-400/30 bg-cyan-500/10 text-cyan-300">
                <LayoutDashboard size={18} />
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <DashboardStat icon={MessageSquareText} label="Chats" value={String(totalChats)} />
              <DashboardStat icon={BellRing} label="Unread" value={String(unreadNotifications)} />
              <DashboardStat icon={FileText} label="PDF" value="Ready" />
              <DashboardStat icon={Sparkles} label="Assistant" value="Online" />
            </div>
          </div>
        )}

        <h1 className="text-xl font-semibold text-white sm:text-2xl">{heroTitle || "How can I help you today?"}</h1>
        <p className="mt-2 text-xs text-gray-400 sm:text-sm">
          {heroSubtitle || "Ask a question or upload a PDF to get started"}
        </p>

        <div className="mt-6 sm:mt-8">
          <div className={`empty-state-input flex items-end gap-3 rounded-3xl border border-white/25 bg-[#1a1a1a] px-3 py-3 shadow-md transition ${disabled ? "opacity-60" : ""}`}>
            {showUploadButton && (
              <div className="pb-1">
                <PdfUploadButton
                  sessionId={sessionId}
                  iconOnly={true}
                  disabled={disabled}
                  onUploadStart={onUploadStart}
                  onUploadSuccess={onUploadSuccess}
                  onUploadError={onUploadError}
                  onUploadProgress={onUploadProgress}
                />
              </div>
            )}

            <input
              ref={inputRef}
              value={value}
              spellCheck={false}
              disabled={disabled}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canSend) handleSubmit();
              }}
              placeholder={disabled ? "AI is responding..." : "Ask anything..."}
              className="flex-1 bg-transparent py-2.5 text-[13px] text-white outline-none placeholder:text-gray-500 sm:py-3 sm:text-sm"
            />

            <div className="pb-1">
              <button
                onClick={() => canSend && handleSubmit()}
                disabled={!canSend}
                className={`flex h-8 w-8 items-center justify-center rounded-xl transition sm:h-9 sm:w-9 sm:rounded-2xl ${
                  canSend
                    ? "bg-white text-black hover:bg-gray-200"
                    : "cursor-not-allowed bg-white/10 text-gray-500"
                }`}
              >
                <Send size={16} />
              </button>
            </div>
          </div>
        </div>

        {(showPmlEntryCard || effectivePrompts.length > 0) && (
          <div className="empty-state-prompts mt-6 grid w-full grid-cols-1 gap-3 sm:mt-8 sm:gap-4 sm:grid-cols-2">
            {showPmlEntryCard && (
              <a
                href="/pml"
                className="block rounded-xl border border-cyan-300/30 bg-cyan-500/10 p-4 text-left transition hover:bg-cyan-500/20"
              >
                <div className="text-sm font-semibold text-cyan-100">Open PML Assistant</div>
                <div className="mt-1 text-xs text-cyan-100/80">
                  Dedicated code-writing assistant module for PML generation.
                </div>
              </a>
            )}
            {effectivePrompts.map((item) => (
              <PromptCard
                key={item.title}
                title={item.title}
                description={item.description}
                onClick={() => handleSubmit(item.prompt)}
                disabled={disabled}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function DashboardStat({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-black/30 px-3 py-2">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-gray-400">
        <Icon size={12} className="text-cyan-300" />
        <span>{label}</span>
      </div>
      <div className="mt-1 text-sm font-semibold text-white">{value}</div>
    </div>
  );
}
