"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import EmptyState from "../EmptyState";
import InlineMetadataPrompt from "./InlineMetadataPrompt";
import RagDebugPanel from "@/app/components/debug/RagDebugPanel";
import SourceViewerModal from "./SourceViewerModal";
// ✅ NEW: Import ChatHeader
import ChatHeader from "./ChatHeader"; 
import { Message, RagSource } from "@/app/lib/types";
import { KAVIN_MODELS, KavinModelId } from "@/app/lib/kavin-models";
import { LLMUIEvent, MetadataRequestField } from "@/app/lib/llm-ui-events";
import { StreamParser } from "@/app/lib/stream-parser";
import { updateMetadata, generateChatTitle, commitUpload } from "@/app/lib/api";
import { startJob, abortJob, finishJob } from "@/app/lib/job-manager";
import { API_BASE } from "@/app/lib/config";
import NetKeyModal from "@/app/components/net/NetKeyModal";
import { hasNetApiKey } from "@/app/lib/net-key-store";
import { UploadStatus } from "@/app/hooks/useSmartUpload";

/* =========================================================
   SAFE UTILS
========================================================= */

function uuidv4() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0,
      v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/* =========================================================
   SAFE MODEL REGISTRY
========================================================= */

type SafeModel = {
  id: KavinModelId;
  label: string;
};

const SAFE_MODELS: SafeModel[] = [
  { id: KAVIN_MODELS.base.id, label: KAVIN_MODELS.base.label },
  { id: KAVIN_MODELS.lite.id, label: KAVIN_MODELS.lite.label },
  { id: KAVIN_MODELS.net.id, label: KAVIN_MODELS.net.label },
];

const THINKING_LABELS = [
    "Initializing Model...",
    "Loading Context...", 
    "Classifying Intent...",
    "Reranking Results...",
    "Generating Response..."
];

/* ================= PROPS ================= */

interface ChatWindowProps {
  messages: Message[];
  onUpdateMessages: (
    updater: Message[] | ((prev: Message[]) => Message[])
  ) => void;
  model: KavinModelId;
  sessionId: string | null;
  // ✅ NEW PROPS NEEDED FOR HEADER
  title?: string;
  onRenameSession?: (title: string) => void;
  onModelChange?: (model: KavinModelId) => void;
}

/* ================= COMPONENT ================= */

