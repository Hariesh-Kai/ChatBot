"use client";

import { useEffect, useRef, useState, useMemo, useCallback, type RefObject } from "react";
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
import { LLMUIEvent, MetadataRequestField, UI_EVENT_PREFIX, parseLLMUIEvent } from "@/app/lib/llm-ui-events";
import type { UploadStatus } from "@/app/hooks/useSmartUpload";
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

const STAGE_UI: Record<string, { label: string; step: string }> = {
  intent: {
    label: "Understanding your question...",
    step: "Understand question",
  },
  retrieval: {
    label: "Searching your documents...",
    step: "Search documents",
  },
  reranking: {
    label: "Choosing the best sections...",
    step: "Choose sections",
  },
  chunks: {
    label: "Picked the most relevant sections.",
    step: "Pick sections",
  },
  generation: {
    label: "Writing the answer...",
    step: "Write answer",
  },
};

function resolveStageUI(stage?: string, message?: string, didUseDocs?: boolean) {
  const stageKey = (stage || "").toLowerCase();
  const msg = (message || "").toLowerCase();

  if (msg.includes("retrieval disabled")) {
    return {
      label: "Document search is off for this session.",
      step: "Document search off",
    };
  }

  if (msg.includes("no rag") || msg.includes("no documents")) {
    return {
      label: "Writing the answer (no documents).",
      step: "Write answer",
    };
  }

  if (stageKey === "generation" && !didUseDocs) {
    return {
      label: "Writing the answer (no documents).",
      step: "Write answer",
    };
  }

  if (STAGE_UI[stageKey]) return STAGE_UI[stageKey];

  if (message && message.trim()) {
    return {
      label: message.replace(/\bRAG\b/gi, "documents"),
      step: "Working",
    };
  }

  return {
    label: "Preparing your answer...",
    step: "Working",
  };
}





/* ================= COMPONENT ================= */

interface ChatWindowProps {
  messages: Message[];
  onUpdateMessages: (updater: Message[] | ((prev: Message[]) => Message[])) => void;
  model: KavinModelId;
  sessionId: string | null;
  userLabel?: string;
  devSettings?: any;
  title?: string;
  onRenameSession?: (title: string) => void;
  onModelChange?: (model: KavinModelId) => void;
  metadataActive?: boolean;
  uploadPipeline?: {
    percent: number;
    label: string;
  } | null;
  onUploadStart?: (file: File) => void;
  onUploadProgress?: (status: UploadStatus, percent: number, label: string) => void;
  onUploadSuccess?: (result: any) => void;
  onUploadError?: (error: string) => void;
  inputRefExternal?: RefObject<HTMLTextAreaElement>;

  externalMetadataRequest?: {
      jobId: string;
      fields: MetadataRequestField[];
      filename: string;
  } | null;
  onExternalMetadataSubmit?: (
  jobId: string,
  fields: MetadataRequestField[]
) => Promise<void> | void;
}

type FinalizeOptions = {
  status?: "done" | "error";
  content?: string;
};

