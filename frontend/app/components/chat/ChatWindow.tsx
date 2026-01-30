"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import EmptyState from "../EmptyState";
import InlineMetadataPrompt from "./InlineMetadataPrompt";
import SourceViewerModal from "./SourceViewerModal";
import ChatHeader from "./ChatHeader";
import ProcessingBubble from "./ProcessingBubble";
import Disclaimer from "../ui/Disclaimer"; //  Imported

import { Message, RagSource } from "@/app/lib/types";
import { KAVIN_MODELS, KavinModelId } from "@/app/lib/kavin-models";
import { LLMUIEvent, MetadataRequestField } from "@/app/lib/llm-ui-events";
import { StreamParser } from "@/app/lib/stream-parser";
import { streamChat, updateMetadata, generateChatTitle } from "@/app/lib/api";

import { startJob, abortJob, finishJob } from "@/app/lib/job-manager";
import NetKeyModal from "@/app/components/net/NetKeyModal";

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





/* ================= COMPONENT ================= */

interface ChatWindowProps {
  messages: Message[];
  onUpdateMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  model: KavinModelId;
  sessionId: string | null;
  title?: string;
  onRenameSession?: (title: string) => void;
  onModelChange?: (model: KavinModelId) => void;
  uploadPipeline?: {
    percent: number;
    label: string;
  } | null;

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
  uploadPipeline,
  title = "New Chat",
  onRenameSession,
  onModelChange,
  externalMetadataRequest,
  onExternalMetadataSubmit,
}: ChatWindowProps) {
  
  // --- UI State ---
    const [input, setInput] = useState("");
    const [inlineMetadataFields, setInlineMetadataFields] =
      useState<MetadataRequestField[] | null>(null);

    const hasStarted = messages.length > 0 || Boolean(inlineMetadataFields) || Boolean(uploadPipeline);


  
  // --- Modals ---
  const [netModalOpen, setNetModalOpen] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [activeSources, setActiveSources] = useState<RagSource[]>([]);
  const [netRateLimitedUntil, setNetRateLimitedUntil] = useState<number | null>(null);

  
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);

  // --- Live Model Stage ---
  const [currentStage, setCurrentStage] = useState<string | null>(null);

  // --- Refs ---
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const parserRef = useRef(new StreamParser());
  const textBufferRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const pendingQuestionRef = useRef<string | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const ignoreStreamRef = useRef<boolean>(false);
  const finalizedRef = useRef(false);
  const lastAssistantIdRef = useRef<string | null>(null);
  const jobFinishedRef = useRef(false);
  const modelLabel = useMemo(() => SAFE_MODELS.find((m) => m.id === model)?.label ?? "KavinBase", [model]);


  // --- Blocking Logic ---
  const isTyping =
  !inlineMetadataFields &&
  assistantIdRef.current !== null &&
  messages.some(m => m.status === "typing" || m.status === "streaming");


  const isNetBlocked = model === "net" && netRateLimitedUntil !== null && Date.now() < netRateLimitedUntil;
  const isUIBlocked = Boolean(uploadPipeline)  || Boolean(inlineMetadataFields) || isNetBlocked;



  //  SAFETY: Auto-fix "Stuck Red Button" if backend disconnects or state gets out of sync
    useEffect(() => {
      const lastMsg = messages[messages.length - 1];
      if (lastMsg && (lastMsg.status === 'done' || lastMsg.status === 'error')) {
          if (assistantIdRef.current) {
              assistantIdRef.current = null;
          }
      }
    }, [messages]);


  // Handle external metadata requests (from Sidebar)
  useEffect(() => {
    if (externalMetadataRequest && !inlineMetadataFields) {
        setPendingJobId(externalMetadataRequest.jobId);
        setInlineMetadataFields(externalMetadataRequest.fields);
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
    }
  }, [externalMetadataRequest, inlineMetadataFields]);

  useEffect(() => {
  if (sessionId && pendingQuestionRef.current) {
    const question = pendingQuestionRef.current;
    pendingQuestionRef.current = null;
    generateAIResponse(question);
  }
}, [sessionId]);


  // ----------------------------------------------------------------------
  // 2. INLINE METADATA SUBMISSION
  // ----------------------------------------------------------------------

  async function handleInlineMetadataSubmit(values: Record<string, string>) {
    setInlineMetadataFields(null);
    onExternalMetadataSubmit?.(); 

    

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
        if (!pendingJobId) {
          throw new Error("Missing job id for metadata submission");
        }

        const targetId = pendingJobId;
        
        await updateMetadata(
          {
            job_id: targetId,
            metadata: values,
          },
          (event) => {
             // NO-OP: backend confirmation handled via METADATA_CONFIRMED event
            if (!event?.message) return;

            onUpdateMessages(prev =>
              prev.map(m =>
                m.id === progressMsgId
                  ? {
                      ...m,
                      progress: event.progress ?? m.progress,
                      progressLabel: event.message,
                    }
                  : m
              )
            );
          }
        );

        
        setPendingJobId(null);
        requestAnimationFrame(() => focusInput());

    } catch (err: any) {
        onUpdateMessages((prev) => prev.map(m => 
            m.id === progressMsgId 
            ? { ...m, status: "error", content: ` Process Failed: ${err.message}` } 
            : m
        ));
    }
  }

  // ----------------------------------------------------------------------
  // 3. STANDARD CHAT LOGIC
  // ----------------------------------------------------------------------

  function focusInput() {
    if (!inlineMetadataFields) {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  function handleSend(customInput?: string) {
    if (isUIBlocked) return;
    const text = (customInput ?? input).trim();
    if (!text) return;
    onUpdateMessages((prev) => [...prev, { id: uuidv4(), role: "user", content: text, createdAt: Date.now(), status: "done" }]);
    setInput("");
    if (!sessionId) {
      pendingQuestionRef.current = text;

      onUpdateMessages(prev => [
        ...prev,
        {
          id: uuidv4(),
          role: "system",
          content: "Session not ready yet. Please try sending again.",
          createdAt: Date.now(),
          status: "done",
        },
      ]);

      return;
    }
        
    const userMsgCount = messages.filter(m => m.role === "user").length;
    if (userMsgCount === 0 && sessionId && onRenameSession) {
      generateChatTitle(text).then((t) => onRenameSession(t));
    }
    generateAIResponse(text);
  }

  async function generateAIResponse(question: string) {
    if (!sessionId) return;
    if (isNetBlocked) {
        onUpdateMessages((prev) => [...prev, { id: uuidv4(), role: "assistant", content: "Net rate-limited.", createdAt: Date.now(), status: "done" }]);
        return;
    }
    const controller = startJob(sessionId);
    parserRef.current.reset();
    textBufferRef.current = "";
    jobFinishedRef.current = false;
    ignoreStreamRef.current = false;
    finalizedRef.current = false;
    pendingQuestionRef.current = null;

    assistantIdRef.current = null;
    lastAssistantIdRef.current = null;


    try {
      const stream = await streamChat(
      {
        session_id: sessionId,
        question,
        mode: model,
      },
      controller.signal
    );


      const assistantId = uuidv4();
      assistantIdRef.current = assistantId;
      lastAssistantIdRef.current = assistantId;

      onUpdateMessages(prev => [...prev, { id: assistantId, role: "assistant", content: "", createdAt: Date.now(), status: "typing" }]);

     const reader = stream.getReader();

      const decoder = new TextDecoder();

      while (true) {
        if (ignoreStreamRef.current) break;

        const { value, done } = await reader.read();
        if (done || controller.signal.aborted) break;
        if (!value) continue;

        const chunk = decoder.decode(value, { stream: true });
        const frames = parserRef.current.push(chunk);

        for (const frame of frames) {
          if (frame.type === "event") {
            handleUIEvent(frame.value);
            continue;
          }

          if (frame.type === "text") {
            textBufferRef.current += frame.value;

            if (!rafRef.current) {
              rafRef.current = requestAnimationFrame(() => {
                onUpdateMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? {
                          ...m,
                          content: m.content + frame.value,
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
      }

      rafRef.current && cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      // ✅ FINALIZE ONLY AFTER LOOP
      finalizeAssistant();

    } catch (err) { 
      if (!finalizedRef.current) finalizeAssistant();
       } 
   
  }

  //  FIX: CLEANUP FUNCTION MUST RESET REF IMMEDIATELY
  function finalizeAssistant() {
    if (finalizedRef.current) return;
    finalizedRef.current = true;

    const id = assistantIdRef.current;
    if (!id) return;

    //  Commit final content FIRST
    onUpdateMessages(prev =>
      prev.map(m =>
        m.id === id
          ? {
              ...m,
              status: "done",
              content: textBufferRef.current.length > 0 ? textBufferRef.current: m.content,
            }
          : m
      )
    );

    //  THEN unlock UI
    assistantIdRef.current = null;
    setCurrentStage(null);
   if (!jobFinishedRef.current) {
      jobFinishedRef.current = true;
      finishJob();
    }
}




  function handleUIEvent(event: LLMUIEvent) {
      if (event.type === "REQUEST_METADATA") {
        ignoreStreamRef.current = true;
        abortJob();

        const jobId = (event as { jobId?: string }).jobId;
        setPendingJobId(jobId ?? sessionId);

        setInlineMetadataFields(event.fields);

        assistantIdRef.current = null;
        setCurrentStage(null);

        setTimeout(() => {
          bottomRef.current?.scrollIntoView({ behavior: "smooth" });
        }, 100);

        return;
      }


      if (event.type === "METADATA_CONFIRMED") {
        // Backend confirmed metadata saved
        setInlineMetadataFields(null);
        setPendingJobId(null);
        setCurrentStage(null);
        return;
      }

    if (event.type === "PROGRESS") {
      setCurrentStage(event.label ?? "Processing…");

      const cid = assistantIdRef.current;
      if (!cid) return;

      onUpdateMessages(prev =>
        prev.map(m =>
          m.id === cid
            ? {
                ...m,
                progress: event.value,
                progressLabel: event.label,
              }
            : m
        )
      );


      return;
    }

    if (event.type === "MODEL_STAGE") {
      if (!inlineMetadataFields) {
        setCurrentStage(event.message || event.stage);
      }
      return;
    }
    if (event.type === "ERROR") {
      setCurrentStage(null);
      finalizeAssistant();
      return;
    }


    if (event.type === "NET_RATE_LIMITED") {
      const until = Date.now() + event.retryAfterSec * 1000;
      setNetRateLimitedUntil(until);
      setNetModalOpen(true); // 🔥 ADD

      // Show message immediately
      onUpdateMessages(prev => [
        ...prev,
        {
          id: uuidv4(),
          role: "system",
          content: `Net model rate-limited. Try again in ${event.retryAfterSec}s.`,
          createdAt: Date.now(),
          status: "done",
        },
      ]);
      return;
    }


    if (event.type === "SOURCES") {
      const cid = assistantIdRef.current;
      if (!cid) return;

      onUpdateMessages(prev =>
        prev.map(m => m.id === cid ? { ...m, sources: event.data } : m)
      );
    }

  }


  //  FIX: Force Stop Logic
  function handleStop() {
    ignoreStreamRef.current = true;
    abortJob(); 
    if (!jobFinishedRef.current) {
      jobFinishedRef.current = true;
      finishJob();
    }
    setCurrentStage(null);
    
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

  

 

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length, inlineMetadataFields, messages[messages.length-1]?.content]);

useEffect(() => {
  if (!netRateLimitedUntil) return;

  const timeout = setTimeout(() => {
    setNetRateLimitedUntil(null);
  }, netRateLimitedUntil - Date.now());

  return () => clearTimeout(timeout);
}, [netRateLimitedUntil]);


  // ----------------------------------------------------------------------
  // 4. RENDER
  // ----------------------------------------------------------------------
  return (
    <>
      <div className="relative h-full w-full flex flex-col">
        <ChatHeader 
          title={title}
          isTyping={isTyping}
          activeModel={model}
          onModelChange={(nextModel) => {
            if (nextModel === "net" && netRateLimitedUntil) {
              setNetModalOpen(true);   // 🔥 TRIGGER HERE
            }
            onModelChange?.(nextModel);
          }}
          onRename={onRenameSession || (() => {})}
          onClear={() => onUpdateMessages([])} 
        />


        <div className="relative flex-1 w-full overflow-hidden">
            <div className={`absolute inset-0 flex items-center justify-center transition-all ${hasStarted ? "opacity-0 pointer-events-none" : "opacity-100"}`}>
                <EmptyState 
                  disabled={isUIBlocked}
                  onSend={handleSend}
                  sessionId={sessionId}
                />
            </div>

            <div className={`absolute inset-0 flex flex-col transition-opacity ${hasStarted ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
                <div className="flex-1 overflow-y-auto px-4 pt-6">
                    <div className="mx-auto max-w-3xl space-y-5">
                        {!inlineMetadataFields && (uploadPipeline || currentStage) && (
                          <div className="mb-6 flex justify-start">
                            <ProcessingBubble
                              stepName={uploadPipeline?.label || currentStage!}
                              progress={uploadPipeline?.percent}
                            />
                          </div>
                        )}

                        {messages.map((m, index) => {
                            return (
                                <MessageBubble
                                  key={m.id}
                                  message={m}
                                  modelLabel={modelLabel}
                                  isLastAssistant={
                                    m.role === "assistant" &&
                                    index === messages.map((x) => x.role).lastIndexOf("assistant")
                                  }
                                  sessionId={sessionId}
                                  companyDocumentId={m.sources?.[0]?.company_document_id}
                                  revisionNumber={m.sources?.[0]?.revision_number}
                                  onViewSources={(sources) => {
                                    setActiveSources(sources);
                                    setViewerOpen(true);
                                  }}
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
                      {/* ================= CHAT INPUT ================= */}
                      <ChatInput
                          ref={inputRef}
                          value={input}
                          onChange={setInput}
                          onSend={handleSend}
                          disabled={isUIBlocked}
                          isGenerating={isTyping}
                          onStop={handleStop}
                          sessionId={sessionId}
                          netBlockedUntil={netRateLimitedUntil}
                        />

                        {/*  DISCLAIMER ADDED */}
                        <div className="mt-2">
                            <Disclaimer text="KavinBase can make mistakes. Verify important information." />
                        </div>
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


  