export default function ChatWindow({
  messages,
  onUpdateMessages,
  model,
  sessionId,
  title = "New Chat", // Default title
  onRenameSession,
  onModelChange,
}: ChatWindowProps) {
  const [input, setInput] = useState("");
  const hasStarted = messages.length > 0;

  const [netModalOpen, setNetModalOpen] = useState(false);
  const [netRateLimitedUntil, setNetRateLimitedUntil] = useState<number | null>(null);

  const [metadataFields, setMetadataFields] =
    useState<MetadataRequestField[] | null>(null);
  const [metadataPending, setMetadataPending] = useState(false);
  
  const [isUploading, setIsUploading] = useState(false);
  const [currentUploadMsgId, setCurrentUploadMsgId] = useState<string | null>(null);

  const [viewerOpen, setViewerOpen] = useState(false);
  const [activeSources, setActiveSources] = useState<RagSource[]>([]);

  const [debugOpen, setDebugOpen] = useState(false);

  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const parserRef = useRef(new StreamParser());
  const textBufferRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const pendingQuestionRef = useRef<string | null>(null);
  
  const thinkingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const hasReceivedFirstTokenRef = useRef(false);

  /* ================= MODEL LABEL ================= */

  const modelLabel = useMemo(() => {
    return (
      SAFE_MODELS.find((m) => m.id === model)?.label ?? "KavinBase"
    );
  }, [model]);

  /* ================= STATE ================= */

  const isTyping =
    messages.some(
      (m) => m.status === "typing" || m.status === "streaming"
    ) || assistantIdRef.current !== null;


  const isNetBlocked =
    model === "net" &&
    netRateLimitedUntil !== null &&
    Date.now() < netRateLimitedUntil;

  const isUIBlocked = metadataPending || isNetBlocked || isUploading;

  /* ================= HELPERS ================= */

  function focusInput() {
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  function createAssistantMessage(): string {
    const id = uuidv4();

    onUpdateMessages((prev) => [
      ...prev,
      {
        id,
        role: "assistant",
        content: "",
        createdAt: Date.now(),
        status: "typing",
        progressLabel: "Initializing..." 
      },
    ]);

    return id;
  }

  function finalizeAssistant() {
    stopThinkingSimulation(); 
    const id = assistantIdRef.current;
    if (!id) return;

    onUpdateMessages((prev) =>
      prev.map((m) =>
        m.id === id
          ? {
              ...m,
              content: textBufferRef.current,
              status: "done",
              progressLabel: undefined 
            }
          : m
      )
    );

    assistantIdRef.current = null;
    hasReceivedFirstTokenRef.current = false;
  }

  /* ================= THINKING SIMULATION ================= */
  
  function startThinkingSimulation(msgId: string) {
      let index = 0;
      if (thinkingIntervalRef.current) clearInterval(thinkingIntervalRef.current);
      
      thinkingIntervalRef.current = setInterval(() => {
          index = (index + 1) % THINKING_LABELS.length;
          const label = THINKING_LABELS[index];
          
          onUpdateMessages((prev) => prev.map((m) => 
              m.id === msgId ? { ...m, progressLabel: label } : m
          ));
      }, 1500); 
  }

  function stopThinkingSimulation() {
      if (thinkingIntervalRef.current) {
          clearInterval(thinkingIntervalRef.current);
          thinkingIntervalRef.current = null;
      }
  }

  /* ================= UPLOAD HANDLERS ================= */

  function handleUploadStart() {
    setIsUploading(true);
    const msgId = uuidv4();
    setCurrentUploadMsgId(msgId);

    onUpdateMessages((prev) => [
      ...prev,
      {
        id: msgId,
        role: "assistant",
        content: "",
        createdAt: Date.now(),
        status: "progress",
        progress: 0,
        progressLabel: "Starting upload...",
      },
    ]);
  }

  function handleUploadProgress(status: UploadStatus, percent: number, label: string) {
    if (!currentUploadMsgId) return;

    onUpdateMessages((prev) =>
      prev.map((m) =>
        m.id === currentUploadMsgId
          ? { ...m, progress: percent, progressLabel: label }
          : m
      )
    );
  }

  async function handleUploadSuccess(result: any) {
    try {
        handleUploadProgress("processing", 85, "Finalizing index...");
        
        await commitUpload({
            job_id: result.job_id,
            metadata: {},
            force: true
        });

        setIsUploading(false);
        
        if (currentUploadMsgId) {
            onUpdateMessages((prev) => prev.map(m => m.id === currentUploadMsgId ? {
                ...m,
                role: "system",
                status: "done",
                content: `✅ Successfully processed "${result.filename}" (v${result.revision_number}). \n\nYou can now ask questions about this document.`
            } : m));
            setCurrentUploadMsgId(null);
        }

        if (messages.length === 0 && sessionId && onRenameSession) {
            onRenameSession(result.filename.replace(".pdf", ""));
        }

    } catch (err: any) {
        setIsUploading(false);
        handleUploadError(err.message || "Failed to commit document (Phase 2).");
    }
  }

  function handleUploadError(errorMsg: string) {
    setIsUploading(false);

    if (currentUploadMsgId) {
        onUpdateMessages((prev) => prev.map(m => m.id === currentUploadMsgId ? {
            ...m,
            status: "error",
            content: `⚠️ **Upload Failed**: ${errorMsg}`
        } : m));
        setCurrentUploadMsgId(null);
    } else {
        const errorBubble: Message = {
            id: uuidv4(),
            role: "assistant",
            content: `⚠️ **Upload Failed**: ${errorMsg}`,
            createdAt: Date.now(),
            status: "error",
        };
        onUpdateMessages((prev) => [...prev, errorBubble]);
    }
  }

  /* ================= STREAMING ================= */

  async function generateAIResponse(question: string) {
    if (!sessionId || metadataPending) return;

    if (isNetBlocked) {
      onUpdateMessages((prev) => [
        ...prev,
        {
          id: uuidv4(),
          role: "assistant",
          content: "⚠️ Net rate-limited. Falling back to Base model.",
          createdAt: Date.now(),
          status: "done",
        },
      ]);
      return;
    }

    const controller = startJob(sessionId);

    parserRef.current.reset();
    textBufferRef.current = "";
    assistantIdRef.current = null;

    try {
      const response = await fetch(`${API_BASE}/chat/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          question,
          mode: model,
        }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error("Backend request failed");
      }

      const assistantId = createAssistantMessage();
      assistantIdRef.current = assistantId;
      startThinkingSimulation(assistantId);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done || controller.signal.aborted) break;
        if (!value) continue;

        if (!hasReceivedFirstTokenRef.current) {
            hasReceivedFirstTokenRef.current = true;
            stopThinkingSimulation();
            onUpdateMessages((prev) => prev.map((m) => m.id === assistantId ? { ...m, progressLabel: undefined } : m));
        }

        const chunk = decoder.decode(value, { stream: true });
        const frames = parserRef.current.push(chunk);

        for (const frame of frames) {
          if (frame.type === "event") {
            handleUIEvent(frame.value);
            continue;
          }

          textBufferRef.current += frame.value;

          if (!rafRef.current && assistantIdRef.current) {
            rafRef.current = requestAnimationFrame(() => {
              onUpdateMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantIdRef.current
                    ? {
                        ...m,
                        content: textBufferRef.current,
                        status: "streaming",
                      }
                    : m
                )
              );
              rafRef.current = null;
            });
          }
        }
      }

      const flushed = parserRef.current.flush();
      for (const f of flushed) {
        if (f.type === "text") {
          textBufferRef.current += f.value;
        }
      }

      finalizeAssistant();
    } catch (err) {
      finalizeAssistant();
    } finally {
      stopThinkingSimulation(); 
      finishJob();
      focusInput();
    }
  }


  /* ================= UI EVENTS ================= */

  function handleUIEvent(event: LLMUIEvent) {
    if (event.type === "REQUEST_METADATA") {
      if (sessionId) abortJob(`chat:${sessionId}`);
      setMetadataFields(event.fields);
      setMetadataPending(true);
      return;
    }

    if (event.type === "ERROR") {
      if (sessionId) abortJob(`chat:${sessionId}`);
      onUpdateMessages((prev) => [
        ...prev,
        {
          id: uuidv4(),
          role: "assistant",
          content: `⚠️ ${event.message}`,
          createdAt: Date.now(),
          status: "done",
        },
      ]);
      return;
    }

    if (event.type === "SOURCES") {
        const sources = event.data as RagSource[];
        if (assistantIdRef.current) {
            const currentId = assistantIdRef.current;
            onUpdateMessages((prev) =>
                prev.map((m) =>
                    m.id === currentId
                        ? { ...m, sources: sources }
                        : m
                )
            );
        }
    }
  }

  /* ================= STOP ================= */

  function handleStop() {
    if (!sessionId) return;

    abortJob();
    stopThinkingSimulation(); 
    finishJob();

    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }

    if (assistantIdRef.current) {
      onUpdateMessages((prev) =>
        prev.map((m) =>
          m.id === assistantIdRef.current
            ? {
                ...m,
                content: textBufferRef.current,
                status: "done",
              }
            : m
        )
      );
    }

    assistantIdRef.current = null;
    hasReceivedFirstTokenRef.current = false;
  }

  /* ================= SEND ================= */

  function handleSend(customInput?: string) {
    if (isUIBlocked) return;

    if (model === "net" && !hasNetApiKey()) {
      setNetModalOpen(true);
      return;
    }

    const text = (customInput ?? input).trim();
    if (!text) return;

    onUpdateMessages((prev) => [
      ...prev,
      {
        id: uuidv4(),
        role: "user",
        content: text,
        createdAt: Date.now(),
        status: "done",
      },
    ]);

    setInput("");

    if (!sessionId) {
      pendingQuestionRef.current = text;
      return;
    }

    const userMsgCount = messages.filter(m => m.role === "user").length;
    if (userMsgCount === 0 && sessionId && onRenameSession) {
      generateChatTitle(text).then((title) => {
          onRenameSession(title);
      });
    }

    generateAIResponse(text);
  }

  /* ================= EFFECTS ================= */

  useEffect(() => {
    if (
      netRateLimitedUntil &&
      Date.now() >= netRateLimitedUntil
    ) {
      setNetRateLimitedUntil(null);
    }
  }, [netRateLimitedUntil]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, metadataPending, messages[messages.length - 1]?.progress]); 

  /* ================= RENDER ================= */

  return (
    <>
      <div className="relative h-full w-full flex flex-col">
        {/* ✅ HEADER RESTORED */}
        <ChatHeader 
            title={title}
            isTyping={isTyping}
            activeModel={model}
            onModelChange={onModelChange || (() => {})}
            onRename={onRenameSession || (() => {})}
            onClear={() => onUpdateMessages([])}
        />

        <div className="relative flex-1 w-full overflow-hidden">
            <div
            className={`absolute inset-0 flex items-center justify-center transition-all
            ${hasStarted ? "opacity-0 pointer-events-none" : "opacity-100"}`}
            >
            <EmptyState 
                disabled={isUIBlocked} 
                onSend={handleSend}
                sessionId={sessionId}
                onUploadStart={handleUploadStart}
                onUploadProgress={handleUploadProgress} 
                onUploadSuccess={handleUploadSuccess}
                onUploadError={handleUploadError}
            />
            </div>

            <div
            className={`absolute inset-0 flex flex-col transition-opacity
            ${hasStarted ? "opacity-100" : "opacity-0 pointer-events-none"}`}
            >
            <div className="flex-1 overflow-y-auto px-4 pt-6">
                <div className="mx-auto max-w-3xl space-y-5">
                {messages.map((m, index) => (
                    <MessageBubble
                    key={m.id}
                    message={m}
                    modelLabel={modelLabel}
                    isLastAssistant={
                        m.role === "assistant" &&
                        index ===
                        messages
                            .map((x) => x.role)
                            .lastIndexOf("assistant")
                    }
                    onViewSources={(sources) => {
                        setActiveSources(sources);
                        setViewerOpen(true);
                    }}
                    />
                ))}

                {metadataPending && metadataFields && (
                    <InlineMetadataPrompt
                    fields={metadataFields}
                    onSubmit={async (values) => {
                        if (!sessionId) return;
                        await updateMetadata({
                        job_id: sessionId,
                        metadata: values,
                        });
                        setMetadataFields(null);
                        setMetadataPending(false);
                        focusInput();
                    }}
                    />
                )}

                {process.env.NODE_ENV === "development" &&
                    sessionId && (
                    <RagDebugPanel
                        sessionId={sessionId}
                        open={debugOpen}
                    />
                    )}

                <div ref={bottomRef} />
                </div>
            </div>

            <div className="border-t border-white/10 bg-black py-4">
                <div className="mx-auto max-w-3xl px-4">
                <ChatInput
                    ref={inputRef}
                    value={input}
                    onChange={setInput}
                    onSend={handleSend}
                    disabled={isUIBlocked} 
                    isGenerating={isTyping}
                    onStop={handleStop}
                    sessionId={sessionId}
                    onUploadStart={handleUploadStart}
                    onUploadProgress={handleUploadProgress} 
                    onUploadSuccess={handleUploadSuccess}
                    onUploadError={handleUploadError}
                />
                </div>
            </div>
            </div>
        </div>
      </div>

      <NetKeyModal
        open={netModalOpen}
        onClose={() => setNetModalOpen(false)}
      />

      <SourceViewerModal 
        open={viewerOpen} 
        sources={activeSources} 
        onClose={() => setViewerOpen(false)} 
      />
    </>
  );
}