export default function ChatWindow({
  messages,
  onUpdateMessages,
  model,
  sessionId,
  userLabel,
  devSettings,
  uploadPipeline,
  title = "New Chat",
  onRenameSession,
  onModelChange,
  onUploadStart,
  onUploadProgress,
  onUploadSuccess,
  onUploadError,
  inputRefExternal,
  externalMetadataRequest,
  onExternalMetadataSubmit,
}: ChatWindowProps) {
  
  // --- UI State ---
    const [input, setInput] = useState("");
    const [inlineMetadataFields, setInlineMetadataFields] =
      useState<MetadataRequestField[] | null>(null);

    const hasStarted = messages.length > 0 || Boolean(inlineMetadataFields) || Boolean(uploadPipeline);
    const ragVisualizationEnabled = devSettings?.emit_model_stage_events ?? true;
    const showConfidence = devSettings?.emit_answer_confidence ?? true;


  
  // --- Modals ---
  const [netModalOpen, setNetModalOpen] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [activeSources, setActiveSources] = useState<RagSource[]>([]);
  const [netRateLimitedUntil, setNetRateLimitedUntil] = useState<number | null>(null);
  const [ragSteps, setRagSteps] = useState<{ stage: string; message: string; ts: number }[]>([]);
  const [ragPanelOpen, setRagPanelOpen] = useState(false);

  
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);

  // --- Live Model Stage ---
  const [currentStage, setCurrentStage] = useState<string | null>(null);

  // --- Refs ---
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const localInputRef = useRef<HTMLTextAreaElement | null>(null);
  const inputRef = inputRefExternal ?? localInputRef;
  const parserRef = useRef(new StreamParser());
  const textBufferRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const pendingQuestionRef = useRef<string | null>(null);
  const pendingTitleRef = useRef<string | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const ignoreStreamRef = useRef<boolean>(false);
  const finalizedRef = useRef(false);
  const lastAssistantIdRef = useRef<string | null>(null);
  const jobFinishedRef = useRef(false);
  const externalMetadataSeenJobIdRef = useRef<string | null>(null);
  const lastModelRef = useRef<KavinModelId>(model);
  const modelLabel = useMemo(() => SAFE_MODELS.find((m) => m.id === model)?.label ?? "KavinBase v1.0", [model]);
  const lastMessageContent = messages[messages.length - 1]?.content;


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

  useEffect(() => {
    if (!ragVisualizationEnabled) {
      setRagSteps([]);
    }
  }, [ragVisualizationEnabled]);

  useEffect(() => {
    if (lastModelRef.current === model) return;
    const previousModel = lastModelRef.current;
    onUpdateMessages((prev) =>
      prev.map((m) =>
        m.role === "assistant" && !m.model ? { ...m, model: previousModel } : m
      )
    );
    lastModelRef.current = model;
  }, [model, onUpdateMessages]);


  // Handle external metadata requests (from Sidebar)
  useEffect(() => {
    if (!externalMetadataRequest) return;
    if (inlineMetadataFields) return;

    // Prevent the form from "popping back open" after we hide it on submit,
    // while the parent still holds externalMetadataRequest.
    if (externalMetadataSeenJobIdRef.current === externalMetadataRequest.jobId) return;

    externalMetadataSeenJobIdRef.current = externalMetadataRequest.jobId;
    setPendingJobId(externalMetadataRequest.jobId);
    setInlineMetadataFields(externalMetadataRequest.fields);
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  }, [externalMetadataRequest, inlineMetadataFields]);

  // ----------------------------------------------------------------------
  // 2. INLINE METADATA SUBMISSION
  // ----------------------------------------------------------------------

