"use client";

import { Message } from "@/app/lib/types";
import Avatar from "../ui/Avatar";
import ReactMarkdown from "react-markdown";
import CodeBlock from "./CodeBlock";
import ThinkingDisclosure from "./ThinkingDisclosure";
import { Copy, Trash2, RotateCcw } from "lucide-react";

// ✅ 1. IMPORT THIS (Required for Table support)
import remarkGfm from "remark-gfm";

/* ================= PROPS ================= */

interface Props {
  message: Message;
  modelLabel?: string;
  isLastAssistant?: boolean;
  isEditing?: boolean;
  onRetry?: () => void;
  onDelete?: () => void;
}

/* ================= HELPERS ================= */

function formatTime(timestamp: number) {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/* ================= PROGRESS BAR ================= */

function ProgressBar({
  percent = 0,
  label,
}: {
  percent?: number;
  label?: string;
}) {
  const safe = Math.min(100, Math.max(0, percent));

  return (
    <div className="mt-2">
      {label && (
        <div className="mb-1 text-xs text-gray-400">{label}</div>
      )}

      <div className="h-2 w-full overflow-hidden rounded-full bg-black/40">
        <div
          className="h-full rounded-full bg-blue-500 transition-all duration-300 ease-out"
          style={{ width: `${safe}%` }}
        />
      </div>

      <div className="mt-1 text-right text-[10px] text-gray-400">
        {safe}%
      </div>
    </div>
  );
}

/* ================= COMPONENT ================= */

export default function MessageBubble({
  message,
  modelLabel = "AI",
  isLastAssistant = false,
  isEditing = false,
  onRetry,
  onDelete,
}: Props) {
  const isAssistant = message.role === "assistant";
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

  const isActive =
    message.status === "typing" ||
    message.status === "streaming";

  const isEdited = Boolean(message.edited);
  const isRegenerated = Boolean(message.regenerated);
  const isError = message.status === "error";

  const hasContent =
    typeof message.content === "string" &&
    message.content.trim().length > 0;

  async function handleCopy() {
    if (!hasContent) return;
    await navigator.clipboard.writeText(message.content || "");
  }

  /* ================= PARSE CHAIN OF THOUGHT (CoT) ================= */
  let thoughtContent: string | null = null;
  let finalDisplayContent = message.content || "";

  // Only parse CoT for assistant messages
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

  /* ================= SYSTEM MESSAGE ================= */

  if (isSystem) {
    return (
      <div className="mx-auto my-2 max-w-xl rounded-md bg-white/5 px-4 py-2 text-center text-sm text-gray-400 italic">
        {message.content}
      </div>
    );
  }

  /* ================= NORMAL MESSAGE ================= */

  return (
    <div className="w-full flex transition-opacity duration-200 opacity-100">
      <div
        className={`
          group flex w-full max-w-3xl gap-3
          animate-message-in
          ${isAssistant ? "justify-start" : "justify-end"}
        `}
      >
        {/* Assistant avatar */}
        {isAssistant && <Avatar role="assistant" />}

        {/* Bubble */}
        <div className="max-w-[85%]"> 
          
          {/* RENDER THOUGHTS IF EXIST */}
          {isAssistant && thoughtContent && (
            <ThinkingDisclosure content={thoughtContent} />
          )}

          <div
            className={`
              relative rounded-2xl px-4 py-3
              text-sm leading-relaxed break-words
              ${
                isAssistant
                  ? isError
                    ? "bg-red-500/10 border border-red-500/20 text-red-400"
                    : "bg-[#1f1f1f]"
                  : "bg-[#2a2a2a]"
              }
              ${isEdited ? "ring-2 ring-yellow-400/60" : ""}
            `}
          >
            {/* ================= PROGRESS ================= */}
            {message.status === "progress" && (
              <ProgressBar
                percent={message.progress}
                label={message.progressLabel}
              />
            )}

            {/* ================= CONTENT ================= */}
            {((finalDisplayContent && finalDisplayContent.length > 0) || isActive) && (
              <div className="space-y-2">
                {finalDisplayContent && (
                  <ReactMarkdown
                    skipHtml
                    remarkPlugins={[remarkGfm]} // ✅ 2. ENABLE TABLE PLUGIN
                    components={{
                      // ✅ 3. ADD TABLE STYLING HERE
                      table: ({ node, ...props }) => (
                        <div className="my-4 w-full overflow-x-auto rounded-lg border border-white/10">
                          <table className="min-w-full divide-y divide-white/10 text-left text-sm" {...props} />
                        </div>
                      ),
                      thead: ({ node, ...props }) => (
                        <thead className="bg-white/5 text-gray-200" {...props} />
                      ),
                      tbody: ({ node, ...props }) => (
                        <tbody className="divide-y divide-white/10 bg-transparent" {...props} />
                      ),
                      tr: ({ node, ...props }) => (
                        <tr className="hover:bg-white/5 transition-colors" {...props} />
                      ),
                      th: ({ node, ...props }) => (
                        <th className="px-4 py-3 font-semibold uppercase tracking-wider text-xs text-gray-400" {...props} />
                      ),
                      td: ({ node, ...props }) => (
                        <td className="px-4 py-3 text-gray-300 align-top whitespace-pre-wrap" {...props} />
                      ),
                      // Existing Code Block Logic
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
                      // List styling
                      ul: ({ node, ...props }) => <ul className="list-disc pl-5 space-y-1 my-2" {...props} />,
                      ol: ({ node, ...props }) => <ol className="list-decimal pl-5 space-y-1 my-2" {...props} />,
                      li: ({ node, ...props }) => <li className="pl-1" {...props} />,
                    }}
                  >
                    {finalDisplayContent}
                  </ReactMarkdown>
                )}

                {/* Typing / streaming indicator */}
                {isActive && (
                  <div className="flex items-center gap-2 text-xs text-gray-400">
                    <span className="font-medium text-gray-300">
                      {modelLabel}
                    </span>
                    
                    {thoughtContent && !finalDisplayContent ? "is thinking" : "is typing"}
                    
                    <span className="flex gap-1">
                      <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:0ms]" />
                      <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:150ms]" />
                      <span className="h-1 w-1 animate-bounce rounded-full bg-gray-400 [animation-delay:300ms]" />
                    </span>
                  </div>
                )}
              </div>
            )}

            {/* ================= ACTION BAR ================= */}
            {!isEditing && (
              <div className="absolute -bottom-7 right-1 hidden gap-2 group-hover:flex text-gray-400">
                {hasContent && (
                  <button
                    onClick={handleCopy}
                    className="hover:text-white"
                    title="Copy"
                  >
                    <Copy size={14} />
                  </button>
                )}

                {isAssistant && isLastAssistant && onRetry && (
                  <button
                    onClick={onRetry}
                    className="hover:text-white"
                    title="Retry"
                  >
                    <RotateCcw size={14} />
                  </button>
                )}

                {onDelete && (
                  <button
                    onClick={onDelete}
                    className="hover:text-red-400"
                    title="Delete"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            )}
          </div>

          {/* ================= FOOTER ================= */}
          <div
            className={`mt-1 text-xs text-gray-500 ${
              isAssistant ? "text-left" : "text-right"
            }`}
          >
            {formatTime(message.createdAt)}
            {isEdited && (
              <span className="ml-2 text-yellow-400">· edited</span>
            )}
            {isRegenerated && (
              <span className="ml-2 text-blue-400">· regenerated</span>
            )}
            {isError && (
              <span className="ml-2 text-red-400">· error</span>
            )}
          </div>
        </div>

        {/* User avatar */}
        {isUser && <Avatar role="user" />}
      </div>
    </div>
  );
}