// frontend/components/chat/MessageBubble.tsx
"use client";

import { Message, RagSource } from "@/app/lib/types";
import Avatar from "../ui/Avatar";
import ReactMarkdown from "react-markdown";
import CodeBlock from "./CodeBlock";
import ThinkingDisclosure from "./ThinkingDisclosure";
import TypingIndicator from "./TypingIndicator";
import { Copy, Trash2, RotateCcw, BookOpen, FileText } from "lucide-react"; 
import remarkGfm from "remark-gfm";
import FeedbackBar from "./FeedbackBar";

/* ================= PROPS ================= */

interface Props {
  message: Message;
  modelLabel?: string;
  isLastAssistant?: boolean;
  isEditing?: boolean;
  onRetry?: () => void;
  onDelete?: () => void;
  onViewSources?: (sources: RagSource[]) => void;

  // 🔥 ADD THESE
  sessionId?: string | null;
  companyDocumentId?: string;
  revisionNumber?: number;
}


/* ================= HELPERS ================= */

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ================= COMPONENT ================= */

export default function MessageBubble({
  message,
  modelLabel = "AI",
  isLastAssistant = false,
  isEditing = false,
  onRetry,
  onDelete,
  onViewSources,
  sessionId,
  companyDocumentId,
  revisionNumber,
}: Props) {
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  // --- DERIVED STATES ---
  const isProgress = message.status === "progress";
  const isTyping = message.status === "typing";
  const isError = message.status === "error";
  const isEdited = Boolean(message.edited);
  const isRegenerated = Boolean(message.regenerated);

  const hasContent =
    typeof message.content === "string" &&
    message.content.trim().length > 0;

  async function handleCopy() {
    if (!hasContent) return;
    await navigator.clipboard.writeText(message.content || "");
  }

  /* ================= 1. SYSTEM MESSAGE (CENTERED PILL) ================= */
  if (isSystem) {
    return (
      <div className="mx-auto my-6 flex justify-center animate-in fade-in zoom-in-95 duration-500">
        <div className="flex items-center gap-2 rounded-full bg-white/5 px-4 py-1.5 text-xs font-medium text-gray-400 border border-white/5 shadow-sm">
          <FileText size={12} className="text-blue-400" />
          <span>{message.content}</span>
        </div>
      </div>
    );
  }

  /* ================= 2. PROGRESS / UPLOAD STATE ================= */
  if (isProgress) {
      return (
        <div className="w-full py-2">
            <TypingIndicator 
                modelLabel="System" 
                type="uploading"
                label={message.progressLabel || "Processing..."} 
                progress={message.progress}
            />
        </div>
      );
  }

  /* ================= PARSE CHAIN OF THOUGHT (CoT) ================= */
  let thoughtContent: string | null = null;
  let finalDisplayContent = message.content || "";

  if (isAssistant && hasContent) {
    const thoughtMatch = finalDisplayContent.match(/<thinking>([\s\S]*?)<\/thinking>/);

    if (thoughtMatch) {
      thoughtContent = thoughtMatch[1].trim();
      finalDisplayContent = finalDisplayContent.replace(thoughtMatch[0], "").trim();
    } else if (finalDisplayContent.includes("<thinking>")) {
      thoughtContent = finalDisplayContent.replace("<thinking>", "").trim() || "Thinking...";
      finalDisplayContent = ""; 
    }
  }

  /* ================= GET STATUS LABEL ================= */
  const getStatusLabel = () => {
      if (thoughtContent && !finalDisplayContent) return "Thinking...";
      if (isTyping) return "Generating response...";
      return null;
  };

  /* ================= 3. TYPING INDICATOR (SEARCHING) ================= */
  if (isAssistant && isTyping && !hasContent && !thoughtContent) {
      return (
        <div className="w-full py-2">
            <TypingIndicator 
                modelLabel={modelLabel} 
                type="searching"
                label="Analyzing documents..." 
            />
        </div>
      );
  }

  /* ================= 4. NORMAL MESSAGE BUBBLE ================= */

  return (
    <div className="w-full flex transition-opacity duration-200 opacity-100 my-2">
      <div
        className={`
          group flex w-full max-w-3xl gap-4
          animate-in slide-in-from-bottom-2 duration-300
          ${isAssistant ? "justify-start" : "justify-end"}
        `}
      >
        {/* Assistant avatar */}
        {isAssistant && <Avatar role="assistant" />}

        {/* Bubble Container */}
        <div className="max-w-[85%] min-w-[300px]"> 
          
          {/* HEADER: Show Model & Status Label */}
          {isAssistant && (isTyping || (thoughtContent && !finalDisplayContent)) && (
             <div className="mb-1 flex items-center gap-2 text-xs text-gray-400 select-none">
                <span className="font-semibold text-blue-400">{modelLabel}</span>
                <span>•</span>
                <span className="animate-pulse">{getStatusLabel()}</span>
             </div>
          )}

          {/* RENDER THOUGHTS IF EXIST */}
          {isAssistant && thoughtContent && (
            <ThinkingDisclosure content={thoughtContent} />
          )}

          {/* FEEDBACK BAR FOR LAST ASSISTANT MESSAGE */} 
          {isLastAssistant &&
            message.role === "assistant" &&
            message.status === "done" &&
            sessionId &&
            companyDocumentId &&
            revisionNumber !== undefined && (
              <FeedbackBar
                message={message}
                sessionId={sessionId}
                companyDocumentId={companyDocumentId}
                revisionNumber={revisionNumber}
              />
          )}

          {/* ================= BUBBLE ================= */}
          <div
            className={`
              relative rounded-xl px-4 py-3
              text-sm leading-relaxed break-words shadow-sm
              ${
                isAssistant
                  ? isError
                    ? "bg-red-900/20 border border-red-500/30 text-red-200"
                    : "bg-[#1f1f1f] text-gray-100 border border-white/5"
                  : "bg-[#2a2a2a] text-white"
              }
              ${isEdited ? "ring-2 ring-yellow-400/60" : ""}
            `}
          >
            {/* ================= CONTENT RENDER ================= */}
            {hasContent ? (
              <div className="space-y-2">
                <ReactMarkdown
                  skipHtml
                  remarkPlugins={[remarkGfm]}
                  components={{
                    table: ({ node, ...props }) => (
                      <div className="my-4 w-full overflow-x-auto rounded border border-white/10">
                        <table className="min-w-full divide-y divide-white/10 text-left text-sm" {...props} />
                      </div>
                    ),
                    thead: ({ node, ...props }) => <thead className="bg-white/5 text-gray-200" {...props} />,
                    tbody: ({ node, ...props }) => <tbody className="divide-y divide-white/10 bg-transparent" {...props} />,
                    tr: ({ node, ...props }) => <tr className="hover:bg-white/5 transition-colors" {...props} />,
                    th: ({ node, ...props }) => <th className="px-4 py-2 font-semibold text-gray-300 text-left" {...props} />,
                    td: ({ node, ...props }) => <td className="px-4 py-2 text-gray-300 align-top whitespace-pre-wrap" {...props} />,
                    
                    code({ className, children, ...props }) {
                      const match = /language-(\w+)/.exec(className || "");
                      const isInline = !match && !String(children).includes("\n");

                      if (isInline) {
                        return (
                          <code className="rounded bg-black/30 px-1 py-0.5 text-xs text-blue-200 font-mono" {...props}>
                            {children}
                          </code>
                        );
                      }

                      return (
                        <CodeBlock
                          code={String(children).replace(/\n$/, "")}
                          language={match ? match[1] : "text"}
                        />
                      );
                    },
                    ul: ({ node, ...props }) => <ul className="list-disc pl-5 space-y-1 my-2" {...props} />,
                    ol: ({ node, ...props }) => <ol className="list-decimal pl-5 space-y-1 my-2" {...props} />,
                    li: ({ node, ...props }) => <li className="pl-1" {...props} />,
                    a: ({ node, ...props }) => <a className="text-blue-400 hover:underline" target="_blank" rel="noopener noreferrer" {...props} />,
                  }}
                >
                  {finalDisplayContent}
                </ReactMarkdown>
              </div>
            ) : (
               <span className="italic text-gray-500">No content generated.</span>
            )}

            {/* ================= SOURCES BUTTON (REVERTED) ================= */}
            {/* This replaces the individual chips with a single clean button */}
            {isAssistant && message.sources && message.sources.length > 0 && (
                <div className="mt-3 pt-3 border-t border-white/10">
                    <button
                        onClick={() => onViewSources?.(message.sources!)}
                        className="flex items-center gap-2 rounded-md bg-white/5 px-3 py-1.5 text-xs font-medium text-blue-300 hover:bg-white/10 hover:text-blue-200 transition-colors border border-white/5"
                    >
                        <BookOpen size={14} />
                        <span>View {message.sources.length} Source{message.sources.length > 1 ? "s" : ""}</span>
                    </button>
                </div>
            )}

            {/* ================= ACTION BAR ================= */}
            {!isEditing && !isProgress && (
              <div className="absolute -bottom-6 right-0 hidden gap-2 group-hover:flex text-gray-500 bg-[#111] px-2 py-1 rounded-md border border-white/5 shadow-sm z-10">
                {hasContent && (
                  <button
                    onClick={handleCopy}
                    className="hover:text-white p-1 transition-colors"
                    title="Copy"
                  >
                    <Copy size={13} />
                  </button>
                )}

                {isAssistant && isLastAssistant && onRetry && (
                  <button
                    onClick={onRetry}
                    className="hover:text-white p-1 transition-colors"
                    title="Regenerate"
                  >
                    <RotateCcw size={13} />
                  </button>
                )}

                {onDelete && (
                  <button
                    onClick={onDelete}
                    className="hover:text-red-400 p-1 transition-colors"
                    title="Delete Message"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </div>
            )}
          </div>

          {/* ================= FOOTER ================= */}
          <div
            className={`mt-1 text-xs text-gray-600 ${
              isAssistant ? "text-left pl-1" : "text-right pr-1"
            }`}
          >
            {formatTime(message.createdAt)}
            {isEdited && <span className="ml-2 text-yellow-500/50">· edited</span>}
            {isRegenerated && <span className="ml-2 text-blue-500/50">· regenerated</span>}
            {isError && <span className="ml-2 text-red-500/50">· error</span>}
          </div>
        </div>

        {/* User avatar */}
        {isUser && <Avatar role="user" />}
      </div>
    </div>
  );
}