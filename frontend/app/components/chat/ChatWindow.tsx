"use client";

import { useEffect, useRef, useState, useMemo, useCallback, type DragEvent, type RefObject } from "react";
import MessageBubble from "./MessageBubble";
import ChatInput from "./ChatInput";
import EmptyState, { type EmptyPrompt } from "../EmptyState";
import InlineMetadataPrompt from "./InlineMetadataPrompt";
import SourceViewerModal from "./SourceViewerModal";
import ChatHeader from "./ChatHeader";
import ProcessingBubble from "./ProcessingBubble";
import Disclaimer from "../ui/Disclaimer"; //  Imported
import DeleteConfirmModal from "../ui/DeleteConfirmModal";

import { Message, RagSource } from "@/app/lib/types";
import { CHAT_UI_MODELS, ChatUIModelId } from "@/app/lib/chat-ui-models";
import { LLMUIEvent, MetadataRequestField, UI_EVENT_PREFIX, parseLLMUIEvent } from "@/app/lib/llm-ui-events";
import type { UploadStatus } from "@/app/hooks/useSmartUpload";
import { useSmartUpload } from "@/app/hooks/useSmartUpload";
import { StreamParser } from "@/app/lib/stream-parser";
import {
  fetchUploadPreprocessingPreview,
  generateChatTitle,
  streamChat,
  updateMetadata,
  type PreprocessingPreviewResponse,
} from "@/app/lib/api";

