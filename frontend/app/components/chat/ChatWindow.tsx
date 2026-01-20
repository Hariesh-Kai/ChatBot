"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import EmptyState from "../EmptyState";
import InlineMetadataPrompt from "./InlineMetadataPrompt";
import SourceViewerModal from "./SourceViewerModal";
import ChatHeader from "./ChatHeader";
import ProcessingBubble from "./ProcessingBubble";
import Disclaimer from "../ui/Disclaimer"; // ✅ Imported

import { Message, RagSource } from "@/app/lib/types";
import { KAVIN_MODELS, KavinModelId } from "@/app/lib/kavin-models";
import { LLMUIEvent, MetadataRequestField } from "@/app/lib/llm-ui-events";
import { StreamParser } from "@/app/lib/stream-parser";
import { updateMetadata, generateChatTitle, commitUpload } from "@/app/lib/api";
import { startJob, abortJob, finishJob } from "@/app/lib/job-manager";
import { API_BASE } from "@/app/lib/config";
import NetKeyModal from "@/app/components/net/NetKeyModal";
import { UploadStatus } from "@/app/hooks/useSmartUpload";

/* ================= UTILS ================= */

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

/* ================= CONSTANTS ================= */

const SAFE_MODELS = [
  { id: KAVIN_MODELS.base.id, label: KAVIN_MODELS.base.label },
  { id: KAVIN_MODELS.lite.id, label: KAVIN_MODELS.lite.label },
  { id: KAVIN_MODELS.net.id, label: KAVIN_MODELS.net.label },
];

const THINKING_LABELS = [
    "Initializing Model...", "Loading Context...", "Classifying Intent...", "Reranking Results...", "Generating Response..."
];

/* ================= COMPONENT ================= */

interface ChatWindowProps {
  messages: Message[];
  onUpdateMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  model: KavinModelId;
  sessionId: string | null;
  title?: string;
  onRenameSession?: (title: string) => void;
  onModelChange?: (model: KavinModelId) => void;
  
  externalMetadataRequest?: {
      jobId: string;
      fields: MetadataRequestField[];
      filename: string;
  } | null;
  onExternalMetadataSubmit?: () => void;
}