async function handleInlineMetadataSubmit(values: Record<string, string>) {
  if (!pendingJobId || !inlineMetadataFields) {
    console.error("Missing jobId or metadata fields", {
      pendingJobId,
      inlineMetadataFields,
    });
    return;
  }

  const jobId = pendingJobId;

  // Build fields with user-entered values
  const filledFields = inlineMetadataFields.map((f) => ({
    ...f,
    value: values[f.key] ?? f.value ?? "",
  }));

  // Hide the form immediately so the upload progress bubble can show.
  setInlineMetadataFields(null);
  setCurrentStage("Submitting metadata...");

  try {
    if (onExternalMetadataSubmit) {
      await Promise.resolve(onExternalMetadataSubmit(jobId, filledFields));
    } else {
      const metadata = filledFields.reduce((acc, f) => {
        acc[f.key] = typeof f.value === "string" ? f.value : String(f.value ?? "");
        return acc;
      }, {} as Record<string, string>);

      await updateMetadata(
        { job_id: jobId, metadata, force: true },
        (evt) => {
          if (evt?.message) setCurrentStage(evt.message);
        }
      );
    }
    setPendingJobId(null);
  } catch (err: any) {
    // If submission fails, restore the form so the user can retry.
    setPendingJobId(jobId);
    setInlineMetadataFields(filledFields);

    onUpdateMessages((prev) => [
      ...prev,
      {
        id: uuidv4(),
        role: "system",
        content: err?.message || "Metadata submission failed. Please try again.",
        createdAt: Date.now(),
        status: "done",
      },
    ]);
  } finally {
    setCurrentStage(null);
    ignoreStreamRef.current = false;
  }
}


  // ----------------------------------------------------------------------
  // 3. STANDARD CHAT LOGIC
  // ----------------------------------------------------------------------

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
    if (userMsgCount === 0 && sessionId && onRenameSession && title === "New Chat") {
      pendingTitleRef.current = text;
    }
    generateAIResponse(text);
  }

  //  FIX: CLEANUP FUNCTION MUST RESET REF IMMEDIATELY
  const finalizeAssistant = useCallback((opts?: FinalizeOptions) => {
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
              status: opts?.status ?? "done",
              content:
                typeof opts?.content === "string"
                  ? opts.content
                  : textBufferRef.current.length > 0
                  ? textBufferRef.current
                  : m.content,
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

    const pendingTitle = pendingTitleRef.current;
    if (pendingTitle && onRenameSession && title === "New Chat") {
      pendingTitleRef.current = null;
      generateChatTitle(pendingTitle).then((t) => onRenameSession(t));
    } else if (pendingTitle) {
      pendingTitleRef.current = null;
    }
  }, [onUpdateMessages, onRenameSession, title]);

  const handleUIEvent = useCallback((event: LLMUIEvent) => {
    if (event.type === "REQUEST_METADATA") {
      ignoreStreamRef.current = true;
      abortJob();

      // Convert the placeholder assistant bubble into a helpful message instead of
      // showing "No content generated."
      const cid = assistantIdRef.current;
      if (cid) {
        onUpdateMessages((prev) =>
          prev.map((m) =>
            m.id === cid
              ? {
                  ...m,
                  status: "done",
                  content:
                    m.content?.trim().length > 0
                      ? m.content
                      : "Information required. Please fill the details below to continue.",
                }
              : m
          )
        );
      }

      setPendingJobId(event.jobId ?? sessionId);

      setInlineMetadataFields(event.fields);

      assistantIdRef.current = null;
      setCurrentStage(null);

      setTimeout(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      }, 100);

      return;
    }

    if (event.type === "SYSTEM_MESSAGE") {
      const text = (event.text || "").trim();
      const lower = text.toLowerCase();
      if (lower === "thinking..." || lower === "thinking…" || lower === "thinking") {
        return;
      }
      onUpdateMessages((prev) => [
        ...prev,
        {
          id: uuidv4(),
          role: "system",
          content: text,
          createdAt: Date.now(),
          status: "done",
        },
      ]);
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
      const ui = resolveStageUI(event.stage, event.message);
      if (!inlineMetadataFields) {
        setCurrentStage(ui.label);
      }
      const cid = assistantIdRef.current;
      if (cid) {
        onUpdateMessages((prev) =>
          prev.map((m) =>
            m.id === cid
              ? {
                  ...m,
                  progressLabel: ui.label,
                }
              : m
          )
        );
      }
      if (ragVisualizationEnabled) {
        setRagSteps((prev) => {
          const last = prev[prev.length - 1];
          if (last && last.stage === ui.step) return prev;
          return [...prev, { stage: ui.step, message: ui.label, ts: Date.now() }];
        });
      }
      return;
    }
    if (event.type === "ANSWER_CONFIDENCE") {
      const cid = assistantIdRef.current || lastAssistantIdRef.current;
      if (!cid) return;
      onUpdateMessages((prev) =>
        prev.map((m) =>
          m.id === cid
            ? {
                ...m,
                confidence: {
                  confidence: event.confidence,
                  level: event.level,
                },
              }
            : m
        )
      );
      return;
    }
    if (event.type === "ERROR") {
      const msg = event.message || "Something went wrong.";
      if (assistantIdRef.current) {
        finalizeAssistant({ status: "error", content: msg });
      } else {
        setCurrentStage(null);
        onUpdateMessages((prev) => [
          ...prev,
          {
            id: uuidv4(),
            role: "system",
            content: msg,
            createdAt: Date.now(),
            status: "done",
          },
        ]);
      }
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
  }, [inlineMetadataFields, onUpdateMessages, sessionId, ragVisualizationEnabled, finalizeAssistant]);

  const generateAIResponse = useCallback(async (question: string) => {
    if (!sessionId) return;
    if (isNetBlocked) {
        onUpdateMessages((prev) => [
          ...prev,
          {
            id: uuidv4(),
            role: "assistant",
            model,
            content: "Net rate-limited.",
            createdAt: Date.now(),
            status: "done",
          },
        ]);
        return;
    }
    const controller = startJob(sessionId);
    parserRef.current.reset();
    textBufferRef.current = "";
    jobFinishedRef.current = false;
    ignoreStreamRef.current = false;
    finalizedRef.current = false;
    pendingQuestionRef.current = null;
    setRagSteps([]);
    if (ragVisualizationEnabled) setRagPanelOpen(true);

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

      onUpdateMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          role: "assistant",
          model,
          content: "",
          createdAt: Date.now(),
          status: "typing",
        },
      ]);

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
                const nextText = textBufferRef.current;
                onUpdateMessages(prev =>
                  prev.map(m =>
                    m.id === assistantId
                      ? {
                          ...m,
                          content: nextText,
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

      const tailFrames = parserRef.current.flush();
      for (const frame of tailFrames) {
        if (frame.type === "event") {
          handleUIEvent(frame.value);
          continue;
        }

        if (frame.type === "text") {
          textBufferRef.current += frame.value;

          if (!rafRef.current) {
            rafRef.current = requestAnimationFrame(() => {
              const nextText = textBufferRef.current;
              onUpdateMessages(prev =>
                prev.map(m =>
                  m.id === assistantId
                    ? {
                        ...m,
                        content: nextText,
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

      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
      }
      rafRef.current = null;
      // ✅ FINALIZE ONLY AFTER LOOP
      finalizeAssistant();

    } catch (err: any) { 
      const msg = err?.message ? String(err.message) : "";
      if (msg.startsWith(UI_EVENT_PREFIX)) {
        const evt = parseLLMUIEvent(msg);
        if (evt) {
          handleUIEvent(evt);
          if (!jobFinishedRef.current) {
            jobFinishedRef.current = true;
            finishJob();
          }
          return;
        }
      }

      if (assistantIdRef.current) {
        finalizeAssistant({
          status: "error",
          content: msg || "Request failed.",
        });
      } else if (msg) {
        onUpdateMessages((prev) => [
          ...prev,
          {
            id: uuidv4(),
            role: "system",
            content: msg,
            createdAt: Date.now(),
            status: "done",
          },
        ]);
        if (!jobFinishedRef.current) {
          jobFinishedRef.current = true;
          finishJob();
        }
      }
    } 
   
  }, [sessionId, isNetBlocked, model, ragVisualizationEnabled, onUpdateMessages, handleUIEvent, finalizeAssistant]);


  useEffect(() => {
    if (sessionId && pendingQuestionRef.current) {
      const question = pendingQuestionRef.current;
      pendingQuestionRef.current = null;
      generateAIResponse(question);
    }
  }, [sessionId, generateAIResponse]);


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

  

 

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, inlineMetadataFields, lastMessageContent]);

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
          key={sessionId ?? "new"}
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
                  onUploadStart={onUploadStart}
                  onUploadProgress={onUploadProgress}
                  onUploadSuccess={onUploadSuccess}
                  onUploadError={onUploadError}
                />
            </div>

            <div className={`absolute inset-0 flex flex-col transition-opacity ${hasStarted ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
                <div className="flex-1 overflow-y-auto px-4 pt-6">
                    <div className="mx-auto max-w-3xl space-y-5">
                        {false && !inlineMetadataFields && currentStage && (
                          <div className="mb-6 flex justify-start">
                            <ProcessingBubble
                              stepName={currentStage}
                            />
                          </div>
                        )}

                        {false && ragVisualizationEnabled && ragSteps.length > 0 && (
                          <div className="mb-4 rounded-lg border border-white/10 bg-black/40 px-4 py-3">
                            <button
                              onClick={() => setRagPanelOpen((v) => !v)}
                              className="w-full flex items-center justify-between text-xs text-gray-300"
                            >
                              <span>Answer steps ({ragSteps.length})</span>
                              <span className="text-gray-500">{ragPanelOpen ? "Hide" : "Show"}</span>
                            </button>
                            {ragPanelOpen && (
                              <div className="mt-3 space-y-2">
                                {ragSteps.map((s, idx) => (
                                  <div key={`${s.stage}-${idx}`} className="flex items-start gap-3 text-xs text-gray-300">
                                    <div className="mt-0.5 h-2 w-2 rounded-full bg-blue-500/70" />
                                    <div>
                                      <div className="text-gray-200 font-medium">{s.message}</div>
                                      <div className="text-[10px] text-gray-500">{s.stage}</div>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}

                        {messages.map((m, index) => {
                            const assistantModel =
                              m.role === "assistant" ? m.model ?? model : model;
                            const assistantLabel =
                              SAFE_MODELS.find((item) => item.id === assistantModel)?.label ??
                              modelLabel;
                            return (
                                <MessageBubble
                                  key={m.id}
                                  message={m}
                                  modelLabel={assistantLabel}
                                  assistantModel={assistantModel}
                                  showConfidence={showConfidence}
                                  userLabel={userLabel}
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
                          onUploadStart={onUploadStart}
                          onUploadProgress={onUploadProgress}
                          onUploadSuccess={onUploadSuccess}
                          onUploadError={onUploadError}
                          netBlocked={isNetBlocked}
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