import { startJob, abortJob, finishJob } from "@/app/lib/job-manager";
import NetKeyModal from "@/app/components/net/NetKeyModal";
import { getFirstPdfFile, validatePdfFile, MAX_PDF_SIZE_MB } from "@/app/lib/pdf-upload";

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
  { id: CHAT_UI_MODELS.base.id, label: CHAT_UI_MODELS.base.label },
  { id: CHAT_UI_MODELS.lite.id, label: CHAT_UI_MODELS.lite.label },
  { id: CHAT_UI_MODELS.net.id, label: CHAT_UI_MODELS.net.label },
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
  model: ChatUIModelId;
  sessionId: string | null;
  userLabel?: string;
  userRole?: string;
  totalChats?: number;
  unreadNotifications?: number;
  devSettings?: any;
  title?: string;
  ingestionPollingActive?: boolean;
  ingestionPollingCount?: number;
  onRenameSession?: (title: string) => void;
  onModelChange?: (model: ChatUIModelId) => void;
  metadataActive?: boolean;
  uploadCancelState?: {
    messageId: string;
    label: string;
    phase: "metadata" | "preprocessing" | "ingestion";
  } | null;
  cancelUploadBusy?: boolean;
  onCancelUpload?: () => Promise<void> | void;
  uploadPipeline?: {
    percent: number;
    label: string;
  } | null;
  onUploadStart?: (file: File) => void;
  onUploadProgress?: (status: UploadStatus, percent: number, label: string) => void;
  onUploadSuccess?: (result: any) => void;
  onUploadError?: (error: string) => void;
  inputRefExternal?: RefObject<HTMLTextAreaElement | null>;

  externalMetadataRequest?: {
      jobId: string;
      fields: MetadataRequestField[];
      filename: string;
  } | null;
  onExternalMetadataSubmit?: (
  jobId: string,
  fields: MetadataRequestField[]
) => Promise<void> | void;
  streamChatOverride?: (
    payload: { session_id: string; question: string; mode: ChatUIModelId },
    signal?: AbortSignal
  ) => Promise<ReadableStream<Uint8Array>>;
  showUpload?: boolean;
  showSources?: boolean;
  lockModelSelector?: boolean;
  lockedModelLabel?: string;
  disableMetadataWorkflow?: boolean;
  emptyStateConfig?: {
    dashboardTitle?: string;
    dashboardSubtitle?: string;
    heroTitle?: string;
    heroSubtitle?: string;
    prompts?: EmptyPrompt[];
    showPmlEntryCard?: boolean;
    showSummaryCard?: boolean;
  };
  inputPlaceholderText?: string;
  disclaimerText?: string;
  generateTitleOverride?: (question: string) => Promise<string>;
  onSaveLatestAssistant?: (content: string) => Promise<void> | void;
  saveLatestAssistantLabel?: string;
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
  userRole,
  totalChats = 0,
  unreadNotifications = 0,
  devSettings,
  uploadCancelState = null,
  cancelUploadBusy = false,
  onCancelUpload,
  uploadPipeline,
  title = "New Chat",
  ingestionPollingActive = false,
  ingestionPollingCount = 0,
  onRenameSession,
  onModelChange,
  metadataActive = false,
  onUploadStart,
  onUploadProgress,
  onUploadSuccess,
  onUploadError,
  inputRefExternal,
  externalMetadataRequest,
  onExternalMetadataSubmit,
  streamChatOverride,
  showUpload = true,
  showSources = true,
  lockModelSelector = false,
  lockedModelLabel,
  disableMetadataWorkflow = false,
  emptyStateConfig,
  inputPlaceholderText,
  disclaimerText = "Kavin can make mistakes. Verify important information.",
  generateTitleOverride,
  onSaveLatestAssistant,
  saveLatestAssistantLabel = "Save as Template",
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
  const [saveTemplateBusy, setSaveTemplateBusy] = useState(false);
  const [saveTemplateStatus, setSaveTemplateStatus] = useState<string | null>(null);
  const [cancelConfirmOpen, setCancelConfirmOpen] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const dragDepthRef = useRef(0);

  
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
  const [preprocessingPreview, setPreprocessingPreview] =
    useState<PreprocessingPreviewResponse | null>(null);
  const [preprocessingPreviewLoading, setPreprocessingPreviewLoading] = useState(false);
  const [preprocessingPreviewError, setPreprocessingPreviewError] = useState<string | null>(null);

  // --- Live Model Stage ---
  const [currentStage, setCurrentStage] = useState<string>("");
  const { startUpload: startDroppedUpload } = useSmartUpload();

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
  const lastModelRef = useRef<ChatUIModelId>(model);
  const modelLabel = useMemo(() => SAFE_MODELS.find((m) => m.id === model)?.label ?? "Kavin Base v1.0", [model]);
  const lastMessageContent = messages[messages.length - 1]?.content;
  const latestAssistantContent = useMemo(() => {
    for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
      const item = messages[idx];
      if (item.role !== "assistant") continue;
      const content = (item.content || "").trim();
      if (!content) continue;
      return content;
    }
    return "";
  }, [messages]);


  // --- Blocking Logic ---
  const isTyping =
  !inlineMetadataFields &&
  assistantIdRef.current !== null &&
  messages.some(m => m.status === "typing" || m.status === "streaming");


  const isNetBlocked = model === "net" && netRateLimitedUntil !== null && Date.now() < netRateLimitedUntil;
  const isUIBlocked = Boolean(uploadPipeline)  || Boolean(inlineMetadataFields) || isNetBlocked;
  const dropUploadEnabled = Boolean(showUpload && sessionId && !isUIBlocked);

  const isAbortLikeError = useCallback((error: unknown) => {
    const name = String((error as any)?.name || "").trim();
    const message = String((error as any)?.message || "")
      .trim()
      .toLowerCase();
    return (
      name === "AbortError" ||
      message.includes("cancelled by user") ||
      message.includes("upload cancelled") ||
      message.includes("aborted")
    );
  }, []);

  const requestUploadCancel = useCallback(() => {
    if (!uploadCancelState || !onCancelUpload || cancelUploadBusy) return;
    if (uploadCancelState.phase === "metadata") {
      void Promise.resolve(onCancelUpload());
      return;
    }
    setCancelConfirmOpen(true);
  }, [cancelUploadBusy, onCancelUpload, uploadCancelState]);

  const confirmUploadCancel = useCallback(() => {
    if (!onCancelUpload || cancelUploadBusy) {
      setCancelConfirmOpen(false);
      return;
    }
    setCancelConfirmOpen(false);
    void Promise.resolve(onCancelUpload());
  }, [cancelUploadBusy, onCancelUpload]);



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
    if (!uploadCancelState) {
      setCancelConfirmOpen(false);
    }
  }, [uploadCancelState]);

  useEffect(() => {
    if (disableMetadataWorkflow) return;
    if (externalMetadataRequest || metadataActive) return;
    if (!inlineMetadataFields && !pendingJobId) return;

    setInlineMetadataFields(null);
    setPendingJobId(null);
    setCurrentStage("");
  }, [
    disableMetadataWorkflow,
    externalMetadataRequest,
    inlineMetadataFields,
    metadataActive,
    pendingJobId,
  ]);

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
    if (disableMetadataWorkflow) return;
    if (!externalMetadataRequest) return;
    if (inlineMetadataFields) return;

    // Prevent the form from "popping back open" after we hide it on submit,
    // while the parent still holds externalMetadataRequest.
    if (externalMetadataSeenJobIdRef.current === externalMetadataRequest.jobId) return;

    externalMetadataSeenJobIdRef.current = externalMetadataRequest.jobId;
    setPendingJobId(externalMetadataRequest.jobId);
    setInlineMetadataFields(externalMetadataRequest.fields);
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  }, [externalMetadataRequest, inlineMetadataFields, disableMetadataWorkflow]);

  const loadPreprocessingPreview = useCallback(async (
    jobId: string,
    scope: "auto" | "quick" | "full" = "auto"
  ) => {
    if (!jobId) return;
    setPreprocessingPreviewLoading(true);
    setPreprocessingPreviewError(null);

    try {
      const preview = await fetchUploadPreprocessingPreview(jobId, scope);
      setPreprocessingPreview(preview);
    } catch (err: any) {
      setPreprocessingPreviewError(
        err?.message || "Failed to load preprocessing preview."
      );
    } finally {
      setPreprocessingPreviewLoading(false);
    }
  }, []);

  useEffect(() => {
    if (disableMetadataWorkflow || !pendingJobId || !inlineMetadataFields) {
      if (!inlineMetadataFields) {
        setPreprocessingPreview(null);
        setPreprocessingPreviewError(null);
        setPreprocessingPreviewLoading(false);
      }
      return;
    }

    setPreprocessingPreview(null);
    setPreprocessingPreviewError(null);
    setPreprocessingPreviewLoading(false);
  }, [pendingJobId, inlineMetadataFields, disableMetadataWorkflow]);

  // ----------------------------------------------------------------------
  // 2. INLINE METADATA SUBMISSION
  // ----------------------------------------------------------------------