export default function ChatWindow({
  messages,
  onUpdateMessages,
  model,
  sessionId,
  title = "New Chat",
  onRenameSession,
  onModelChange,
  externalMetadataRequest,
  onExternalMetadataSubmit,
}: ChatWindowProps) {
  
  // --- UI State ---
  const [input, setInput] = useState("");
  const hasStarted = messages.length > 0;
  
  // --- Modals ---
  const [netModalOpen, setNetModalOpen] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [activeSources, setActiveSources] = useState<RagSource[]>([]);
  const [netRateLimitedUntil, setNetRateLimitedUntil] = useState<number | null>(null);

  // --- Upload / Metadata State ---
  const [isUploading, setIsUploading] = useState(false);
  const uploadMsgIdRef = useRef<string | null>(null);
  
  const [inlineMetadataFields, setInlineMetadataFields] = useState<MetadataRequestField[] | null>(null);
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);

  // --- Refs ---
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const parserRef = useRef(new StreamParser());
  const textBufferRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const pendingQuestionRef = useRef<string | null>(null);
  const thinkingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const hasReceivedFirstTokenRef = useRef(false);

  const modelLabel = useMemo(() => SAFE_MODELS.find((m) => m.id === model)?.label ?? "KavinBase", [model]);

  // --- Blocking Logic ---
  const isTyping = messages.some((m) => m.status === "typing" || m.status === "streaming") || assistantIdRef.current !== null;
  const isNetBlocked = model === "net" && netRateLimitedUntil !== null && Date.now() < netRateLimitedUntil;
  const isUIBlocked = Boolean(inlineMetadataFields) || isUploading || isNetBlocked;

  // ✅ SAFETY: Auto-fix "Stuck Red Button" if backend disconnects or state gets out of sync
  useEffect(() => {
      const lastMsg = messages[messages.length - 1];
      if (lastMsg && (lastMsg.status === 'done' || lastMsg.status === 'error')) {
          // If the last message says done, but we still think we are typing, FORCE RESET
          if (assistantIdRef.current) {
              assistantIdRef.current = null;
              hasReceivedFirstTokenRef.current = false;
              stopThinkingSimulation(); 
          }
      }
  }, [messages]);

  // Handle external metadata requests (from Sidebar)
  useEffect(() => {
      if (externalMetadataRequest) {
          setPendingJobId(externalMetadataRequest.jobId);
          setInlineMetadataFields(externalMetadataRequest.fields);
          setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
      }
  }, [externalMetadataRequest]);

  // ----------------------------------------------------------------------
  // 1. UPLOAD HANDLERS
  // ----------------------------------------------------------------------

  function handleUploadStart() {
    setIsUploading(true);
    const msgId = uuidv4();
    uploadMsgIdRef.current = msgId;

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
    const currentId = uploadMsgIdRef.current;
    if (!currentId) return;

    onUpdateMessages((prev) =>
      prev.map((m) =>
        m.id === currentId
          ? { ...m, progress: percent, progressLabel: label }
          : m
      )
    );
  }

  async function handleUploadSuccess(result: any) {
    const currentId = uploadMsgIdRef.current;
    if (currentId) {
        onUpdateMessages((prev) => prev.map(m => m.id === currentId ? {
            ...m,
            status: "done",
            role: "system", 
            content: `📄 **Uploaded:** ${result.filename}` 
        } : m));
        uploadMsgIdRef.current = null;
    }

    if (result.next_action === "WAIT_FOR_METADATA") {
        const fields: MetadataRequestField[] = Object.entries(result.metadata).map(
            ([key, meta]: [string, any]) => ({
                key: key,
                label: key.replace(/_/g, " ").toUpperCase(),
                value: meta.value || "",
                placeholder: `Enter ${key}...`,
                reason: "Required for indexing"
            })
        );
        
        setPendingJobId(result.job_id);
        setInlineMetadataFields(fields);
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
        return;
    }

    if (result.next_action === "READY_TO_COMMIT") {
        await startDirectCommit(result);
    }
  }

  async function startDirectCommit(result: any) {
      const commitMsgId = uuidv4();
      uploadMsgIdRef.current = commitMsgId;

      onUpdateMessages((prev) => [
        ...prev,
        {
            id: commitMsgId,
            role: "assistant",
            content: "",
            createdAt: Date.now(),
            status: "streaming", 
            progress: 10,
            progressLabel: "Finalizing index..."
        }
      ]);

      try {
        await commitUpload({ job_id: result.job_id, metadata: {}, force: true });
        completeUploadProcess(result.filename, result.revision_number);
      } catch (err: any) {
        handleUploadError(err.message);
      }
  }

  function handleUploadError(errorMsg: string) {
    setIsUploading(false);
    const currentId = uploadMsgIdRef.current;

    if (currentId) {
        onUpdateMessages((prev) => prev.map(m => m.id === currentId ? {
            ...m, status: "error", content: `⚠️ **Upload Failed**: ${errorMsg}`
        } : m));
        uploadMsgIdRef.current = null;
    }
  }

  function completeUploadProcess(filename: string, revision: any) {
    setIsUploading(false);
    const currentId = uploadMsgIdRef.current;

    if (currentId) {
        onUpdateMessages((prev) => prev.map(m => m.id === currentId ? {
            ...m,
            role: "assistant",
            status: "done",
            content: `✅ **Success!**\n\nI have indexed **"${filename}"** (Rev ${revision}). You can now ask questions.`
        } : m));
        uploadMsgIdRef.current = null;
    }

    if (messages.length <= 2 && sessionId && onRenameSession) {
        onRenameSession(filename.replace(".pdf", ""));
    }
  }


  // ----------------------------------------------------------------------
  // 2. INLINE METADATA SUBMISSION
  // ----------------------------------------------------------------------

  async function handleInlineMetadataSubmit(values: Record<string, string>) {
    setInlineMetadataFields(null);
    onExternalMetadataSubmit?.(); 

    onUpdateMessages(prev => prev.filter(m => 
        m.status !== 'progress' && m.status !== 'streaming'
    ));

    const progressMsgId = uuidv4();
    onUpdateMessages((prev) => [
        ...prev,
        {
            id: progressMsgId,
            role: "assistant",
            content: "",
            createdAt: Date.now(),
            status: "streaming", 
            progress: 5,
            progressLabel: "Initializing..."
        }
    ]);

    try {
        const targetId = pendingJobId || sessionId!;
        
        await updateMetadata({
            job_id: targetId,
            metadata: values
        }, (logMessage) => {
            let pct = 10;
            const lower = logMessage.toLowerCase();
            if (lower.includes("backing")) pct = 20;
            if (lower.includes("analyz")) pct = 45;
            if (lower.includes("chunk")) pct = 65;
            if (lower.includes("index")) pct = 85;
            if (lower.includes("ready")) pct = 100;

            onUpdateMessages((prev) => prev.map(m => 
                m.id === progressMsgId 
                ? { ...m, progress: pct, progressLabel: logMessage } 
                : m
            ));
        });

        onUpdateMessages((prev) => prev.map(m => 
            m.id === progressMsgId 
            ? { 
                ...m, 
                status: "done",
                content: `✅ **Document Indexed.**\n\nProcessing complete. You can now ask questions.`
              } 
            : m
        ));
        
        setPendingJobId(null);
        focusInput();

    } catch (err: any) {
        onUpdateMessages((prev) => prev.map(m => 
            m.id === progressMsgId 
            ? { ...m, status: "error", content: `❌ Process Failed: ${err.message}` } 
            : m
        ));
    }
  }

  // ----------------------------------------------------------------------
  // 3. STANDARD CHAT LOGIC
  // ----------------------------------------------------------------------

  function focusInput() { requestAnimationFrame(() => inputRef.current?.focus()); }

  function handleSend(customInput?: string) {
    if (isUIBlocked) return;
    const text = (customInput ?? input).trim();
    if (!text) return;
    onUpdateMessages((prev) => [...prev, { id: uuidv4(), role: "user", content: text, createdAt: Date.now(), status: "done" }]);
    setInput("");
    if (!sessionId) { pendingQuestionRef.current = text; return; }
    
    const userMsgCount = messages.filter(m => m.role === "user").length;
    if (userMsgCount === 0 && sessionId && onRenameSession) {
      generateChatTitle(text).then((t) => onRenameSession(t));
    }
    generateAIResponse(text);
  }

  async function generateAIResponse(question: string) {
    if (!sessionId) return;
    if (isNetBlocked) {
        onUpdateMessages((prev) => [...prev, { id: uuidv4(), role: "assistant", content: "⚠️ Net rate-limited.", createdAt: Date.now(), status: "done" }]);
        return;
    }
    const controller = startJob(sessionId);
    parserRef.current.reset();
    textBufferRef.current = "";
    assistantIdRef.current = null;

    try {
      const response = await fetch(`${API_BASE}/chat/`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, question, mode: model }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) throw new Error("Backend request failed");
      const assistantId = uuidv4();
      assistantIdRef.current = assistantId;
      onUpdateMessages(prev => [...prev, { id: assistantId, role: "assistant", content: "", createdAt: Date.now(), status: "typing", progressLabel: "Thinking..." }]);
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
            onUpdateMessages(prev => prev.map(m => m.id === assistantId ? { ...m, progressLabel: undefined } : m));
        }

        const chunk = decoder.decode(value, { stream: true });
        const frames = parserRef.current.push(chunk);

        for (const frame of frames) {
          if (frame.type === "event") { handleUIEvent(frame.value); continue; }
          textBufferRef.current += frame.value;
          if (!rafRef.current) {
            rafRef.current = requestAnimationFrame(() => {
              onUpdateMessages(prev => prev.map(m => m.id === assistantId ? { ...m, content: textBufferRef.current, status: "streaming" } : m));
              rafRef.current = null;
            });
          }
        }
      }
      finalizeAssistant();
    } catch (err) { finalizeAssistant(); } 
    finally { stopThinkingSimulation(); finishJob(); focusInput(); }
  }

  // ✅ FIX: CLEANUP FUNCTION MUST RESET REF IMMEDIATELY
  function finalizeAssistant() {
    stopThinkingSimulation();
    
    // 1. Capture the ID *before* clearing
    const currentId = assistantIdRef.current;
    
    // 2. Clear the ref FIRST to unlock the UI (Red -> Blue Button)
    assistantIdRef.current = null;
    hasReceivedFirstTokenRef.current = false;

    if (!currentId) return;

    // 3. Mark the message as done
    onUpdateMessages(prev => prev.map(m => m.id === currentId ? { 
        ...m, 
        content: textBufferRef.current, 
        status: "done", 
        progressLabel: undefined 
    } : m));
  }

  function handleUIEvent(event: LLMUIEvent) {
    if (event.type === "REQUEST_METADATA") {
      if (sessionId) abortJob(`chat:${sessionId}`);
      setInlineMetadataFields(event.fields); 
      return;
    }
    if (event.type === "SOURCES") {
        const sources = event.data as RagSource[];
        if (assistantIdRef.current) {
            const cid = assistantIdRef.current;
            onUpdateMessages(prev => prev.map(m => m.id === cid ? { ...m, sources } : m));
        }
    }
  }

  // ✅ FIX: Force Stop Logic
  function handleStop() {
    abortJob(); 
    stopThinkingSimulation(); 
    finishJob();
    
    if (rafRef.current) { 
        cancelAnimationFrame(rafRef.current); 
        rafRef.current = null; 
    }
    
    // 🔥 Force finalize even if ref is missing
    const currentId = assistantIdRef.current;
    if (currentId) {
        finalizeAssistant();
    } else {
        // Fallback: If ref is missing but we are "typing", find the last typing message and kill it
        onUpdateMessages(prev => prev.map(m => 
            (m.status === 'typing' || m.status === 'streaming') 
            ? { ...m, status: 'done', content: m.content || "Stopped." } 
            : m
        ));
    }
  }

  function startThinkingSimulation(msgId: string) {
      let index = 0;
      if (thinkingIntervalRef.current) clearInterval(thinkingIntervalRef.current);
      thinkingIntervalRef.current = setInterval(() => {
          index = (index + 1) % THINKING_LABELS.length;
          onUpdateMessages((prev) => prev.map((m) => m.id === msgId ? { ...m, progressLabel: THINKING_LABELS[index] } : m));
      }, 1500); 
  }

  function stopThinkingSimulation() {
      if (thinkingIntervalRef.current) { clearInterval(thinkingIntervalRef.current); thinkingIntervalRef.current = null; }
  }

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length, inlineMetadataFields, messages[messages.length-1]?.content]);

  // ----------------------------------------------------------------------
  // 4. RENDER
  // ----------------------------------------------------------------------
  return (
    <>
      <div className="relative h-full w-full flex flex-col">
        <ChatHeader 
            title={title} isTyping={isTyping} activeModel={model} 
            onModelChange={onModelChange || (() => {})} onRename={onRenameSession || (() => {})} onClear={() => onUpdateMessages([])} 
        />

        <div className="relative flex-1 w-full overflow-hidden">
            <div className={`absolute inset-0 flex items-center justify-center transition-all ${hasStarted ? "opacity-0 pointer-events-none" : "opacity-100"}`}>
                <EmptyState 
                    disabled={isUIBlocked} onSend={handleSend} sessionId={sessionId}
                    onUploadStart={handleUploadStart} onUploadProgress={handleUploadProgress} 
                    onUploadSuccess={handleUploadSuccess} onUploadError={handleUploadError}
                />
            </div>

            <div className={`absolute inset-0 flex flex-col transition-opacity ${hasStarted ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
                <div className="flex-1 overflow-y-auto px-4 pt-6">
                    <div className="mx-auto max-w-3xl space-y-5">
                        {messages.map((m, index) => {
                            if (m.status === "streaming" && m.progressLabel && !m.content) {
                                return (
                                    <div key={m.id} className="mb-6 flex justify-start">
                                        <ProcessingBubble 
                                            stepName={m.progressLabel} 
                                            progress={m.progress || 0} 
                                        />
                                    </div>
                                );
                            }

                            return (
                                <MessageBubble
                                    key={m.id} message={m} modelLabel={modelLabel}
                                    isLastAssistant={m.role === "assistant" && index === messages.map((x) => x.role).lastIndexOf("assistant")}
                                    onViewSources={(sources) => { setActiveSources(sources); setViewerOpen(true); }}
                                />
                            );
                        })}

                        {inlineMetadataFields && (
                            <InlineMetadataPrompt
                                fields={inlineMetadataFields}
                                onSubmit={handleInlineMetadataSubmit}
                            />
                        )}

                        <div ref={bottomRef} />
                    </div>
                </div>

                <div className="border-t border-white/10 bg-black pt-4 pb-2">
                    <div className="mx-auto max-w-3xl px-4">
                        <ChatInput
                            ref={inputRef} value={input} onChange={setInput} onSend={handleSend}
                            disabled={isUIBlocked} isGenerating={isTyping} onStop={handleStop} sessionId={sessionId}
                            onUploadStart={handleUploadStart} onUploadProgress={handleUploadProgress} 
                            onUploadSuccess={handleUploadSuccess} onUploadError={handleUploadError}
                        />
                        {/* ✅ DISCLAIMER ADDED */}
                        <div className="mt-2">
                            <Disclaimer text="KavinBase can make mistakes. Verify important information." />
                        </div>
                    </div>
                </div>
            </div>
        </div>
      </div>

      <NetKeyModal open={netModalOpen} onClose={() => setNetModalOpen(false)} />
      <SourceViewerModal open={viewerOpen} sources={activeSources} onClose={() => setViewerOpen(false)} />
    </>
  );
}