async function handleInlineMetadataSubmit(values: Record<string, string>) {
  if (disableMetadataWorkflow) return;
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
    if (isAbortLikeError(err)) {
      setPendingJobId(null);
      setInlineMetadataFields(null);
      return;
    }

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
    setCurrentStage("");
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
    setCurrentStage("");
    if (!jobFinishedRef.current) {
      jobFinishedRef.current = true;
      finishJob();
    }

    const pendingTitle = pendingTitleRef.current;
    if (pendingTitle && onRenameSession && title === "New Chat") {
      pendingTitleRef.current = null;
      const titleFn = generateTitleOverride ?? generateChatTitle;
      titleFn(pendingTitle).then((t) => onRenameSession(t));
    } else if (pendingTitle) {
      pendingTitleRef.current = null;
    }
  }, [onUpdateMessages, onRenameSession, title, generateTitleOverride]);

  const handleUIEvent = useCallback((event: LLMUIEvent) => {
    if (event.type === "REQUEST_METADATA") {
      if (disableMetadataWorkflow) return;
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
      setCurrentStage("");

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
      if (disableMetadataWorkflow) return;
      // Backend confirmed metadata saved
      setInlineMetadataFields(null);
      setPendingJobId(null);
      setCurrentStage("");
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
        setCurrentStage("");
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
      const sourceData = Array.isArray((event as any).data)
        ? (event as any).data
        : Array.isArray((event as any).sources)
          ? (event as any).sources
          : [];
      if (sourceData.length === 0) return;

      onUpdateMessages(prev =>
        prev.map(m => m.id === cid ? { ...m, sources: sourceData } : m)
      );
    }
  }, [inlineMetadataFields, onUpdateMessages, sessionId, ragVisualizationEnabled, finalizeAssistant, disableMetadataWorkflow]);

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
      const streamFn = streamChatOverride ?? streamChat;
      const stream = await streamFn(
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
   
  }, [sessionId, isNetBlocked, model, ragVisualizationEnabled, onUpdateMessages, handleUIEvent, finalizeAssistant, streamChatOverride]);


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
    setCurrentStage("");
    
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

  async function handleSaveLatestAssistant() {
    if (!onSaveLatestAssistant || saveTemplateBusy) return;
    const code = latestAssistantContent.trim();
    if (!code) {
      setSaveTemplateStatus("No generated code to save yet.");
      return;
    }

    setSaveTemplateBusy(true);
    setSaveTemplateStatus(null);
    try {
      await Promise.resolve(onSaveLatestAssistant(code));
      setSaveTemplateStatus("Saved as PML template.");
    } catch (err: any) {
      setSaveTemplateStatus(err?.message || "Failed to save template.");
    } finally {
      setSaveTemplateBusy(false);
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

  function hasDraggedFiles(event: DragEvent<HTMLElement>) {
    return Array.from(event.dataTransfer?.types || []).includes("Files");
  }

  function clearDragState() {
    dragDepthRef.current = 0;
    setDragActive(false);
  }

  async function handleDroppedFile(file: File) {
    if (!sessionId) {
      onUploadError?.("Initializing chat... please try again.");
      return;
    }

    const validationError = validatePdfFile(file);
    if (validationError) {
      onUploadError?.(validationError);
      return;
    }

    onUploadStart?.(file);
    await startDroppedUpload(
      file,
      sessionId,
      (status, pct, label) => onUploadProgress?.(status, pct, label),
      (data) => onUploadSuccess?.(data),
      (err) => onUploadError?.(err)
    );
  }

  function handleDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!dropUploadEnabled || !hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current += 1;
    setDragActive(true);
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!dropUploadEnabled || !hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = "copy";
    if (!dragActive) setDragActive(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) {
      setDragActive(false);
    }
  }

  async function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (!hasDraggedFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    clearDragState();
    if (!dropUploadEnabled) return;

    const file = getFirstPdfFile(event.dataTransfer.files);
    if (!file) return;
    await handleDroppedFile(file);
  }


  // ----------------------------------------------------------------------
  // 4. RENDER
  // ----------------------------------------------------------------------
  return (
    <>
      <div className="chat-window-shell relative h-full w-full flex flex-col">
        <ChatHeader 
          key={sessionId ?? "new"}
          title={title}
          isTyping={isTyping}
          ingestionPollingActive={ingestionPollingActive}
          ingestionPollingCount={ingestionPollingCount}
          activeModel={model}
          lockModelSelector={lockModelSelector}
          lockedModelLabel={lockedModelLabel}
          onModelChange={(nextModel) => {
            if (nextModel === "net" && netRateLimitedUntil) {
              setNetModalOpen(true);   // 🔥 TRIGGER HERE
            }
            onModelChange?.(nextModel);
          }}
          onRename={onRenameSession || (() => {})}
          onClear={() => onUpdateMessages([])} 
        />


        <div
          className="relative flex-1 w-full overflow-hidden"
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
            {dragActive && dropUploadEnabled && (
              <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-black/60 backdrop-blur-sm">
                <div className="rounded-3xl border border-cyan-400/40 bg-cyan-500/10 px-6 py-5 text-center shadow-[0_16px_48px_rgba(6,182,212,0.18)]">
                  <div className="text-base font-semibold text-white">Drop PDF to upload</div>
                  <div className="mt-1 text-sm text-cyan-100/80">
                    PDF only, up to {MAX_PDF_SIZE_MB}MB
                  </div>
                </div>
              </div>
            )}
            <div className={`absolute inset-0 flex items-center justify-center transition-all ${hasStarted ? "opacity-0 pointer-events-none" : "opacity-100"}`}>
                <EmptyState 
                  disabled={isUIBlocked}
                  onSend={handleSend}
                  sessionId={sessionId}
                  userLabel={userLabel}
                  userRole={userRole}
                  totalChats={totalChats}
                  unreadNotifications={unreadNotifications}
                  showUploadButton={showUpload}
                  showPmlEntryCard={emptyStateConfig?.showPmlEntryCard}
                  showSummaryCard={emptyStateConfig?.showSummaryCard}
                  dashboardTitle={emptyStateConfig?.dashboardTitle}
                  dashboardSubtitle={emptyStateConfig?.dashboardSubtitle}
                  heroTitle={emptyStateConfig?.heroTitle}
                  heroSubtitle={emptyStateConfig?.heroSubtitle}
                  prompts={emptyStateConfig?.prompts}
                  onUploadStart={onUploadStart}
                  onUploadProgress={onUploadProgress}
                  onUploadSuccess={onUploadSuccess}
                  onUploadError={onUploadError}
                />
            </div>

            <div className={`absolute inset-0 flex flex-col transition-opacity ${hasStarted ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
                <div className="chat-window-scroll flex-1 overflow-y-auto px-2.5 pt-4 sm:px-4 sm:pt-6">
                    <div className="mx-auto w-full max-w-3xl space-y-5">
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
                            const uploadMessageId = uploadCancelState?.messageId ?? null;
                            const uploadBubbleActive =
                              uploadMessageId !== null &&
                              m.id === uploadMessageId;
                            return (
                                <MessageBubble
                                  key={m.id}
                                  message={m}
                                  modelLabel={assistantLabel}
                                  assistantModel={assistantModel}
                                  showConfidence={showConfidence}
                                  uploadCancelState={
                                    uploadBubbleActive && uploadCancelState
                                      ? {
                                          phase: uploadCancelState.phase,
                                          label: uploadCancelState.label,
                                        }
                                      : null
                                  }
                                  cancelUploadBusy={cancelUploadBusy}
                                  onCancelUpload={uploadBubbleActive ? requestUploadCancel : undefined}
                                  userLabel={userLabel}
                                  isLastAssistant={
                                    m.role === "assistant" &&
                                    index === messages.map((x) => x.role).lastIndexOf("assistant")
                                  }
                                  sessionId={sessionId}
                                  companyDocumentId={m.sources?.[0]?.company_document_id}
                                  revisionNumber={m.sources?.[0]?.revision_number}
                                  onViewSources={
                                    showSources
                                      ? (sources) => {
                                          setActiveSources(sources);
                                          setViewerOpen(true);
                                        }
                                      : undefined
                                  }
                                />
                            );
                        })}

                        {!disableMetadataWorkflow && inlineMetadataFields && (
                            <InlineMetadataPrompt
                                fields={inlineMetadataFields}
                                onSubmit={handleInlineMetadataSubmit}
                                onCancel={
                                  uploadCancelState?.phase === "metadata" && onCancelUpload
                                    ? () => {
                                        requestUploadCancel();
                                      }
                                    : undefined
                                }
                                cancelDisabled={cancelUploadBusy}
                                previewJobId={pendingJobId}
                                preview={preprocessingPreview}
                                previewLoading={preprocessingPreviewLoading}
                                previewError={preprocessingPreviewError}
                                onRetryPreview={() => {
                                  if (pendingJobId) {
                                    void loadPreprocessingPreview(pendingJobId);
                                  }
                                }}
                                onLoadFullPreview={() => {
                                  if (pendingJobId) {
                                    void loadPreprocessingPreview(pendingJobId, "full");
                                  }
                                }}
                            />
                        )}

                        <div ref={bottomRef} />
                    </div>
                </div>

                <div className="chat-window-input-area border-t border-white/10 bg-black pb-[calc(0.5rem+env(safe-area-inset-bottom))] pt-3 sm:pt-4">
                    <div className="mx-auto w-full max-w-3xl px-2.5 sm:px-4">
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
                          showUploadButton={showUpload}
                          placeholderText={inputPlaceholderText}
                          onUploadStart={onUploadStart}
                          onUploadProgress={onUploadProgress}
                          onUploadSuccess={onUploadSuccess}
                          onUploadError={onUploadError}
                          netBlocked={isNetBlocked}
                        />

                        {/*  DISCLAIMER ADDED */}
                        <div className="chat-window-disclaimer mt-2">
                            <Disclaimer text={disclaimerText} />
                        </div>
                        {onSaveLatestAssistant && (
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                void handleSaveLatestAssistant();
                              }}
                              disabled={saveTemplateBusy || !latestAssistantContent.trim()}
                              className="rounded-md border border-white/30 bg-white px-3 py-1.5 text-xs font-medium text-black transition-colors hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {saveTemplateBusy ? "Saving..." : saveLatestAssistantLabel}
                            </button>
                            {saveTemplateStatus && (
                              <span className="text-[11px] text-gray-400">{saveTemplateStatus}</span>
                            )}
                          </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
      </div>

      <DeleteConfirmModal
        open={cancelConfirmOpen}
        title="Cancel document processing?"
        description="Continuing will cancel this upload and delete any preprocessing or ingestion data saved so far."
        confirmLabel="Continue"
        onCancel={() => setCancelConfirmOpen(false)}
        onConfirm={confirmUploadCancel}
      />

            <NetKeyModal
          open={netModalOpen}
          onClose={() => setNetModalOpen(false)}
        />

        {showSources && (
          <SourceViewerModal
            open={viewerOpen}
            sources={activeSources}
            onClose={() => setViewerOpen(false)}
          />
        )}
    </>
  );
}
