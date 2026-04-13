// frontend/app/dashboard/page.tsx

"use client";

import { useEffect, useState, useCallback } from "react";
import { API_BASE } from "@/app/lib/config";
import { Play, CheckCircle, AlertTriangle, Search, FileText, Download, Trash2, RefreshCw, Key } from "lucide-react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  authMe,
  fetchUploadPreprocessingPreview,
  type PreprocessingPreviewResponse,
} from "@/app/lib/api";
import DeleteConfirmModal from "@/app/components/ui/DeleteConfirmModal";
import StartupLoader from "@/app/components/ui/StartupLoader";
import RuntimeOverview from "@/app/components/debug/RuntimeOverview";
import PreprocessingPreviewPanel from "@/app/components/chat/PreprocessingPreviewPanel";

const RECOMMENDED_LOCAL_MODEL = {
  label: "Qwen 2.5 3B GGUF (Q4_K_M)",
  repoId: "Qwen/Qwen2.5-3B-Instruct-GGUF",
  modelId: "lite_qwen_3b_q4",
  ggufFilename: "qwen2.5-3b-instruct-q4_k_m.gguf",
  note: "Best single local model for CPU-first developer setups. Installs once, then powers both Lite and Base.",
} as const;

type DevtoolsHealthStatus = "ok" | "fail" | "skipped";

interface DevtoolsHealthEntry {
  name: string;
  method: "GET" | "POST" | "PATCH" | "DELETE";
  path: string;
  status: DevtoolsHealthStatus;
  code?: number;
  timeMs?: number;
  detail?: string;
}

interface RecentUploadJob {
  job_id: string;
  session_id?: string | null;
  status: string;
  progress?: number | null;
  progress_label?: string | null;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  company_document_id?: string | null;
  revision_number?: string | null;
  source_file?: string | null;
  rag_preprocessor?: string | null;
  rag_ingest_mode?: string | null;
  rag_collection_name?: string | null;
  segregation_summary?: Record<string, any> | null;
  preview_available?: boolean;
  artifact_only_preview?: boolean;
  preview_unavailable_reason?: string | null;
  missing_fields?: string[] | null;
}

function canAccessDeveloperDashboard(role?: string) {
  const normalized = (role || "").trim().toLowerCase();
  return (
    normalized === "admin" ||
    normalized === "developer" ||
    normalized === "piping_admin" ||
    normalized === "pipe_lead" ||
    normalized === "pipe_stress_engineer"
  );
}

function formatSegmentationSnapshot(summary?: Record<string, any> | null) {
  const filtered = summary && typeof summary === "object" ? summary.filtered : null;
  if (!filtered || typeof filtered !== "object") return null;
  return `T ${Number(filtered.table || 0)} | Text ${Number(filtered.text || 0)} | Img ${Number(filtered.image || 0)} | Other ${Number(filtered.other || 0)}`;
}

export default function DashboardPage() {
  const router = useRouter();

  const [activeTab, setActiveTab] = useState<
    "settings" | "preprocessing" | "models" | "runtime" | "users" | "databases" | "danger" | "intent" | "rewrite" | "retrieve" | "health"
  >("settings");

  const [backendReady, setBackendReady] = useState(false);
  const [backendWarming, setBackendWarming] = useState(true);
  const [backendWarmupError, setBackendWarmupError] = useState<string | null>(null);
  const [backendHealth, setBackendHealth] = useState<"unknown" | "ok" | "degraded" | "error">("unknown");
  const [warmupServices, setWarmupServices] = useState<
    { label: string; status: "pending" | "ok" | "degraded" | "error" }[]
  >([
    { label: "Frontend", status: "ok" },
    { label: "API", status: "pending" },
    { label: "Postgres", status: "pending" },
    { label: "Redis", status: "pending" },
    { label: "MinIO", status: "pending" },
    { label: "RabbitMQ", status: "pending" },
  ]);

  // --- Intent State ---
  const [intentInput, setIntentInput] = useState("");
  const [intentResult, setIntentResult] = useState<any>(null);

  // --- Rewrite State ---
  const [rewriteInput, setRewriteInput] = useState("");
  const [rewriteHistory, setRewriteHistory] = useState("");
  const [rewriteResult, setRewriteResult] = useState<any>(null);

  // --- Retrieval State ---
  const [retrievalQuery, setRetrievalQuery] = useState("");
  const [retrievalDocId, setRetrievalDocId] = useState("");
  const [retrievalRevision, setRetrievalRevision] = useState("1");
  const [retrievalCollectionName, setRetrievalCollectionName] = useState("");
  const [retrievalResult, setRetrievalResult] = useState<any>(null);

  // --- Preprocessing Preview State ---
  const [preprocessingJobs, setPreprocessingJobs] = useState<RecentUploadJob[]>([]);
  const [preprocessingJobsError, setPreprocessingJobsError] = useState<string | null>(null);
  const [preprocessingJobsBusy, setPreprocessingJobsBusy] = useState(false);
  const [preprocessingJobDraft, setPreprocessingJobDraft] = useState("");
  const [selectedPreprocessingJobId, setSelectedPreprocessingJobId] = useState<string | null>(null);
  const [preprocessingPreview, setPreprocessingPreview] =
    useState<PreprocessingPreviewResponse | null>(null);
  const [preprocessingPreviewLoading, setPreprocessingPreviewLoading] = useState(false);
  const [preprocessingPreviewError, setPreprocessingPreviewError] = useState<string | null>(null);

  // --- Settings State ---
  const [settings, setSettings] = useState<any>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [ragCollectionDraft, setRagCollectionDraft] = useState("");

  // --- Models State ---
  const [modelsData, setModelsData] = useState<any>(null);
  const [activeModels, setActiveModels] = useState<any>(null);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [modelsBusy, setModelsBusy] = useState(false);
  const [runtimeData, setRuntimeData] = useState<any>(null);
  const [runtimeError, setRuntimeError] = useState<string | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);

  // Auto-download from Hugging Face (detect GGUF vs HF)
  const [hfAutoRepoId, setHfAutoRepoId] = useState("");
  const [hfAutoModelId, setHfAutoModelId] = useState("");
  const [hfAutoGgufFile, setHfAutoGgufFile] = useState("");
  const [hfAutoStatus, setHfAutoStatus] = useState<string | null>(null);
  const [assignLiteModel, setAssignLiteModel] = useState("");
  const [assignBaseModel, setAssignBaseModel] = useState("");
  const [assignNetModel, setAssignNetModel] = useState("");

  // --- Danger Zone State ---
  const [resetConfirm, setResetConfirm] = useState("");
  const [resetBucket, setResetBucket] = useState("");
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetResult, setResetResult] = useState<any>(null);
  const [nuclearBusy, setNuclearBusy] = useState(false);
  const [nuclearResult, setNuclearResult] = useState<string | null>(null);
  const [nuclearError, setNuclearError] = useState<string | null>(null);

  // --- Users State ---
  const [usersData, setUsersData] = useState<any>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [usersBusy, setUsersBusy] = useState(false);
  const [newUserEmail, setNewUserEmail] = useState("");
  const [newUserUsername, setNewUserUsername] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newUserRole, setNewUserRole] = useState("user");
  const [newUserPgDatabase, setNewUserPgDatabase] = useState("");
  const [newUserMinioBucket, setNewUserMinioBucket] = useState("");
  const [userPasswordDrafts, setUserPasswordDrafts] = useState<Record<string, string>>({});
  const [userRoleDrafts, setUserRoleDrafts] = useState<Record<string, string>>({});
  const [deleteUserTarget, setDeleteUserTarget] = useState<{ username: string; email?: string } | null>(null);

  // --- DevTools API Health ---
  const [devtoolsHealth, setDevtoolsHealth] = useState<DevtoolsHealthEntry[] | null>(null);
  const [devtoolsHealthBusy, setDevtoolsHealthBusy] = useState(false);
  const [devtoolsHealthError, setDevtoolsHealthError] = useState<string | null>(null);
  const [devtoolsHealthLastRun, setDevtoolsHealthLastRun] = useState<number | null>(null);

  const USERNAME_RULES = "3–32 chars, start with a letter, use letters/numbers/._-";
  const USERNAME_RE = /^[a-zA-Z][a-zA-Z0-9_.-]{2,31}$/;
  const EMAIL_RULES = "Use a valid email like name@example.com";
  const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
  const PASSWORD_RULES = "8–128 characters";
  const PG_DB_RULES = "3–63 chars, letters/numbers/underscore";
  const PG_DB_RE = /^[a-zA-Z][a-zA-Z0-9_]{2,62}$/;
  const MINIO_BUCKET_RULES = "3–63 chars, lowercase, digits, dots or hyphens";
  const MINIO_BUCKET_RE = /^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/;
  const usernameFormatError =
    newUserUsername.trim().length > 0 && !USERNAME_RE.test(newUserUsername.trim())
      ? `Invalid username format. ${USERNAME_RULES}`
      : "";
  const emailFormatError =
    newUserEmail.trim().length > 0 && !EMAIL_RE.test(newUserEmail.trim())
      ? `Invalid email format. ${EMAIL_RULES}`
      : "";
  const passwordFormatError =
    newUserPassword.length > 0 && (newUserPassword.length < 8 || newUserPassword.length > 128)
      ? `Invalid password length. ${PASSWORD_RULES}`
      : "";
  const pgDbFormatError =
    newUserPgDatabase.trim().length > 0 && !PG_DB_RE.test(newUserPgDatabase.trim())
      ? `Invalid database name. ${PG_DB_RULES}`
      : "";
  const minioBucketFormatError =
    newUserMinioBucket.trim().length > 0 && !MINIO_BUCKET_RE.test(newUserMinioBucket.trim())
      ? `Invalid bucket name. ${MINIO_BUCKET_RULES}`
      : "";
  const suggestedDbName = (() => {
    const base = newUserUsername.trim().toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "");
    return base ? `chat_ui_${base}` : "chat_ui_user";
  })();
  const suggestedBucketName = (() => {
    const base = newUserUsername.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
    return base ? `chat-ui-${base}` : "chat-ui-user";
  })();

  // --- Database Viewer State ---
  const [dbList, setDbList] = useState<any[]>([]);
  const [dbId, setDbId] = useState<string>("");
  const [dbTables, setDbTables] = useState<string[]>([]);
  const [dbTable, setDbTable] = useState<string>("");
  const [dbTableQuery, setDbTableQuery] = useState("");
  const [dbRows, setDbRows] = useState<any[]>([]);
  const [dbColumns, setDbColumns] = useState<string[]>([]);
  const [dbTotal, setDbTotal] = useState<number>(0);
  const [dbLimit, setDbLimit] = useState<number>(25);
  const [dbOffset, setDbOffset] = useState<number>(0);
  const [dbError, setDbError] = useState<string | null>(null);
  const [dbBusy, setDbBusy] = useState(false);

  // --- RAG Overrides ---
  const [ragOverrideSession, setRagOverrideSession] = useState("");
  const [ragOverrideUser, setRagOverrideUser] = useState("");
  const [ragOverrides, setRagOverrides] = useState<any>(null);

  const runBackendWarmup = useCallback(async (isCancelled?: () => boolean) => {
    setBackendWarming(true);
    setBackendWarmupError(null);
    setBackendReady(false);
    setWarmupServices([
      { label: "Frontend", status: "ok" },
      { label: "API", status: "pending" },
      { label: "Postgres", status: "pending" },
      { label: "Redis", status: "pending" },
      { label: "MinIO", status: "pending" },
      { label: "RabbitMQ", status: "pending" },
    ]);

    const mapStatus = (value?: string) => {
      if (!value) return "pending";
      const lower = value.toLowerCase();
      if (lower.startsWith("ok")) return "ok";
      if (lower.startsWith("error")) return "error";
      if (lower.includes("disabled")) return "degraded";
      return "degraded";
    };

    const updateFromHealth = async () => {
      try {
        const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
        if (!res.ok) return false;
        const data = await res.json();
        if (isCancelled?.()) return false;
        const services = data?.services || {};
        setWarmupServices([
          { label: "Frontend", status: "ok" },
          { label: "API", status: data?.status === "ok" ? "ok" : "degraded" },
          { label: "Postgres", status: mapStatus(services.postgres) },
          { label: "Redis", status: mapStatus(services.redis) },
          { label: "MinIO", status: mapStatus(services.minio) },
          { label: "RabbitMQ", status: mapStatus(services.rabbitmq) },
        ]);
        return true;
      } catch {
        return false;
      }
    };

    const start = Date.now();
    let ok = false;
    while (Date.now() - start < 20000) {
      ok = await updateFromHealth();
      if (ok) break;
      await new Promise((r) => setTimeout(r, 600));
    }

    if (isCancelled?.()) return;
    if (!ok) {
      setBackendWarmupError("Services are still starting. Please try again.");
      setBackendWarming(false);
      return;
    }

    const u = await authMe();
    if (isCancelled?.()) return;
    if (!u) {
      router.replace("/signin");
      return;
    }
    if (!canAccessDeveloperDashboard(u.role)) {
      router.replace("/");
      return;
    }
    setBackendReady(true);
    setBackendWarming(false);
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    runBackendWarmup(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [runBackendWarmup]);

  useEffect(() => {
    if (!backendReady) return;
    loadSettings();
  }, [backendReady]);

  useEffect(() => {
    const nextCollection = String(settings?.rag_collection_name || "rag_documents");
    setRagCollectionDraft(nextCollection);
    setRetrievalCollectionName((prev) => (prev.trim() ? prev : nextCollection));
  }, [settings]);

  const documentProcessingProfile = settings?.document_processing_profile || {};

  useEffect(() => {
    if (!backendReady) return;
    let cancelled = false;
    const check = async () => {
      try {
        const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
        if (!res.ok) throw new Error("Health check failed");
        const data = await res.json();
        if (cancelled) return;
        setBackendHealth(data?.status === "ok" ? "ok" : "degraded");
      } catch {
        if (!cancelled) setBackendHealth("error");
      }
    };
    check();
    return () => {
      cancelled = true;
    };
  }, [backendReady]);

  useEffect(() => {
    if (!backendReady) return;
    if (activeTab === "models") {
      loadModels();
    }
  }, [activeTab, backendReady]);

  useEffect(() => {
    if (!backendReady || activeTab !== "models") return;
    const timer = window.setInterval(() => {
      loadModels();
    }, 10000);
    return () => {
      window.clearInterval(timer);
    };
  }, [activeTab, backendReady]);

  useEffect(() => {
    if (!backendReady) return;
    if (activeTab === "runtime") {
      loadRuntime();
    }
  }, [activeTab, backendReady]);

  useEffect(() => {
    if (!backendReady) return;
    if (activeTab === "users") {
      loadUsers();
    }
  }, [activeTab, backendReady]);

  useEffect(() => {
    if (!backendReady) return;
    if (activeTab === "databases") {
      loadDatabases();
    }
  }, [activeTab, backendReady]);

  useEffect(() => {
    if (!backendReady) return;
    if (activeTab === "databases" && dbId) {
      loadTables(dbId);
    }
  }, [activeTab, dbId, backendReady]);

  useEffect(() => {
    setDbTableQuery("");
  }, [dbId]);

  useEffect(() => {
    if (!backendReady) return;
    if (activeTab === "settings") {
      loadRagOverrides();
    }
  }, [activeTab, backendReady]);

  // --- Handlers ---
  async function loadSettings() {
    setSettingsError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/settings`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      setSettings(await res.json());
    } catch (e: any) {
      setSettingsError(e?.message || "Failed to load settings");
    }
  }

  async function loadModels() {
    setModelsError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/models`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setModelsData(data);
      if (Array.isArray(data?.effective_modes)) {
        setActiveModels((prev: any) => ({
          ...(prev && typeof prev === "object" ? prev : {}),
          modes: data.effective_modes,
          model_registry: data.model_registry,
        }));
      }
      const reg = data?.model_registry || {};
      const hfIds = Object.keys(data?.hf_models || {});
      const ggufIds = Object.keys(data?.gguf_models || {});
      const baseIds = Array.from(new Set([...hfIds, ...ggufIds]));
      const netProviders = Array.isArray(data?.net_providers) ? data.net_providers : [];

      const nextBase =
        reg?.base?.default && baseIds.includes(reg.base.default)
          ? reg.base.default
          : baseIds[0] || "";
      const nextLite =
        reg?.lite?.default && ggufIds.includes(reg.lite.default)
          ? reg.lite.default
          : ggufIds[0] || "";
      const nextNet =
        reg?.net?.default && netProviders.includes(reg.net.default)
          ? reg.net.default
          : netProviders[0] || "";

      setAssignLiteModel(nextLite);
      setAssignBaseModel(nextBase);
      setAssignNetModel(nextNet);
      try {
        const activeRes = await fetch(`${API_BASE}/devtools/models/active`, {
          credentials: "include",
        });
        if (activeRes.ok) {
          setActiveModels(await activeRes.json());
        }
      } catch {
        // ignore
      }
    } catch (e: any) {
      setModelsError(e?.message || "Failed to load models");
    }
  }

  async function loadRuntime() {
    setRuntimeBusy(true);
    setRuntimeError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/runtime`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      setRuntimeData(await res.json());
    } catch (e: any) {
      setRuntimeError(e?.message || "Failed to load runtime status");
    } finally {
      setRuntimeBusy(false);
    }
  }

  const loadDashboardPreprocessingPreview = useCallback(async (
    jobId: string,
    scope: "auto" | "quick" | "full" = "full"
  ) => {
    const trimmed = (jobId || "").trim();
    if (!trimmed) {
      setPreprocessingPreview(null);
      setPreprocessingPreviewError("Enter an upload job id to load a preprocessing preview.");
      return;
    }

    setPreprocessingPreviewLoading(true);
    setPreprocessingPreviewError(null);
    try {
      const preview = await fetchUploadPreprocessingPreview(trimmed, scope);
      setPreprocessingPreview(preview);
      setSelectedPreprocessingJobId(trimmed);
      setPreprocessingJobDraft(trimmed);
    } catch (e: any) {
      setPreprocessingPreview(null);
      setPreprocessingPreviewError(e?.message || "Failed to load preprocessing preview.");
    } finally {
      setPreprocessingPreviewLoading(false);
    }
  }, []);

  const loadRecentUploadJobs = useCallback(async () => {
    setPreprocessingJobsBusy(true);
    setPreprocessingJobsError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/uploads/recent?limit=12`, {
        credentials: "include",
        cache: "no-store",
      });
      if (!res.ok) throw new Error(await res.text());

      const data = await res.json();
      const jobs = Array.isArray(data?.jobs) ? (data.jobs as RecentUploadJob[]) : [];
      setPreprocessingJobs(jobs);

      if (!jobs.length) {
        setSelectedPreprocessingJobId(null);
        setPreprocessingPreview(null);
        setPreprocessingJobDraft("");
        return;
      }
    } catch (e: any) {
      setPreprocessingJobsError(e?.message || "Failed to load recent upload jobs.");
    } finally {
      setPreprocessingJobsBusy(false);
    }
  }, []);

  const openDashboardPreprocessingJob = useCallback(async (
    jobId: string,
    scope: "auto" | "quick" | "full" = "full"
  ) => {
    const trimmed = (jobId || "").trim();
    if (!trimmed) {
      setPreprocessingPreviewError("Enter an upload job id to load a preprocessing preview.");
      return;
    }
    await loadDashboardPreprocessingPreview(trimmed, scope);
  }, [loadDashboardPreprocessingPreview]);

  useEffect(() => {
    if (!backendReady) return;
    if (activeTab === "preprocessing") {
      void loadRecentUploadJobs();
    }
  }, [activeTab, backendReady, loadRecentUploadJobs]);

  useEffect(() => {
    if (!backendReady || activeTab !== "preprocessing") return;
    if (!preprocessingJobs.length) return;

    const currentSelected = (selectedPreprocessingJobId || "").trim();
    const selectedStillPresent = currentSelected
      ? preprocessingJobs.some((job) => job.job_id === currentSelected)
      : false;

    if (selectedStillPresent) return;

    const fallbackJob = preprocessingJobs.find((job) => job.preview_available) || preprocessingJobs[0];
    if (!fallbackJob) return;

    setSelectedPreprocessingJobId(fallbackJob.job_id);
    setPreprocessingJobDraft(fallbackJob.job_id);

    if (fallbackJob.preview_available) {
      void loadDashboardPreprocessingPreview(fallbackJob.job_id, "full");
    } else {
      setPreprocessingPreview(null);
      setPreprocessingPreviewError(
        fallbackJob.preview_unavailable_reason || "This upload no longer has a local PDF available for preview."
      );
    }
  }, [
    activeTab,
    backendReady,
    loadDashboardPreprocessingPreview,
    preprocessingJobs,
    selectedPreprocessingJobId,
  ]);

  async function loadUsers() {
    setUsersError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/users`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      setUsersData(await res.json());
    } catch (e: any) {
      setUsersError(e?.message || "Failed to load users");
    }
  }

  async function createUser() {
    setUsersBusy(true);
    setUsersError(null);
    const trimmedUsername = newUserUsername.trim();
    const trimmedEmail = newUserEmail.trim();
    if (!EMAIL_RE.test(trimmedEmail)) {
      setUsersError(`Invalid email format. ${EMAIL_RULES}`);
      setUsersBusy(false);
      return;
    }
    if (!USERNAME_RE.test(trimmedUsername)) {
      setUsersError(`Invalid username format. ${USERNAME_RULES}`);
      setUsersBusy(false);
      return;
    }
    if (newUserPassword.length < 8 || newUserPassword.length > 128) {
      setUsersError(`Invalid password length. ${PASSWORD_RULES}`);
      setUsersBusy(false);
      return;
    }
    if (newUserPgDatabase.trim() && !PG_DB_RE.test(newUserPgDatabase.trim())) {
      setUsersError(`Invalid database name. ${PG_DB_RULES}`);
      setUsersBusy(false);
      return;
    }
    if (newUserMinioBucket.trim() && !MINIO_BUCKET_RE.test(newUserMinioBucket.trim())) {
      setUsersError(`Invalid bucket name. ${MINIO_BUCKET_RULES}`);
      setUsersBusy(false);
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/devtools/users`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email: trimmedEmail,
          username: trimmedUsername,
          password: newUserPassword,
          role: newUserRole || "user",
          pg_database: newUserPgDatabase.trim() || undefined,
          minio_bucket: newUserMinioBucket.trim() || undefined,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setNewUserEmail("");
      setNewUserUsername("");
      setNewUserPassword("");
      setNewUserPgDatabase("");
      setNewUserMinioBucket("");
      await loadUsers();
    } catch (e: any) {
      setUsersError(e?.message || "Failed to create user");
    } finally {
      setUsersBusy(false);
    }
  }

  async function setUserDisabled(identifier: string, disabled: boolean) {
    setUsersBusy(true);
    setUsersError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/users/disable`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ identifier, disabled }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadUsers();
    } catch (e: any) {
      setUsersError(e?.message || "Failed to update user");
    } finally {
      setUsersBusy(false);
    }
  }

  async function resetUserPassword(identifier: string, newPassword: string) {
    setUsersBusy(true);
    setUsersError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/users/password`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ identifier, new_password: newPassword }),
      });
      if (!res.ok) throw new Error(await res.text());
      setUserPasswordDrafts((prev) => ({ ...prev, [identifier]: "" }));
      await loadUsers();
    } catch (e: any) {
      setUsersError(e?.message || "Failed to reset password");
    } finally {
      setUsersBusy(false);
    }
  }

  async function updateUserRole(identifier: string, role: string) {
    setUsersBusy(true);
    setUsersError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/users/role`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ identifier, role }),
      });
      if (!res.ok) throw new Error(await res.text());
      setUserRoleDrafts((prev) => {
        const next = { ...prev };
        delete next[identifier];
        return next;
      });
      await loadUsers();
    } catch (e: any) {
      setUsersError(e?.message || "Failed to update role");
    } finally {
      setUsersBusy(false);
    }
  }

  async function deleteUser(identifier: string) {
    setUsersBusy(true);
    setUsersError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/users/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ identifier }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadUsers();
    } catch (e: any) {
      setUsersError(e?.message || "Failed to delete user");
    } finally {
      setUsersBusy(false);
    }
  }

  async function runDevtoolsHealthCheck() {
    setDevtoolsHealthBusy(true);
    setDevtoolsHealthError(null);
    setDevtoolsHealthLastRun(null);

    const results: DevtoolsHealthEntry[] = [];
    const push = (entry: DevtoolsHealthEntry) => {
      results.push(entry);
      setDevtoolsHealth([...results]);
    };

    const check = async (
      name: string,
      method: DevtoolsHealthEntry["method"],
      path: string,
      body?: any
    ) => {
      const started = performance.now();
      try {
        const res = await fetch(`${API_BASE}${path}`, {
          method,
          headers: body ? { "Content-Type": "application/json" } : undefined,
          credentials: "include",
          body: body ? JSON.stringify(body) : undefined,
        });
        const timeMs = Math.round(performance.now() - started);
        if (!res.ok) {
          const detail = await res.text();
          push({
            name,
            method,
            path,
            status: "fail",
            code: res.status,
            timeMs,
            detail: detail?.slice(0, 180) || res.statusText,
          });
          return { ok: false, json: null };
        }
        let json: any = null;
        try {
          json = await res.json();
        } catch {
          json = null;
        }
        push({
          name,
          method,
          path,
          status: "ok",
          code: res.status,
          timeMs,
        });
        return { ok: true, json };
      } catch (e: any) {
        const timeMs = Math.round(performance.now() - started);
        push({
          name,
          method,
          path,
          status: "fail",
          timeMs,
          detail: e?.message || "Network error",
        });
        return { ok: false, json: null };
      }
    };

    const skip = (
      name: string,
      method: DevtoolsHealthEntry["method"],
      path: string,
      detail: string
    ) => {
      push({ name, method, path, status: "skipped", detail });
    };

    try {
      await check("Settings", "GET", "/devtools/settings");
      await check("Jobs", "GET", "/devtools/jobs");
      await check("Models", "GET", "/devtools/models");
      await check("Active Models", "GET", "/devtools/models/active");
      await check("Users List", "GET", "/devtools/users");
      await check("RAG Overrides", "GET", "/devtools/rag/overrides");
      const dbs = await check("Databases", "GET", "/devtools/dbs");
      if (dbs.ok) {
        const dbId = dbs.json?.databases?.[0]?.id;
        if (dbId) {
          const tables = await check("DB Tables", "GET", `/devtools/dbs/${dbId}/tables`);
          const table = tables.ok ? tables.json?.tables?.[0] : null;
          if (table) {
            await check(
              "DB Records",
              "GET",
              `/devtools/dbs/${dbId}/records?table=${encodeURIComponent(table)}&limit=1&offset=0`
            );
          } else {
            skip("DB Records", "GET", "/devtools/dbs/{db_id}/records", "No tables available");
          }
        } else {
          skip("DB Tables", "GET", "/devtools/dbs/{db_id}/tables", "No databases configured");
          skip("DB Records", "GET", "/devtools/dbs/{db_id}/records", "No databases configured");
        }
      }

      await check("Intent", "POST", "/devtools/intent", { text: "healthcheck" });
      await check("Rewrite", "POST", "/devtools/rewrite", { text: "healthcheck", history: [] });
      await check("Keywords", "POST", "/devtools/keywords", { text: "healthcheck" });

      if (retrievalDocId.trim()) {
        await check("Retrieve", "POST", "/devtools/retrieve", {
          question: "healthcheck",
          company_document_id: retrievalDocId.trim(),
          revision_number: "1",
        });
      } else {
        skip("Retrieve", "POST", "/devtools/retrieve", "Set Company Document ID to test");
      }

      // Mutating/destructive endpoints intentionally skipped
      skip("Patch Settings", "PATCH", "/devtools/settings", "Mutates state");
      skip("Create User", "POST", "/devtools/users", "Mutates state");
      skip("Disable User", "PATCH", "/devtools/users/disable", "Mutates state");
      skip("Reset Password", "PATCH", "/devtools/users/password", "Mutates state");
      skip("Update Role", "PATCH", "/devtools/users/role", "Mutates state");
      skip("Delete User", "POST", "/devtools/users/delete", "Mutates state");
      skip("RAG Disable", "POST", "/devtools/rag/disable", "Mutates state");
      skip("RAG Enable", "POST", "/devtools/rag/enable", "Mutates state");
      skip("Install HF Model", "POST", "/devtools/models/hf/install", "Mutates state");
      skip("Download Model", "POST", "/devtools/models/download", "Mutates state");
      skip("Register GGUF", "POST", "/devtools/models/gguf/register", "Mutates state");
      skip("Download GGUF", "POST", "/devtools/models/gguf/download", "Mutates state");
      skip("Patch Model Registry", "PATCH", "/devtools/models/registry", "Mutates state");
      skip("Test Model", "POST", "/devtools/models/test", "Mutates state");
      skip("Delete Model", "DELETE", "/devtools/models/{model_id}", "Mutates state");
      skip("Reset RAG", "POST", "/devtools/reset/rag", "Destructive");
      skip("Reset Chat", "POST", "/devtools/reset/chat", "Destructive");
      skip("Reset Redis", "POST", "/devtools/reset/redis", "Destructive");
      skip("Reset MinIO", "POST", "/devtools/reset/minio", "Destructive");
      skip("Reset All", "POST", "/devtools/reset/all", "Destructive");
      skip("Session State", "GET", "/devtools/session-state/{session_id}", "Requires session_id");
    } catch (e: any) {
      setDevtoolsHealthError(e?.message || "Health check failed");
    } finally {
      setDevtoolsHealthLastRun(Date.now());
      setDevtoolsHealthBusy(false);
    }
  }

  async function loadDatabases() {
    setDbError(null);
    setDbBusy(true);
    try {
      const res = await fetch(`${API_BASE}/devtools/dbs`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setDbList(data.databases || []);
      if (data.databases && data.databases.length > 0) {
        setDbId((prev) => prev || data.databases[0].id);
      }
    } catch (e: any) {
      setDbError(e?.message || "Failed to load databases");
    } finally {
      setDbBusy(false);
    }
  }

  async function loadTables(nextDbId: string) {
    if (!nextDbId) return;
    setDbBusy(true);
    setDbError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/dbs/${nextDbId}/tables`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setDbTables(data.tables || []);
      setDbTable("");
      setDbRows([]);
      setDbColumns([]);
      setDbTotal(0);
      setDbOffset(0);
    } catch (e: any) {
      setDbError(e?.message || "Failed to load tables");
    } finally {
      setDbBusy(false);
    }
  }

  async function loadRecords(nextDbId: string, table: string, limit = dbLimit, offset = dbOffset) {
    if (!nextDbId || !table) return;
    setDbBusy(true);
    setDbError(null);
    try {
      const res = await fetch(
        `${API_BASE}/devtools/dbs/${nextDbId}/records?table=${encodeURIComponent(table)}&limit=${limit}&offset=${offset}`,
        { credentials: "include" }
      );
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setDbRows(data.rows || []);
      setDbColumns(data.columns || []);
      setDbTotal(Number(data.total || 0));
      setDbLimit(Number(data.limit || limit));
      setDbOffset(Number(data.offset || offset));
    } catch (e: any) {
      setDbError(e?.message || "Failed to load records");
    } finally {
      setDbBusy(false);
    }
  }

  async function loadRagOverrides() {
    setRagOverrides(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/rag/overrides`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      setRagOverrides(await res.json());
    } catch {
      // ignore
    }
  }

  async function setRagOverride(action: "disable" | "enable") {
    const payload: any = {};
    if (ragOverrideSession.trim()) payload.session_id = ragOverrideSession.trim();
    if (ragOverrideUser.trim()) payload.username = ragOverrideUser.trim();
    if (!payload.session_id && !payload.username) return;
    try {
      const res = await fetch(`${API_BASE}/devtools/rag/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await res.text());
      setRagOverrides(await res.json());
      setRagOverrideSession("");
      setRagOverrideUser("");
    } catch {
      // ignore
    }
  }

  async function downloadModelFromRepo(opts: {
    repoId: string;
    modelId?: string;
    ggufFilename?: string;
    successLabel?: string;
  }) {
    setModelsBusy(true);
    setModelsError(null);
    setHfAutoStatus("Downloading...");
    try {
      const repoId = (opts.repoId || "").trim();
      if (!repoId) {
        throw new Error("Hugging Face repo_id is required");
      }
      const res = await fetch(`${API_BASE}/devtools/models/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          repo_id: repoId,
          model_id: opts.modelId || undefined,
          gguf_filename: opts.ggufFilename || undefined,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      if (data?.mode === "inventory") {
        setHfAutoStatus(`Downloaded and synced to inventory: ${data.asset_id || data.repo_id || "asset"}`);
      } else {
        setHfAutoStatus(
          opts.successLabel ||
            `Downloaded (${data.model_type || "model"}) and registered as ${data.mode || "base"}`
        );
      }
      await loadModels();
    } catch (e: any) {
      setHfAutoStatus(null);
      setModelsError(e?.message || "Download failed");
    } finally {
      setModelsBusy(false);
    }
  }

  async function downloadHFAuto() {
    await downloadModelFromRepo({
      repoId: hfAutoRepoId,
      modelId: hfAutoModelId || undefined,
      ggufFilename: hfAutoGgufFile || undefined,
    });
  }

  async function installRecommendedLocalModel() {
    setHfAutoRepoId(RECOMMENDED_LOCAL_MODEL.repoId);
    setHfAutoModelId(RECOMMENDED_LOCAL_MODEL.modelId);
    setHfAutoGgufFile(RECOMMENDED_LOCAL_MODEL.ggufFilename);
    await downloadModelFromRepo({
      repoId: RECOMMENDED_LOCAL_MODEL.repoId,
      modelId: RECOMMENDED_LOCAL_MODEL.modelId,
      ggufFilename: RECOMMENDED_LOCAL_MODEL.ggufFilename,
      successLabel: "Recommended local model installed and synced to Lite + Base.",
    });
  }

  async function applyModeAssignment(mode: "base" | "lite" | "net") {
    setModelsBusy(true);
    setModelsError(null);
    try {
      const patch: any = {};
      if (mode === "base" && assignBaseModel.trim()) {
        patch.base = { default: assignBaseModel.trim() };
      }
      if (mode === "lite" && assignLiteModel.trim()) {
        patch.lite = { default: assignLiteModel.trim() };
      }
      if (mode === "net" && assignNetModel.trim()) {
        patch.net = { default: assignNetModel.trim() };
      }
      if (!Object.keys(patch).length) return;
      const res = await fetch(`${API_BASE}/devtools/models/registry`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadModels();
    } catch (e: any) {
      setModelsError(e?.message || "Assignment failed");
    } finally {
      setModelsBusy(false);
    }
  }

  async function deleteModel(modelId: string) {
    if (!modelId) return;
    setModelsBusy(true);
    setModelsError(null);
    try {
      const res = await fetch(`${API_BASE}/devtools/models/${encodeURIComponent(modelId)}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      await loadModels();
    } catch (e: any) {
      setModelsError(e?.message || "Delete failed");
    } finally {
      setModelsBusy(false);
    }
  }

  async function runReset(path: string, extra?: any) {
    setResetBusy(true);
    setResetError(null);
    setResetResult(null);
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          confirm: resetConfirm,
          minio_bucket: resetBucket || undefined,
          ...(extra || {}),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setResetResult(await res.json());
    } catch (e: any) {
      setResetError(e?.message || "Reset failed");
    } finally {
      setResetBusy(false);
    }
  }

  async function runNuclearReset() {
    if (!window.confirm(
      "⚠️ NUCLEAR RESET\n\nThis will permanently delete:\n" +
      "• All PostgreSQL data (RAG vectors, chat history, jobs, outbox)\n" +
      "• All MinIO files\n" +
      "• All Redis keys\n" +
      "• All RabbitMQ queued tasks\n" +
      "• All backend tmp/job cache files\n" +
      "• Browser localStorage (chats, pending uploads)\n\n" +
      "Type OK to confirm."
    )) return;

    setNuclearBusy(true);
    setNuclearError(null);
    setNuclearResult(null);
    const log: string[] = [];

    try {
      // 1. Server-side: wipe all DB + MinIO + Redis via existing endpoint
      try {
        const res = await fetch(`${API_BASE}/devtools/reset/all`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            confirm: "DELETE_EVERYTHING",
            wipe_redis_all: true,
            minio_bucket: resetBucket || undefined,
          }),
        });
        if (res.ok) {
          log.push("✅ Server: PostgreSQL + MinIO + Redis wiped");
        } else {
          const detail = await res.text();
          log.push(`⚠️ Server reset: ${detail.slice(0, 120)}`);
        }
      } catch (e: any) {
        log.push(`❌ Server reset failed: ${e?.message}`);
      }

      // 2. Purge RabbitMQ queue via Celery purge endpoint
      try {
        const res = await fetch(`${API_BASE}/devtools/reset/queue`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ confirm: "DELETE_EVERYTHING" }),
        });
        if (res.ok) {
          const data = await res.json();
          log.push(`✅ RabbitMQ: ${data?.purged ?? 0} tasks purged`);
        } else {
          log.push("⚠️ RabbitMQ purge: endpoint not available (purge manually)");
        }
      } catch {
        log.push("⚠️ RabbitMQ purge: endpoint not available (purge manually)");
      }

      // 3. Clear backend tmp/jobs via dedicated endpoint
      try {
        const res = await fetch(`${API_BASE}/devtools/reset/tmp`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ confirm: "DELETE_EVERYTHING" }),
        });
        if (res.ok) {
          const data = await res.json();
          log.push(`✅ Backend tmp: ${data?.deleted ?? 0} job folders removed`);
        } else {
          log.push("⚠️ Backend tmp: endpoint not available");
        }
      } catch {
        log.push("⚠️ Backend tmp: endpoint not available");
      }

      // 4. Clear browser localStorage
      try {
        const localKeys = [
          "chat-ui-chats",
          "chat-ui-pending-ingestion",
          "pml-ui-chats",
          "chat-ui-active-id",
        ];
        localKeys.forEach((k) => {
          try { localStorage.removeItem(k); } catch { /* ignore */ }
        });
        log.push("✅ Browser localStorage: cleared");
      } catch {
        log.push("⚠️ Browser localStorage: could not clear");
      }

      setNuclearResult(log.join("\n"));

      // Reload after short delay so fresh state loads
      setTimeout(() => { window.location.href = "/"; }, 2000);

    } catch (e: any) {
      setNuclearError(e?.message || "Nuclear reset failed");
    } finally {
      setNuclearBusy(false);
    }
  }

  async function patchSettings(patch: Record<string, boolean | string>) {
    setSettingsError(null);
    setSettingsBusy(true);
    try {
      const res = await fetch(`${API_BASE}/devtools/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(patch),
      });
      if (!res.ok) throw new Error(await res.text());
      setSettings(await res.json());
      try {
        localStorage.setItem("devtools_settings_updated", String(Date.now()));
      } catch {
        // ignore
      }
    } catch (e: any) {
      setSettingsError(e?.message || "Failed to update settings");
    } finally {
      setSettingsBusy(false);
    }
  }

  async function saveRagCollectionName() {
    const trimmed = ragCollectionDraft.trim() || "rag_documents";
    setRagCollectionDraft(trimmed);
    await patchSettings({ rag_collection_name: trimmed });
  }

  async function testIntent() {
    const res = await fetch(`${API_BASE}/devtools/intent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ text: intentInput }),
    });
    setIntentResult(await res.json());
  }

  async function testRewrite() {
    const historyArr = rewriteHistory.split("\n").filter(line => line.trim() !== "");
    const res = await fetch(`${API_BASE}/devtools/rewrite`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ text: rewriteInput, history: historyArr }),
    });
    setRewriteResult(await res.json());
  }

  async function testRetrieval() {
    const res = await fetch(`${API_BASE}/devtools/retrieve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ 
            question: retrievalQuery,
            company_document_id: retrievalDocId || "Agogo-1ST1", // Default for testing
            revision_number: retrievalRevision || "1",
            collection_name: retrievalCollectionName || settings?.rag_collection_name || "rag_documents",
        }),
    });
    setRetrievalResult(await res.json());
  }

  const activeModeMap: Record<string, any> = Array.isArray(activeModels?.modes)
    ? activeModels.modes.reduce((acc: Record<string, any>, m: any) => {
        const key = String(m?.mode || "").toLowerCase();
        if (key) acc[key] = m;
        return acc;
      }, {})
    : {};
  const activeBaseId = activeModeMap.base?.model_id || activeModeMap.base?.model || "";
  const activeLiteId = activeModeMap.lite?.model_id || activeModeMap.lite?.model || "";
  const activeNetId =
    activeModeMap.net?.provider || activeModeMap.net?.model || activeModeMap.net?.model_id || "";
  const selectedPreprocessingJob =
    preprocessingJobs.find((job) => job.job_id === selectedPreprocessingJobId) || null;
  const baseAssignmentOptions = Array.from(
    new Set([
      ...Object.keys(modelsData?.hf_models || {}),
      ...Object.keys(modelsData?.gguf_models || {}),
    ])
  );
  const modelInventory: any[] = Array.isArray(modelsData?.inventory) ? modelsData.inventory : [];
  const modelInventoryGroups: Record<string, any[]> = modelInventory.reduce((acc: Record<string, any[]>, item: any) => {
    const key = String(item?.group || "other").toLowerCase();
    if (!acc[key]) acc[key] = [];
    acc[key].push(item);
    return acc;
  }, {});
  const inventoryGroupLabels: Record<string, string> = {
    chat: "Chat Models",
    retrieval: "Retrieval",
    reasoning: "Intent / Reasoning",
    preprocessing: "Preprocessing",
    discovered: "Discovered Local Assets",
    other: "Other",
  };
  const downloadStatusLabel = modelsBusy
    ? "Downloading"
    : modelsError
      ? "Failed"
      : hfAutoStatus
        ? "Completed"
        : "Idle";
  const devtoolsHealthCounts = devtoolsHealth
    ? devtoolsHealth.reduce(
        (acc, entry) => {
          acc[entry.status] += 1;
          return acc;
        },
        { ok: 0, fail: 0, skipped: 0 } as Record<DevtoolsHealthStatus, number>
      )
    : null;
  const devtoolsHealthChecked = devtoolsHealth ? devtoolsHealth.filter((e) => e.status !== "skipped") : [];
  const devtoolsHealthSkipped = devtoolsHealth ? devtoolsHealth.filter((e) => e.status === "skipped") : [];
  const filteredDbTables = dbTables.filter((t) =>
    t.toLowerCase().includes(dbTableQuery.trim().toLowerCase())
  );

  if (backendWarming) {
    return (
      <StartupLoader
        title="Starting developer tools…"
        subtitle="Initializing services. This usually takes a few seconds."
        services={warmupServices}
        icon={<Key size={18} className="text-blue-400" />}
      />
    );
  }

  if (backendWarmupError) {
    return (
      <StartupLoader
        title="Service startup in progress"
        subtitle={backendWarmupError}
        services={warmupServices}
        icon={<Key size={18} className="text-blue-400" />}
        actionLabel="Retry"
        onAction={() => runBackendWarmup()}
      />
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold text-white">Developer Dashboard</h1>
          <span className="rounded bg-white/10 px-2 py-0.5 text-xs text-gray-400">v1.0</span>
        </div>
        <div className="flex items-center gap-3">
          <div
            className={`text-xs px-2 py-1 rounded border ${
              backendHealth === "ok"
                ? "border-green-500/30 bg-green-500/10 text-green-300"
                : backendHealth === "degraded"
                  ? "border-yellow-500/30 bg-yellow-500/10 text-yellow-300"
                  : backendHealth === "error"
                    ? "border-red-500/30 bg-red-500/10 text-red-300"
                    : "border-white/10 bg-white/5 text-gray-400"
            }`}
          >
            Backend:{" "}
            {backendHealth === "ok"
              ? "Connected"
              : backendHealth === "degraded"
                ? "Degraded"
                : backendHealth === "error"
                  ? "Offline"
                  : "Checking"}
          </div>
          <Link href="/" className="text-sm text-gray-400 hover:text-white hover:underline">
            Back to Chat
          </Link>
        </div>
      </div>

      {/* TABS */}
      <div className="mb-8 flex gap-4 border-b border-white/10 pb-1 overflow-x-auto">
        <TabButton active={activeTab === "settings"} onClick={() => setActiveTab("settings")} label="Settings" />
        <TabButton active={activeTab === "preprocessing"} onClick={() => setActiveTab("preprocessing")} label="Preprocessing" />
        <TabButton active={activeTab === "models"} onClick={() => setActiveTab("models")} label="Models" />
        <TabButton active={activeTab === "runtime"} onClick={() => setActiveTab("runtime")} label="Runtime" />
        <TabButton active={activeTab === "users"} onClick={() => setActiveTab("users")} label="Users" />
        <TabButton active={activeTab === "databases"} onClick={() => setActiveTab("databases")} label="Databases" />
        <TabButton active={activeTab === "danger"} onClick={() => setActiveTab("danger")} label="Danger Zone" />
        <TabButton active={activeTab === "retrieve"} onClick={() => setActiveTab("retrieve")} label="RAG Retrieval" />
        <TabButton active={activeTab === "intent"} onClick={() => setActiveTab("intent")} label="Intent Classifier" />
        <TabButton active={activeTab === "rewrite"} onClick={() => setActiveTab("rewrite")} label="Query Rewriter" />
        <TabButton active={activeTab === "health"} onClick={() => setActiveTab("health")} label="System Health" />
      </div>

      {/* === TAB: SETTINGS === */}
      {activeTab === "settings" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-1 space-y-6">
            <Card title="Developer Flags">
              {settingsError && (
                <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">
                  {settingsError}
                </div>
              )}

              {!settings ? (
                <div className="text-gray-500 italic">Loading settings...</div>
              ) : (
                <>
                  <div className="text-xs text-gray-400 mb-3">
                    Runtime flags that control chat behavior and retrieval.
                  </div>
                  <div className="mb-3 rounded border border-white/10 bg-black/30 px-3 py-3">
                    <Toggle
                      label="Fast document processing"
                      description="Off keeps uploads on the strict high-accuracy path for richer table extraction, image classification, and stronger header/footer cleanup. Turn it on only when you want faster preprocessing."
                      value={!!settings.enable_fast_document_processing}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ enable_fast_document_processing: v })}
                    />
                  </div>
                  <div className="mb-3 rounded border border-white/10 bg-black/30 px-3 py-3">
                    <label className="block text-xs text-gray-400 mb-2">RAG Collection Name</label>
                    <div className="flex gap-2">
                      <input
                        value={ragCollectionDraft}
                        disabled={settingsBusy}
                        onChange={(e) => setRagCollectionDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void saveRagCollectionName();
                          }
                        }}
                        className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                        placeholder="rag_documents"
                      />
                      <button
                        type="button"
                        onClick={() => void saveRagCollectionName()}
                        disabled={settingsBusy}
                        className="shrink-0 rounded bg-gray-800 px-3 py-2 text-sm text-white hover:bg-gray-700 disabled:opacity-50"
                      >
                        Save
                      </button>
                    </div>
                    <div className="mt-2 text-xs text-gray-500">
                      Uploads and chat retrieval will use this collection. Use different names to isolate parser tests.
                    </div>
                  </div>
                  <div className="mb-3 rounded border border-white/10 bg-black/30 px-3 py-3">
                    <label className="block text-xs text-gray-400 mb-2">RAG Retrieval Mode</label>
                    <select
                      value={String(settings.rag_retrieval_mode || "auto")}
                      disabled={settingsBusy}
                      onChange={(e) => patchSettings({ rag_retrieval_mode: e.target.value })}
                      className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    >
                      <option value="auto">Auto (intent-based)</option>
                      <option value="fast">Fast</option>
                      <option value="balanced">Balanced</option>
                      <option value="high_fidelity">High Fidelity</option>
                    </select>
                    <div className="mt-2 text-xs text-gray-500">
                      Auto routing: factual/definition to Fast, summary/compare to High Fidelity, others to Balanced.
                    </div>
                  </div>
                  <div className="mb-3 rounded border border-emerald-500/20 bg-emerald-500/5 px-3 py-3">
                    <div className="text-[11px] uppercase tracking-wide text-emerald-300">Document Processing Profile</div>
                    <div className="mt-2 text-sm text-white">
                      {documentProcessingProfile.label === "fast" ? "Fast" : "High accuracy"} using{" "}
                      <span className="font-mono text-emerald-200">
                        {String(documentProcessingProfile.rag_preprocessor || settings.rag_preprocessor || "unstructured")}
                      </span>
                    </div>
                    <div className="mt-2 text-xs text-emerald-100/80 space-y-1">
                      <div>Ingest mode: {String(documentProcessingProfile.rag_ingest_mode || settings.rag_ingest_mode || settings.rag_mode || "high_fidelity")}</div>
                      <div>Preview scope: {String(documentProcessingProfile.preview_scope || "full")}</div>
                      <div>Header/footer cleanup: {String(documentProcessingProfile.header_footer_cleanup || "strict")}</div>
                      <div>Image classification: {documentProcessingProfile.image_classification ? "enabled" : "reduced"}</div>
                      <div>
                        Collection:{" "}
                        <span className="font-mono">
                          {String(settings.rag_collection_name || "rag_documents")}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 gap-3">
                    <Toggle
                      label="Query rewrite"
                      description="Enable query rewrite and intent routing before retrieval"
                      value={!!settings.enable_query_rewrite}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ enable_query_rewrite: v })}
                    />
                    <Toggle
                      label="Agent pipeline"
                      description="Allow orchestrator-based multi-step answer generation"
                      value={!!settings.enable_agent_pipeline}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ enable_agent_pipeline: v })}
                    />
                    <Toggle
                      label="Hybrid retrieval"
                      description="Enable keyword search, fusion, reranking, and retrieval extras"
                      value={!!settings.enable_hybrid_retrieval}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ enable_hybrid_retrieval: v })}
                    />
                    <Toggle
                      label="Learning"
                      description="Enable adaptive retrieval, retrieval stats, and few-shot helpers"
                      value={!!settings.enable_learning}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ enable_learning: v })}
                    />
                    <Toggle
                      label="Eval gate"
                      description="Enable release gating and answer evaluation checks"
                      value={!!settings.enable_eval_gate}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ enable_eval_gate: v })}
                    />
                    <Toggle
                      label="Cache"
                      description="Enable retrieval cache reuse between similar questions"
                      value={!!settings.enable_cache}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ enable_cache: v })}
                    />
                    <Toggle
                      label="RAG visualization"
                      description="Show model stages in chat"
                      value={!!settings.emit_model_stage_events}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ emit_model_stage_events: v })}
                    />
                    <Toggle
                      label="Confidence score"
                      description="Show answer confidence in chat"
                      value={!!settings.emit_answer_confidence}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ emit_answer_confidence: v })}
                    />
                    <Toggle
                      label="Emit sources"
                      description="Attach citations when available"
                      value={!!settings.emit_sources}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ emit_sources: v })}
                    />
                    <Toggle
                      label="Detailed retrieval"
                      description="Extra retrieval steps"
                      value={!!settings.force_detailed_retrieval}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ force_detailed_retrieval: v })}
                    />
                    <Toggle
                      label="Bypass retrieval policy"
                      description="Ignore retrieval rules (advanced)"
                      value={!!settings.disable_retrieval_policy}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ disable_retrieval_policy: v })}
                    />
                    <Toggle
                      label="Disable RAG globally"
                      description="Force answers without RAG"
                      value={!!settings.disable_rag_globally}
                      disabled={settingsBusy}
                      onChange={(v) => patchSettings({ disable_rag_globally: v })}
                    />
                  </div>

                  <button
                    onClick={loadSettings}
                    disabled={settingsBusy}
                    className="mt-4 w-full bg-gray-800 hover:bg-gray-700 text-white px-4 py-2 rounded disabled:opacity-50"
                  >
                    Refresh
                  </button>
                </>
              )}
            </Card>

            {ragOverrides?.enabled && (
              <Card title="RAG Overrides (Per Session/User)">
                <div className="text-xs text-gray-500 mb-3">
                  Disable retrieval for a specific session ID or username.
                </div>
                <label className="block text-xs text-gray-400 mb-2">Session ID</label>
                <input
                  value={ragOverrideSession}
                  onChange={(e) => setRagOverrideSession(e.target.value)}
                  className="w-full bg-[#222] border border-white/10 rounded p-2 text-white mb-3"
                  placeholder="e.g., 8f8e7a50-..."
                />
                <label className="block text-xs text-gray-400 mb-2">Username</label>
                <input
                  value={ragOverrideUser}
                  onChange={(e) => setRagOverrideUser(e.target.value)}
                  className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                  placeholder="e.g., alice"
                />
                <div className="mt-4 flex gap-2">
                  <button
                    onClick={() => setRagOverride("disable")}
                    className="flex-1 bg-red-600/80 hover:bg-red-600 text-white px-3 py-2 rounded text-sm"
                  >
                    Disable RAG
                  </button>
                  <button
                    onClick={() => setRagOverride("enable")}
                    className="flex-1 bg-green-600/80 hover:bg-green-600 text-white px-3 py-2 rounded text-sm"
                  >
                    Enable RAG
                  </button>
                </div>
                <div className="mt-4 text-xs text-gray-400">
                  Disabled sessions: {ragOverrides?.disabled_sessions?.length || 0}
                  <br />
                  Disabled users: {ragOverrides?.disabled_users?.length || 0}
                </div>
              </Card>
            )}
          </div>

          <div className="lg:col-span-2 space-y-6">
            <Card title="What These Affect">
              <div className="text-gray-300 space-y-3 text-sm leading-relaxed">
                <div><span className="text-white font-medium">Fast document processing</span> is the single upload-quality toggle. Off forces the strict high-accuracy path, full preview scope, stronger table parsing, and tougher cleanup of repeated headers, footers, page markers, and image placeholders.</div>
                <div><span className="text-white font-medium">RAG collection name</span> decides which PGVector collection both upload indexing and chat retrieval use.</div>
                <div><span className="text-white font-medium">RAG retrieval mode</span> controls live query retrieval depth. Auto applies intent-based routing with manual override.</div>
                <div><span className="text-white font-medium">Query rewrite</span> re-enables the pre-retrieval rewrite and intent classification layer.</div>
                <div><span className="text-white font-medium">Agent pipeline</span> re-enables the orchestrated multi-step answer flow instead of single-model generation.</div>
                <div><span className="text-white font-medium">Hybrid retrieval</span> adds keyword search, fusion, reranking, and metadata-heavy retrieval behaviors.</div>
                <div><span className="text-white font-medium">Learning</span> re-enables adaptive retrieval, retrieval stats, and few-shot prompt injection.</div>
                <div><span className="text-white font-medium">Eval gate</span> re-enables release checks and factual answer gating after generation.</div>
                <div><span className="text-white font-medium">Cache</span> re-enables retrieval result reuse for repeated questions.</div>
                <div><span className="text-white font-medium">RAG visualization</span> shows high-level retrieval stages during responses.</div>
                <div><span className="text-white font-medium">Confidence score</span> controls the confidence badge in chat.</div>
                <div><span className="text-white font-medium">Sources</span> controls the citations button and source viewer.</div>
                <div><span className="text-white font-medium">Force detailed retrieval</span> increases candidate chunks (slower, more recall).</div>
                <div><span className="text-white font-medium">Disable retrieval policy</span> bypasses the adaptive retrieval filter (debug).</div>
                <div><span className="text-white font-medium">Disable RAG globally</span> stops retrieval for all sessions.</div>
              </div>
            </Card>

            <Card title="DevTools API Health" className="max-h-[70vh] overflow-hidden">
              <div className="text-xs text-gray-400 mb-3">
                Safe read-only checks. Mutating endpoints are listed as skipped.
              </div>
              {devtoolsHealthError && (
                <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200 whitespace-pre-wrap">
                  {devtoolsHealthError}
                </div>
              )}
              <div className="flex items-center gap-3">
                <button
                  onClick={runDevtoolsHealthCheck}
                  disabled={devtoolsHealthBusy}
                  className="bg-blue-600 hover:bg-blue-500 text-white px-3 py-2 rounded text-sm disabled:opacity-50"
                >
                  {devtoolsHealthBusy ? "Running..." : "Run Checks"}
                </button>
                {devtoolsHealthLastRun && (
                  <span className="text-xs text-gray-500">
                    Last run: {new Date(devtoolsHealthLastRun).toLocaleTimeString()}
                  </span>
                )}
              </div>

              {devtoolsHealth && (
                <>
                  <div className="mt-3 flex flex-wrap gap-2 text-xs">
                    <span className="rounded border border-green-500/30 bg-green-500/10 px-2 py-0.5 text-green-300">
                      OK {devtoolsHealthCounts?.ok ?? 0}
                    </span>
                    <span className="rounded border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-red-300">
                      Failed {devtoolsHealthCounts?.fail ?? 0}
                    </span>
                    <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-gray-300">
                      Skipped {devtoolsHealthCounts?.skipped ?? 0}
                    </span>
                  </div>
                  <div className="mt-3 max-h-56 overflow-auto rounded-lg border border-white/10 divide-y divide-white/10">
                    {devtoolsHealthChecked.map((entry) => (
                      <div
                        key={`${entry.method}-${entry.path}-${entry.name}`}
                        className="flex items-start justify-between gap-3 px-3 py-2"
                      >
                        <div className="flex items-start gap-2 min-w-0">
                          <span
                            className={`mt-1 h-2.5 w-2.5 rounded-full ${
                              entry.status === "ok" ? "bg-green-400" : "bg-red-400"
                            }`}
                          />
                          <div className="min-w-0">
                            <div className="text-xs text-gray-300 break-all">
                              <span className="font-semibold">{entry.method}</span> {entry.path}
                            </div>
                            <div className="text-[11px] text-gray-500">{entry.name}</div>
                            {entry.detail && entry.status === "fail" && (
                              <div className="text-[11px] text-red-300 mt-1 truncate" title={entry.detail}>
                                {entry.detail}
                              </div>
                            )}
                          </div>
                        </div>
                        <div className="shrink-0 text-[11px] text-gray-500 text-right">
                          {entry.timeMs !== undefined && <div>{entry.timeMs}ms</div>}
                          {entry.code && <div>{entry.code}</div>}
                        </div>
                      </div>
                    ))}
                  </div>

                  {devtoolsHealthSkipped.length > 0 && (
                    <details className="mt-3">
                      <summary className="text-xs text-gray-400 cursor-pointer">
                        Skipped endpoints ({devtoolsHealthSkipped.length})
                      </summary>
                      <div className="mt-2 max-h-32 overflow-auto space-y-2 pr-1">
                        {devtoolsHealthSkipped.map((entry) => (
                          <div
                            key={`${entry.method}-${entry.path}-${entry.name}`}
                            className="flex items-start justify-between gap-3 rounded border border-white/10 bg-black/30 px-3 py-2"
                          >
                            <div className="min-w-0">
                              <div className="text-xs text-gray-300 break-all">
                                <span className="font-semibold">{entry.method}</span> {entry.path}
                              </div>
                              <div className="text-[11px] text-gray-500">{entry.name}</div>
                              {entry.detail && (
                                <div className="text-[11px] text-gray-500 mt-1">{entry.detail}</div>
                              )}
                            </div>
                            <span className="text-[11px] px-2 py-0.5 rounded bg-gray-500/20 text-gray-300">
                              SKIPPED
                            </span>
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </>
              )}
            </Card>
          </div>
        </div>
      )}

      {/* === TAB: RETRIEVAL === */}
      {activeTab === "retrieve" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-1 space-y-6">
                <Card title="Search Parameters">
                    <label className="block text-sm text-gray-400 mb-2">Question</label>
                    <input 
                        value={retrievalQuery}
                        onChange={(e) => setRetrievalQuery(e.target.value)}
                        className="w-full bg-[#222] border border-white/10 rounded p-2 text-white mb-4"
                        placeholder="e.g. What is the design pressure?"
                    />
                    <label className="block text-sm text-gray-400 mb-2">Document ID</label>
                    <input 
                        value={retrievalDocId}
                        onChange={(e) => setRetrievalDocId(e.target.value)}
                        className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                        placeholder="e.g. Agogo-1ST1"
                    />
                    <label className="block text-sm text-gray-400 mb-2 mt-4">Revision</label>
                    <input
                        value={retrievalRevision}
                        onChange={(e) => setRetrievalRevision(e.target.value)}
                        className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                        placeholder="e.g. 1"
                    />
                    <label className="block text-sm text-gray-400 mb-2 mt-4">Collection Name</label>
                    <input
                        value={retrievalCollectionName}
                        onChange={(e) => setRetrievalCollectionName(e.target.value)}
                        className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                        placeholder="rag_documents"
                    />
                    <div className="mt-2 text-xs text-gray-500">
                        Leave this synced with Settings to test the active parser collection, or point it at another benchmark collection.
                    </div>
                    <button onClick={testRetrieval} className="mt-4 w-full bg-green-600 hover:bg-green-500 text-white px-4 py-2 rounded flex items-center justify-center gap-2">
                        <Search size={16} /> Run Retrieval
                    </button>
                </Card>
            </div>
            
            <div className="lg:col-span-2">
                 <Card title="Retrieved Chunks">
                    {retrievalResult ? (
                        <div className="space-y-4">
                            <div className="text-xs text-gray-500 mb-2">
                              Found {retrievalResult.count} chunks from{" "}
                              <span className="font-mono text-gray-300">
                                {retrievalResult.collection_name || retrievalCollectionName || "rag_documents"}
                              </span>
                            </div>
                            {(retrievalResult.chunks ?? retrievalResult.preview ?? []).map((chunk: any, i: number) => (
                                <div key={i} className="bg-[#1a1a1a] border border-white/5 rounded p-3">
                                    <div className="flex justify-between items-start mb-2">
                                        <span className="text-xs font-mono text-blue-400 bg-blue-900/20 px-2 py-0.5 rounded">
                                            Score: {chunk.score.toFixed(4)}
                                        </span>
                                        <span className="text-xs text-gray-500">{chunk.section}</span>
                                    </div>
                                    <p className="text-sm text-gray-300 whitespace-pre-wrap">{chunk.content}</p>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-gray-500 italic flex flex-col items-center justify-center h-40">
                            <FileText size={40} className="mb-2 opacity-20" />
                            Run a search to see vectors.
                        </div>
                    )}
                 </Card>
            </div>
        </div>
      )}

      {/* === TAB: PREPROCESSING === */}
      {activeTab === "preprocessing" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-1 space-y-6">
            <Card title="Preview Loader">
              <div className="text-xs text-gray-400 mb-3">
                Open a document preprocessing preview from a recent upload job. This shows OCR elements,
                extracted tables and figures, removed headers and footers, and the chunks that will be indexed.
                The dashboard loads the full preview so you can inspect all detected tables and images for the selected upload.
              </div>
              <label className="block text-xs text-gray-400 mb-2">Upload Job ID</label>
              <div className="flex gap-2">
                <input
                  value={preprocessingJobDraft}
                  onChange={(e) => setPreprocessingJobDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void openDashboardPreprocessingJob(preprocessingJobDraft, "full");
                    }
                  }}
                  className="w-full bg-[#222] border border-white/10 rounded p-2 text-white font-mono text-xs"
                  placeholder="Paste a job_id from an upload"
                />
                <button
                  type="button"
                  onClick={() => void openDashboardPreprocessingJob(preprocessingJobDraft, "full")}
                  disabled={preprocessingPreviewLoading}
                  className="shrink-0 rounded bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
                >
                  Open Full Preview
                </button>
              </div>
              {selectedPreprocessingJob ? (
                <div className="mt-4 rounded border border-blue-500/20 bg-blue-500/10 p-3 text-xs text-blue-100">
                  <div className="font-medium text-white">
                    {selectedPreprocessingJob.source_file || "Selected upload"}
                  </div>
                  <div className="mt-1 font-mono text-blue-200 break-all">
                    {selectedPreprocessingJob.job_id}
                  </div>
                  <div className="mt-2 space-y-1 text-blue-100/80">
                    <div>Status: {selectedPreprocessingJob.status}</div>
                    <div>Preprocessor: {selectedPreprocessingJob.rag_preprocessor || "unknown"}</div>
                    <div>Ingest mode: {selectedPreprocessingJob.rag_ingest_mode || "unknown"}</div>
                    <div>Collection: {selectedPreprocessingJob.rag_collection_name || "rag_documents"}</div>
                    {formatSegmentationSnapshot(selectedPreprocessingJob.segregation_summary) ? (
                      <div>Filtered groups: {formatSegmentationSnapshot(selectedPreprocessingJob.segregation_summary)}</div>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </Card>

            <Card title="Recent Upload Jobs" className="max-h-[75vh] overflow-hidden">
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="text-xs text-gray-400">
                  Pick a recent upload to inspect how table, figure, and cleanup extraction behaved.
                </div>
                <button
                  type="button"
                  onClick={() => void loadRecentUploadJobs()}
                  disabled={preprocessingJobsBusy}
                  className="inline-flex items-center gap-2 rounded border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-50"
                >
                  <RefreshCw size={12} className={preprocessingJobsBusy ? "animate-spin" : ""} />
                  Refresh
                </button>
              </div>

              {preprocessingJobsError ? (
                <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">
                  {preprocessingJobsError}
                </div>
              ) : null}

              {!preprocessingJobs.length ? (
                <div className="rounded border border-dashed border-white/10 bg-black/30 p-4 text-sm text-gray-400">
                  No upload jobs found yet. Upload a document first, then come back here to inspect the preprocessing output.
                </div>
              ) : (
                <div className="space-y-3 overflow-auto pr-1 max-h-[58vh]">
                  {preprocessingJobs.map((job) => {
                    const isSelected = job.job_id === selectedPreprocessingJobId;
                    return (
                      <div
                        key={job.job_id}
                        className={`rounded-lg border p-3 ${
                          isSelected
                            ? "border-blue-500/40 bg-blue-500/10"
                            : "border-white/10 bg-black/30"
                        }`}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-sm text-white truncate">
                              {job.source_file || "Unnamed upload"}
                            </div>
                            <div className="mt-1 text-[11px] font-mono text-gray-400 break-all">
                              {job.job_id}
                            </div>
                          </div>
                          <span className="shrink-0 rounded bg-white/10 px-2 py-1 text-[10px] uppercase tracking-wide text-gray-200">
                            {job.status}
                          </span>
                        </div>

                        <div className="mt-3 space-y-1 text-[11px] text-gray-400">
                          <div>Document: {job.company_document_id || "n/a"} / rev {job.revision_number || "n/a"}</div>
                          <div>Parser: {job.rag_preprocessor || "unknown"} / {job.rag_ingest_mode || "unknown"}</div>
                          {formatSegmentationSnapshot(job.segregation_summary) ? (
                            <div>Filtered groups: {formatSegmentationSnapshot(job.segregation_summary)}</div>
                          ) : null}
                          <div>
                            Updated: {job.updated_at ? new Date(job.updated_at).toLocaleString() : "n/a"}
                          </div>
                          {job.progress_label ? <div>{job.progress_label}</div> : null}
                          {job.missing_fields?.length ? (
                            <div>Missing metadata fields: {job.missing_fields.join(", ")}</div>
                          ) : null}
                          {!job.preview_available && job.preview_unavailable_reason ? (
                            <div className="text-amber-300">{job.preview_unavailable_reason}</div>
                          ) : null}
                          {job.artifact_only_preview ? (
                            <div className="text-cyan-300">
                              Cached preview only. Source page images are unavailable because the local PDF was cleaned up after ingestion.
                            </div>
                          ) : null}
                          {job.error ? (
                            <div className="text-red-300">{job.error}</div>
                          ) : null}
                        </div>

                        <div className="mt-3 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => void openDashboardPreprocessingJob(job.job_id, "full")}
                            disabled={!job.preview_available || preprocessingPreviewLoading}
                            className="rounded bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            Open full preview
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>
          </div>

          <div className="lg:col-span-2 space-y-6">
            <Card title="What You Can Inspect">
                <div className="text-gray-300 space-y-3 text-sm leading-relaxed">
                  <div><span className="text-white font-medium">Tables</span> show the OCR text, structured HTML, row and column counts, and a cropped image of the detected table area.</div>
                  <div><span className="text-white font-medium">Images</span> show retained figure regions and figure captions that now flow into their own retrieval lane.</div>
                  <div><span className="text-white font-medium">OCR Metadata</span> shows the elements and evidence used for metadata extraction.</div>
                  <div><span className="text-white font-medium">Removed Elements</span> shows headers, footers, page markers, image placeholders, and repeated boilerplate that were filtered out before chunking.</div>
                  <div><span className="text-white font-medium">Chunks</span> shows the final text, table, and image blocks that are prepared for indexing after preprocessing cleanup.</div>
                </div>
              </Card>

            {selectedPreprocessingJobId ? (
              <PreprocessingPreviewPanel
                jobId={selectedPreprocessingJobId}
                preview={preprocessingPreview}
                loading={preprocessingPreviewLoading}
                error={preprocessingPreviewError}
                defaultTab="tables"
                onRetry={() => {
                  if (selectedPreprocessingJobId) {
                    void loadDashboardPreprocessingPreview(selectedPreprocessingJobId, "full");
                  }
                }}
                onLoadFullPreview={() => {
                  if (selectedPreprocessingJobId) {
                    void loadDashboardPreprocessingPreview(selectedPreprocessingJobId, "full");
                  }
                }}
              />
            ) : (
              <Card title="Preprocessing Preview">
                <div className="text-gray-400">
                  Select a recent upload job or paste a job ID to inspect table, figure, and preprocessing output.
                </div>
              </Card>
            )}
          </div>
        </div>
      )}

      {/* === TAB: MODELS === */}
      {activeTab === "models" && (
        <div className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-stretch">
            <Card title="Active LLM Status" className="p-3" titleClassName="text-sm mb-2">
              {activeModels?.modes ? (
                <div className="space-y-1">
                  {[
                    { label: "BASE", data: activeModeMap.base },
                    { label: "LITE", data: activeModeMap.lite },
                    { label: "NET", data: activeModeMap.net },
                  ].map(({ label, data }) => {
                    const isNet = label === "NET";
                    const statusLabel = data?.ready
                      ? isNet
                        ? "Configured"
                        : "Ready"
                      : isNet
                        ? "Not Configured"
                        : "Error";
                    const statusClass = data?.ready
                      ? "bg-green-500/20 text-green-300"
                      : "bg-red-500/20 text-red-300";
                    return (
                      <div key={label} className="rounded border border-white/10 bg-black/30 px-2 py-1">
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2 min-w-0">
                              <div className="w-10 text-[11px] font-semibold text-gray-300">{label}</div>
                              <div className="text-sm text-white truncate">
                                {isNet
                                  ? data?.provider || data?.model || "Unknown provider"
                                  : data?.model_id || data?.model || "Unknown"}
                              </div>
                              {data?.type && (
                                <span className="text-[11px] text-gray-500">({data.type})</span>
                              )}
                            </div>
                            {!isNet && data?.configured_model_id && data?.configured_model_id !== data?.model_id && (
                              <div className="mt-1 pl-12 text-[11px] text-gray-500">
                                Configured default: {data.configured_model_id}
                              </div>
                            )}
                          </div>
                          <div className={`text-[11px] px-2 py-0.5 rounded ${statusClass}`}>
                            {statusLabel}
                          </div>
                        </div>
                        {data?.error && (
                          <div className="mt-1 text-xs text-red-300 whitespace-pre-wrap">{data.error}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-gray-500 italic">No data loaded yet.</div>
              )}
            </Card>

            <Card title="Available Modules" className="p-4" titleClassName="text-sm mb-2">
              <div className="text-xs text-gray-500 mb-2">Registry defaults</div>
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-gray-400">Base (HF)</span>
                  <span className="text-gray-200 truncate">
                      {modelsData?.configured_model_registry?.base?.default || "Not set"}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-gray-400">Lite (GGUF)</span>
                  <span className="text-gray-200 truncate">
                      {modelsData?.configured_model_registry?.lite?.default || "Not set"}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-gray-400">Net (Provider)</span>
                  <span className="text-gray-200 truncate">
                      {modelsData?.configured_model_registry?.net?.default || "Not set"}
                  </span>
                </div>
              </div>
              <div className="mt-3 text-xs text-gray-500">
                Models available: HF {Object.keys(modelsData?.hf_models || {}).length} · GGUF{" "}
                {Object.keys(modelsData?.gguf_models || {}).length}
              </div>
              <div className="mt-1 text-xs text-gray-500">
                Inventory entries: {modelInventory.length} · Default preprocessor:{" "}
                {String(modelsData?.default_rag_preprocessor || "unknown")}
              </div>
            </Card>

            <Card title="Download Status" className="p-4" titleClassName="text-sm mb-2">
              <div className="text-sm text-gray-200">State: {downloadStatusLabel}</div>
              <div className="mt-1 text-xs text-gray-500">
                Current: {modelsBusy ? hfAutoRepoId || hfAutoModelId || "Unknown" : "Idle"}
              </div>
              <div className="mt-1 text-xs text-gray-500">
                Last: {hfAutoStatus || "No recent downloads"}
              </div>
              {modelsError && (
                <div className="mt-3 text-xs text-red-300 whitespace-pre-wrap">{modelsError}</div>
              )}
            </Card>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-stretch">
            <Card title="Stored Models On Disk">
              <div className="text-xs text-gray-500 mb-3">
                HF cache: <span className="font-mono">models/hf_cache</span> | GGUF:
                <span className="font-mono"> models/gguf</span>
              </div>
              {Object.keys(modelsData?.hf_models || {}).length === 0 &&
              Object.keys(modelsData?.gguf_models || {}).length === 0 && (
                <div className="mb-3 rounded border border-yellow-500/20 bg-yellow-500/10 px-3 py-2 text-xs text-yellow-100">
                  Local model cache is empty. That is why the HF and GGUF lists are blank here and active local
                  model status shows missing files. The Backend Model Inventory below still shows the expected backend
                  assets and whether each one is ready or missing.
                </div>
              )}
              <div className="space-y-4 max-h-72 overflow-auto pr-1">
                <div>
                  <div className="text-xs text-gray-400 mb-2">HF Models</div>
                  {Object.entries(modelsData?.hf_models || {}).map(([id, repo]: any) => (
                    <div
                      key={id}
                      className="flex items-center justify-between gap-3 rounded border border-white/10 bg-black/30 p-2 mb-2"
                    >
                      <div className="min-w-0">
                        <div className="text-sm text-white">{id}</div>
                        <div className="text-xs text-gray-500 truncate" title={String(repo || "")}>
                          {String(repo || "").split(/[\\/]/).filter(Boolean).pop() || String(repo || "")}
                        </div>
                      </div>
                      <button
                        onClick={() => {
                          if (window.confirm(`Delete model "${id}"? This removes files from disk.`)) {
                            deleteModel(id);
                          }
                        }}
                        disabled={modelsBusy}
                        className="px-3 py-1.5 text-xs rounded bg-red-600/80 hover:bg-red-600 text-white disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </div>
                  ))}
                  {Object.keys(modelsData?.hf_models || {}).length === 0 && (
                    <div className="text-xs text-gray-500 italic">No HF model snapshots found on disk.</div>
                  )}
                </div>

                <div>
                  <div className="text-xs text-gray-400 mb-2">GGUF Models</div>
                  {Object.entries(modelsData?.gguf_models || {}).map(([id, path]: any) => (
                    <div
                      key={id}
                      className="flex items-center justify-between gap-3 rounded border border-white/10 bg-black/30 p-2 mb-2"
                    >
                      <div className="min-w-0">
                        <div className="text-sm text-white">{id}</div>
                        <div className="text-xs text-gray-500 truncate" title={String(path || "")}>
                          {String(path || "").split(/[\\/]/).filter(Boolean).pop() || String(path || "")}
                        </div>
                      </div>
                      <button
                        onClick={() => {
                          if (window.confirm(`Delete model "${id}"? This removes files from disk.`)) {
                            deleteModel(id);
                          }
                        }}
                        disabled={modelsBusy}
                        className="px-3 py-1.5 text-xs rounded bg-red-600/80 hover:bg-red-600 text-white disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </div>
                  ))}
                  {Object.keys(modelsData?.gguf_models || {}).length === 0 && (
                    <div className="text-xs text-gray-500 italic">No GGUF files found on disk.</div>
                  )}
                </div>
              </div>
            </Card>

            <Card title="Assign Models Per Mode">
              <div className="space-y-3">
                <div className="rounded border border-white/10 bg-black/30 p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      <label className="block text-xs text-gray-400">Base (Best Local)</label>
                      {activeBaseId && (
                        <span className="text-[10px] px-2 py-0.5 rounded bg-green-500/20 text-green-300">
                          Active
                        </span>
                      )}
                    </div>
                  </div>
                  <select
                    value={assignBaseModel}
                    onChange={(e) => setAssignBaseModel(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    disabled={modelsBusy}
                  >
                    {baseAssignmentOptions.map((id) => (
                      <option key={id} value={id}>
                        {id}
                      </option>
                    ))}
                  </select>
                  <div className="mt-2 flex items-center justify-between">
                    {activeBaseId ? (
                      <div className="text-[11px] text-gray-500">Active: {activeBaseId}</div>
                    ) : (
                      <span />
                    )}
                    <button
                      onClick={() => applyModeAssignment("base")}
                      disabled={modelsBusy || !assignBaseModel}
                      className="px-3 py-1.5 text-xs rounded bg-green-600 hover:bg-green-500 text-white disabled:opacity-50"
                    >
                      Apply
                    </button>
                  </div>
                  <div className="mt-2 text-[11px] text-gray-500">
                    Base can use either an HF snapshot or a GGUF local model on CPU-first machines.
                  </div>
                </div>

                <div className="rounded border border-white/10 bg-black/30 p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      <label className="block text-xs text-gray-400">Lite (GGUF)</label>
                      {activeLiteId && (
                        <span className="text-[10px] px-2 py-0.5 rounded bg-green-500/20 text-green-300">
                          Active
                        </span>
                      )}
                    </div>
                  </div>
                  <select
                    value={assignLiteModel}
                    onChange={(e) => setAssignLiteModel(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    disabled={modelsBusy}
                  >
                    {(Object.keys(modelsData?.gguf_models || {}) || []).map((id) => (
                      <option key={id} value={id}>
                        {id}
                      </option>
                    ))}
                  </select>
                  <div className="mt-2 flex items-center justify-between">
                    {activeLiteId ? (
                      <div className="text-[11px] text-gray-500">Active: {activeLiteId}</div>
                    ) : (
                      <span />
                    )}
                    <button
                      onClick={() => applyModeAssignment("lite")}
                      disabled={modelsBusy || !assignLiteModel}
                      className="px-3 py-1.5 text-xs rounded bg-green-600 hover:bg-green-500 text-white disabled:opacity-50"
                    >
                      Apply
                    </button>
                  </div>
                </div>

                <div className="rounded border border-white/10 bg-black/30 p-3">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="flex items-center gap-2">
                      <label className="block text-xs text-gray-400">Net (Provider)</label>
                      {activeNetId && (
                        <span className="text-[10px] px-2 py-0.5 rounded bg-green-500/20 text-green-300">
                          Active
                        </span>
                      )}
                    </div>
                  </div>
                  <input
                    value={assignNetModel}
                    onChange={(e) => setAssignNetModel(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    placeholder="e.g., groq"
                    disabled={modelsBusy}
                    list="net-providers"
                  />
                  <datalist id="net-providers">
                    {(Array.isArray(modelsData?.net_providers) ? modelsData.net_providers : ["groq", "xai"]).map(
                      (provider: string) => (
                        <option key={provider} value={provider} />
                      )
                    )}
                  </datalist>
                  <div className="mt-2 flex items-center justify-between">
                    {activeNetId ? (
                      <div className="text-[11px] text-gray-500">Active: {activeNetId}</div>
                    ) : (
                      <span />
                    )}
                    <button
                      onClick={() => applyModeAssignment("net")}
                      disabled={modelsBusy || !assignNetModel}
                      className="px-3 py-1.5 text-xs rounded bg-green-600 hover:bg-green-500 text-white disabled:opacity-50"
                    >
                      Apply
                    </button>
                  </div>
                </div>
              </div>
              <div className="mt-3 text-[11px] text-gray-500">Apply updates per mode as needed.</div>
            </Card>

            <Card title="Download Model">
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="min-w-0 text-[11px] text-gray-500 leading-none truncate">
                  Auto-detects GGUF vs HF and stores correctly.
                </div>
                <button
                  onClick={loadModels}
                  disabled={modelsBusy}
                  className="rounded bg-gray-700 hover:bg-gray-600 px-2.5 py-1 text-[11px] leading-none text-white disabled:opacity-50 inline-flex items-center gap-2 whitespace-nowrap"
                >
                  <RefreshCw size={12} /> Refresh Registry
                </button>
              </div>
              <div className="mb-4 rounded border border-emerald-500/20 bg-emerald-500/10 p-3">
                <div className="text-xs uppercase tracking-wide text-emerald-300">Recommended Local Install</div>
                <div className="mt-2 text-sm text-white">{RECOMMENDED_LOCAL_MODEL.label}</div>
                <div className="mt-1 text-[11px] text-gray-300">{RECOMMENDED_LOCAL_MODEL.note}</div>
                <div className="mt-2 text-[11px] text-gray-500 break-all">
                  {RECOMMENDED_LOCAL_MODEL.repoId} · {RECOMMENDED_LOCAL_MODEL.ggufFilename}
                </div>
                <button
                  onClick={installRecommendedLocalModel}
                  disabled={modelsBusy}
                  className="mt-3 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded inline-flex items-center gap-2 disabled:opacity-50"
                >
                  <Download size={16} /> Install Recommended Model
                </button>
              </div>
              <label className="block text-xs text-gray-400 mb-2">Hugging Face repo_id</label>
              <input
                value={hfAutoRepoId}
                onChange={(e) => setHfAutoRepoId(e.target.value)}
                className="w-full bg-[#222] border border-white/10 rounded p-2 text-white mb-3"
                placeholder="meta-llama/Llama-2-7b-chat-hf"
              />
              <div className="space-y-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-2">model_id (optional)</label>
                  <input
                    value={hfAutoModelId}
                    onChange={(e) => setHfAutoModelId(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    placeholder="auto-generated if empty"
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-2">gguf_filename (optional)</label>
                  <input
                    value={hfAutoGgufFile}
                    onChange={(e) => setHfAutoGgufFile(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    placeholder="only if multiple .gguf"
                  />
                </div>
              </div>
              <button
                onClick={downloadHFAuto}
                disabled={modelsBusy || !hfAutoRepoId.trim()}
                className="mt-4 bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded inline-flex items-center gap-2 disabled:opacity-50"
              >
                <Download size={16} /> Download Model
              </button>
              <div className="mt-2 text-xs text-gray-500">Status: {downloadStatusLabel}</div>
            </Card>
          </div>

          <Card title="Backend Model Inventory">
            <div className="mb-4 text-xs text-gray-500">
              This list is discovered from local cache folders and runtime packages, so downloads from the
              frontend and command prompt stay in sync.
            </div>
            {modelInventory.length > 0 ? (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                {Object.entries(modelInventoryGroups).map(([group, items]) => (
                  <div key={group} className="rounded border border-white/10 bg-black/20 p-4">
                    <div className="mb-3 flex items-center justify-between gap-2">
                      <div className="text-sm text-white">{inventoryGroupLabels[group] || group}</div>
                      <div className="text-[11px] text-gray-500">{items.length} item(s)</div>
                    </div>
                    <div className="space-y-2 max-h-72 overflow-auto pr-1">
                      {items.map((item: any) => (
                        <div key={item.id} className="rounded border border-white/10 bg-black/30 p-3">
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0">
                              <div className="text-sm text-white truncate">{item.label || item.id}</div>
                              <div className="text-[11px] text-gray-500">
                                {item.component || item.kind || "component"}
                                {item.source ? ` · ${item.source}` : ""}
                              </div>
                            </div>
                            <span
                              className={`shrink-0 rounded px-2 py-0.5 text-[11px] ${
                                item.ready ? "bg-green-500/20 text-green-300" : "bg-yellow-500/20 text-yellow-300"
                              }`}
                            >
                              {item.ready ? "Ready" : "Missing"}
                            </span>
                          </div>
                          {item.repo_id && (
                            <div className="mt-2 text-[11px] text-gray-400 break-all">{item.repo_id}</div>
                          )}
                          {item.location && (
                            <div className="mt-1 text-[11px] text-gray-500 break-all">{item.location}</div>
                          )}
                          {item.note && <div className="mt-2 text-xs text-gray-400">{item.note}</div>}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-gray-500 italic">No local model assets found yet.</div>
            )}
          </Card>
        </div>
      )}
      {/* === TAB: RUNTIME === */}
      {activeTab === "runtime" && (
        <RuntimeOverview
          runtimeData={runtimeData}
          runtimeBusy={runtimeBusy}
          runtimeError={runtimeError}
          onRefresh={loadRuntime}
        />
      )}
      {activeTab === "users" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-1 space-y-6">
            <Card title="Create User">
              <div className="text-xs text-gray-400 mb-4">
                Add a new account and assign a role. Admins can manage access and reset passwords.
              </div>
              {usersError && (
                <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200 whitespace-pre-wrap">
                  {usersError}
                </div>
              )}
              <div className="grid grid-cols-1 gap-3">
                <div>
                  <label className="block text-xs text-gray-400 mb-2">Email</label>
                  <input
                    value={newUserEmail}
                    onChange={(e) => setNewUserEmail(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    placeholder="user@example.com"
                  />
                  {emailFormatError && (
                    <div className="mt-1 text-[11px] text-red-300">{emailFormatError}</div>
                  )}
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-2">Username</label>
                  <input
                    value={newUserUsername}
                    onChange={(e) => setNewUserUsername(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    placeholder="username"
                  />
                  {usernameFormatError && (
                    <div className="mt-1 text-[11px] text-red-300">{usernameFormatError}</div>
                  )}
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-2">Password</label>
                  <input
                    value={newUserPassword}
                    onChange={(e) => setNewUserPassword(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    type="password"
                    placeholder="********"
                  />
                  {passwordFormatError && (
                    <div className="mt-1 text-[11px] text-red-300">{passwordFormatError}</div>
                  )}
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-2">Role</label>
                  <select
                    value={newUserRole}
                    onChange={(e) => setNewUserRole(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                  >
                    <option value="user">user</option>
                    <option value="developer">developer</option>
                    <option value="admin">admin</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-2">Postgres database (optional)</label>
                  <input
                    value={newUserPgDatabase}
                    onChange={(e) => setNewUserPgDatabase(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    placeholder={suggestedDbName}
                  />
                  <div className="mt-1 text-[11px] text-gray-500">Auto: {suggestedDbName}</div>
                  {pgDbFormatError && (
                    <div className="mt-1 text-[11px] text-red-300">{pgDbFormatError}</div>
                  )}
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-2">MinIO bucket (optional)</label>
                  <input
                    value={newUserMinioBucket}
                    onChange={(e) => setNewUserMinioBucket(e.target.value)}
                    className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                    placeholder={suggestedBucketName}
                  />
                  <div className="mt-1 text-[11px] text-gray-500">Auto: {suggestedBucketName}</div>
                  {minioBucketFormatError && (
                    <div className="mt-1 text-[11px] text-red-300">{minioBucketFormatError}</div>
                  )}
                </div>
              </div>
              <button
                onClick={createUser}
                disabled={
                  usersBusy ||
                  !newUserEmail ||
                  !newUserUsername ||
                  !newUserPassword ||
                  Boolean(usernameFormatError) ||
                  Boolean(emailFormatError) ||
                  Boolean(passwordFormatError) ||
                  Boolean(pgDbFormatError) ||
                  Boolean(minioBucketFormatError)
                }
                className="mt-4 w-full bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded disabled:opacity-50"
              >
                Create User
              </button>
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card title="User Management">
              {!usersData ? (
                <div className="text-gray-500 italic">Loading users...</div>
              ) : (
                <div className="space-y-4">
                  {(usersData.users || []).map((u: any) => {
                    const pwdValue = userPasswordDrafts[u.username] || "";
                    const roleValue = userRoleDrafts[u.username] ?? (u.role || "user");
                    const roleDirty = roleValue !== (u.role || "user");
                    const statusClass = u.disabled
                      ? "bg-red-500/20 text-red-300"
                      : "bg-green-500/20 text-green-300";
                    return (
                      <div key={u.username} className="rounded-lg border border-white/10 bg-black/30 p-4">
                        <div className="flex items-start justify-between gap-4">
                          <div className="flex items-start gap-3 min-w-0">
                            <div className="h-10 w-10 rounded-full bg-blue-500/20 text-blue-200 flex items-center justify-center text-sm font-semibold">
                              {(u.username || "U").slice(0, 1).toUpperCase()}
                            </div>
                            <div className="min-w-0">
                              <div className="text-sm font-semibold text-white truncate">{u.username}</div>
                              <div className="text-xs text-gray-400 truncate">{u.email}</div>
                              <div className="mt-2 inline-flex items-center gap-2 text-[11px] text-gray-400">
                                <span className="uppercase tracking-wide">Role</span>
                                <span className="px-2 py-0.5 rounded bg-white/5 text-gray-200">
                                  {u.role || "user"}
                                </span>
                              </div>
                            </div>
                          </div>
                          <div className={`text-xs px-2 py-1 rounded ${statusClass}`}>
                            {u.disabled ? "Disabled" : "Active"}
                          </div>
                        </div>

                        <div className="mt-4 grid grid-cols-1 lg:grid-cols-12 gap-3">
                          <div className="lg:col-span-5">
                            <label className="block text-[11px] text-gray-400 mb-1">Reset Password</label>
                            <div className="flex gap-2">
                              <input
                                value={pwdValue}
                                onChange={(e) =>
                                  setUserPasswordDrafts((prev) => ({ ...prev, [u.username]: e.target.value }))
                                }
                                className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                                type="password"
                                placeholder="New password"
                              />
                              <button
                                onClick={() => resetUserPassword(u.username, pwdValue)}
                                disabled={usersBusy || !pwdValue}
                                className="bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded disabled:opacity-50"
                              >
                                Reset
                              </button>
                            </div>
                          </div>
                          <div className="lg:col-span-4">
                            <label className="block text-[11px] text-gray-400 mb-1">Role</label>
                            <div className="flex gap-2">
                              <select
                                value={roleValue}
                                onChange={(e) =>
                                  setUserRoleDrafts((prev) => ({ ...prev, [u.username]: e.target.value }))
                                }
                                className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                                disabled={usersBusy}
                              >
                                <option value="user">user</option>
                                <option value="developer">developer</option>
                                <option value="admin">admin</option>
                              </select>
                              <button
                                onClick={() => updateUserRole(u.username, roleValue)}
                                disabled={usersBusy || !roleDirty}
                                className="bg-blue-600 hover:bg-blue-500 text-white px-3 py-2 rounded disabled:opacity-50"
                              >
                                Update
                              </button>
                            </div>
                          </div>
                          <div className="lg:col-span-3">
                            <label className="block text-[11px] text-gray-400 mb-1">Actions</label>
                            <div className="flex gap-2">
                              <button
                                onClick={() => setUserDisabled(u.username, !u.disabled)}
                                disabled={usersBusy}
                                className={`px-3 py-2 rounded ${u.disabled ? "bg-green-600 hover:bg-green-500" : "bg-red-600 hover:bg-red-500"} text-white disabled:opacity-50`}
                              >
                                {u.disabled ? "Enable" : "Disable"}
                              </button>
                              <button
                                onClick={() => setDeleteUserTarget({ username: u.username, email: u.email })}
                                disabled={usersBusy}
                                className="bg-red-700 hover:bg-red-600 text-white px-3 py-2 rounded inline-flex items-center justify-center gap-2 disabled:opacity-50"
                              >
                                <Trash2 size={14} /> Delete
                              </button>
                            </div>
                          </div>
                        </div>

                        <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-3">
                          <div className="text-[11px] text-gray-400 mb-2 uppercase tracking-wide">Provisioned Resources</div>
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs text-gray-300">
                            <div className="rounded bg-white/5 px-2 py-1">
                              <span className="text-gray-400">Postgres:</span>{" "}
                              <span className="font-mono">
                                {u.resources?.postgres?.database || "—"}
                              </span>
                            </div>
                            <div className="rounded bg-white/5 px-2 py-1">
                              <span className="text-gray-400">MinIO:</span>{" "}
                              <span className="font-mono">
                                {u.resources?.minio?.bucket || "—"}
                              </span>
                            </div>
                            <div className="rounded bg-white/5 px-2 py-1">
                              <span className="text-gray-400">Redis:</span>{" "}
                              <span className="font-mono">
                                {u.resources?.redis?.namespace || "—"}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                  {(usersData.users || []).length === 0 && (
                    <div className="text-gray-500 italic text-sm">No users created yet.</div>
                  )}
                </div>
              )}
            </Card>
          </div>
        </div>
      )}

      <DeleteConfirmModal
        open={Boolean(deleteUserTarget)}
        title="Delete user?"
        description={
          deleteUserTarget
            ? `Delete ${deleteUserTarget.username}? This cannot be undone.`
            : "Delete this user? This cannot be undone."
        }
        onCancel={() => setDeleteUserTarget(null)}
        onConfirm={() => {
          if (!deleteUserTarget) return;
          const identifier = deleteUserTarget.username;
          setDeleteUserTarget(null);
          deleteUser(identifier);
        }}
      />

      {/* === TAB: DATABASES === */}
      {activeTab === "databases" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
          <div className="lg:col-span-1 space-y-6">
            <Card title="Databases" className="h-auto">
              {dbError && (
                <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200 whitespace-pre-wrap">
                  {dbError}
                </div>
              )}
              <label className="block text-xs text-gray-400 mb-2">Select Database</label>
              <select
                value={dbId}
                onChange={(e) => setDbId(e.target.value)}
                className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                disabled={dbBusy}
              >
                {dbList.map((db: any) => (
                  <option key={db.id} value={db.id}>
                    {db.label}
                  </option>
                ))}
              </select>
              <div className="mt-4 flex items-center justify-between text-xs text-gray-500">
                <span>Tables</span>
                <span>
                  {filteredDbTables.length}/{dbTables.length}
                </span>
              </div>
              <input
                value={dbTableQuery}
                onChange={(e) => setDbTableQuery(e.target.value)}
                className="mt-2 w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                placeholder="Filter tables..."
                disabled={dbBusy}
              />
              <div className="mt-2 space-y-1 max-h-72 overflow-auto rounded border border-white/10 bg-black/20 p-1">
                {filteredDbTables.map((t) => (
                  <button
                    key={t}
                    onClick={() => {
                      setDbTable(t);
                      setDbOffset(0);
                      loadRecords(dbId, t, dbLimit, 0);
                    }}
                    disabled={dbBusy}
                    className={`w-full text-left px-2 py-1 rounded text-sm ${
                      dbTable === t ? "bg-white/10 text-white" : "text-gray-300 hover:bg-white/5"
                    }`}
                  >
                    <span className="truncate block">{t}</span>
                  </button>
                ))}
                {filteredDbTables.length === 0 && (
                  <div className="text-gray-500 text-xs italic px-2 py-2">
                    {dbBusy
                      ? "Loading..."
                      : dbTables.length
                        ? "No tables match your filter."
                        : "No tables found."}
                  </div>
                )}
              </div>
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card title={`Records${dbTable ? `: ${dbTable}` : ""}`} className="h-auto">
              {!dbTable ? (
                <div className="text-gray-500 italic">Select a table to view records.</div>
              ) : (
                <>
                  <div className="flex flex-col gap-3 max-h-[70vh]">
                    <div className="flex items-center justify-between text-xs text-gray-400">
                      <div>Total: {dbTotal}</div>
                      <div className="flex items-center gap-2">
                        <span>Limit</span>
                        <input
                          value={dbLimit}
                          onChange={(e) => setDbLimit(Number(e.target.value || 25))}
                          className="w-20 bg-[#222] border border-white/10 rounded p-1 text-white"
                          type="number"
                          min={1}
                          max={200}
                        />
                        <button
                          onClick={() => loadRecords(dbId, dbTable, dbLimit, 0)}
                          disabled={dbBusy}
                          className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-white"
                        >
                          Reload
                        </button>
                      </div>
                    </div>
                    <div className="flex-1 min-h-0 overflow-auto border border-white/10 rounded">
                      <table className="min-w-full text-xs">
                        <thead className="bg-[#151515] sticky top-0 z-10">
                          <tr>
                            {dbColumns.map((c) => (
                              <th key={c} className="px-3 py-2 text-left text-gray-300">
                                {c}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-white/10">
                          {dbRows.map((row, idx) => (
                            <tr key={idx} className="hover:bg-white/5">
                              {dbColumns.map((c) => (
                                <td key={c} className="px-3 py-2 text-gray-200 align-top whitespace-pre-wrap">
                                  {String(row[c] ?? "")}
                                </td>
                              ))}
                            </tr>
                          ))}
                          {dbRows.length === 0 && (
                            <tr>
                              <td colSpan={dbColumns.length || 1} className="px-3 py-4 text-gray-500">
                                No records found.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                    <div className="flex items-center justify-between text-xs text-gray-400">
                      <button
                        onClick={() => {
                          const next = Math.max(0, dbOffset - dbLimit);
                          setDbOffset(next);
                          loadRecords(dbId, dbTable, dbLimit, next);
                        }}
                        disabled={dbOffset === 0 || dbBusy}
                        className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-white disabled:opacity-50"
                      >
                        Prev
                      </button>
                      <div>
                        Offset {dbOffset} of {dbTotal}
                      </div>
                      <button
                        onClick={() => {
                          const next = dbOffset + dbLimit;
                          setDbOffset(next);
                          loadRecords(dbId, dbTable, dbLimit, next);
                        }}
                        disabled={dbOffset + dbLimit >= dbTotal || dbBusy}
                        className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-white disabled:opacity-50"
                      >
                        Next
                      </button>
                    </div>
                  </div>
                </>
              )}
            </Card>
          </div>
        </div>
      )}

      {/* === TAB: DANGER ZONE === */}
      {activeTab === "danger" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8 items-start">
          <div className="lg:col-span-1 space-y-6">
            <Card title="Confirm" className="h-auto">
              <div className="text-sm text-gray-300">
                Destructive actions are disabled by default. Backend must have
                <span className="font-mono text-red-300"> CHAT_UI_ENABLE_DESTRUCTIVE_DEVTOOLS=1</span>.
              </div>
              <div className="mt-4">
                <label className="block text-xs text-gray-400 mb-2">Type confirm phrase</label>
                <input
                  value={resetConfirm}
                  onChange={(e) => setResetConfirm(e.target.value)}
                  className="w-full bg-[#222] border border-white/10 rounded p-2 text-white font-mono"
                  placeholder="DELETE_EVERYTHING"
                />
              </div>
              <div className="mt-4">
                <label className="block text-xs text-gray-400 mb-2">MinIO bucket (optional)</label>
                <input
                  value={resetBucket}
                  onChange={(e) => setResetBucket(e.target.value)}
                  className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
                  placeholder="chat-ui-documents"
                />
              </div>
              {resetError && (
                <div className="mt-4 rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200 whitespace-pre-wrap">
                  {resetError}
                </div>
              )}
            </Card>
          </div>

          <div className="lg:col-span-2 space-y-6">
            <Card title="Danger Zone" className="h-auto">
              <div className="text-sm text-gray-300 mb-4">
                These actions permanently delete data. Proceed only if you have backups.
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {[
                  {
                    label: "Wipe RAG DB",
                    desc: "Truncates pgvector tables",
                    action: () => runReset("/devtools/reset/rag"),
                  },
                  {
                    label: "Wipe Chat DB",
                    desc: "Clears chat sessions/messages",
                    action: () => runReset("/devtools/reset/chat"),
                  },
                  {
                    label: "Wipe Redis",
                    desc: "Deletes rag:* / abort:* keys",
                    action: () => runReset("/devtools/reset/redis"),
                  },
                  {
                    label: "Wipe MinIO Bucket",
                    desc: "Deletes all objects",
                    action: () => runReset("/devtools/reset/minio"),
                  },
                ].map((item) => (
                  <button
                    key={item.label}
                    disabled={resetBusy}
                    onClick={item.action}
                    className="rounded-lg border border-red-500/30 bg-red-500/10 hover:bg-red-500/15 px-4 py-3 text-left disabled:opacity-50"
                  >
                    <div className="flex items-center gap-2 text-red-200 font-medium">
                      <Trash2 size={16} /> {item.label}
                    </div>
                    <div className="text-xs text-gray-400 mt-1">{item.desc}</div>
                  </button>
                ))}
              </div>

              <button
                disabled={resetBusy}
                onClick={() => runReset("/devtools/reset/all")}
                className="mt-4 w-full rounded-lg border border-red-500/40 bg-red-600/20 hover:bg-red-600/25 px-4 py-3 text-left disabled:opacity-50"
              >
                <div className="flex items-center gap-2 text-red-100 font-semibold">
                  <Trash2 size={16} /> Wipe EVERYTHING (All)
                </div>
                <div className="text-xs text-gray-200/80 mt-1">RAG DB + Chat DB + Redis + MinIO</div>
              </button>

              {/* NUCLEAR RESET */}
              <div className="mt-6 rounded-xl border-2 border-orange-500/50 bg-orange-500/5 p-4">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-lg">☢️</span>
                  <span className="text-orange-200 font-bold text-sm">Nuclear Reset</span>
                  <span className="ml-auto rounded bg-orange-500/20 px-2 py-0.5 text-[11px] text-orange-300">One-Click Full Wipe</span>
                </div>
                <div className="text-xs text-gray-400 mb-3">
                  Wipes <strong className="text-white">everything</strong> in one shot — PostgreSQL (all tables), MinIO files, Redis keys, RabbitMQ queued tasks, backend tmp/job cache, and browser localStorage. Redirects to home after completion.
                </div>
                {nuclearResult && (
                  <pre className="mb-3 rounded border border-orange-500/20 bg-black/40 p-3 text-xs text-orange-200 whitespace-pre-wrap">{nuclearResult}</pre>
                )}
                {nuclearError && (
                  <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200">{nuclearError}</div>
                )}
                <button
                  id="nuclear-reset-btn"
                  disabled={nuclearBusy}
                  onClick={runNuclearReset}
                  className="w-full rounded-lg border border-orange-500/60 bg-orange-600/30 hover:bg-orange-600/50 px-4 py-3 text-center font-bold text-orange-100 disabled:opacity-50 transition-all duration-150 active:scale-95"
                >
                  {nuclearBusy ? "☢️ Wiping everything..." : "☢️ NUCLEAR RESET — Wipe All Data & Reload"}
                </button>
              </div>
            </Card>

            <Card title="Result" className="h-auto">
              {resetResult ? (
                <pre className="text-xs text-gray-200 overflow-auto bg-black/40 p-4 rounded border border-white/10 max-h-64">
                  {JSON.stringify(resetResult, null, 2)}
                </pre>
              ) : (
                <div className="text-gray-500 italic">Run a reset to see results.</div>
              )}
            </Card>
          </div>
        </div>
      )}

      {/* === TAB: INTENT === */}
      {activeTab === "intent" && (
        <div className="grid grid-cols-2 gap-8">
          <Card title="Input">
            <label className="block text-sm text-gray-400 mb-2">User Query</label>
            <input 
              value={intentInput}
              onChange={(e) => setIntentInput(e.target.value)}
              className="w-full bg-[#222] border border-white/10 rounded p-2 text-white"
              placeholder="e.g., Hello there"
            />
            <button onClick={testIntent} className="mt-4 bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded flex items-center gap-2">
              <Play size={16} /> Test Intent
            </button>
          </Card>

          <Card title="Result">
            {intentResult ? (
              <pre className="text-sm text-green-400 overflow-auto bg-black p-4 rounded border border-white/10">
                {JSON.stringify(intentResult, null, 2)}
              </pre>
            ) : (
              <div className="text-gray-500 italic">Run a test to see results...</div>
            )}
          </Card>
        </div>
      )}

      {/* === TAB: REWRITE === */}
      {activeTab === "rewrite" && (
        <div className="grid grid-cols-2 gap-8">
          <Card title="Input Context">
            <label className="block text-sm text-gray-400 mb-2">Current Question</label>
            <input 
              value={rewriteInput}
              onChange={(e) => setRewriteInput(e.target.value)}
              className="w-full bg-[#222] border border-white/10 rounded p-2 text-white mb-4"
              placeholder="e.g., Tell me more about it"
            />
            
            <label className="block text-sm text-gray-400 mb-2">Chat History (One message per line)</label>
            <textarea 
              value={rewriteHistory}
              onChange={(e) => setRewriteHistory(e.target.value)}
              className="w-full bg-[#222] border border-white/10 rounded p-2 text-white h-32"
              placeholder="User: What is the pressure?&#10;Assistant: 500 psi."
            />

            <button onClick={testRewrite} className="mt-4 bg-purple-600 hover:bg-purple-500 text-white px-4 py-2 rounded flex items-center gap-2">
              <Play size={16} /> Test Rewriter
            </button>
          </Card>

          <Card title="Result">
            {rewriteResult ? (
              <div className="space-y-4">
                <div>
                  <div className="text-xs text-gray-500 uppercase">Original</div>
                  <div className="text-gray-300">{rewriteResult.original}</div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 uppercase">Rewritten For RAG</div>
                  <div className="text-xl font-mono text-purple-400">{rewriteResult.rewritten}</div>
                </div>
              </div>
            ) : (
              <div className="text-gray-500 italic">Run a test to see results...</div>
            )}
          </Card>
        </div>
      )}

      {/* === TAB: HEALTH === */}
      {activeTab === "health" && (
        <SystemHealthCheck />
      )}

    </div>
  );
}

// --- Subcomponents ---

function TabButton({ active, onClick, label }: any) {
  return (
    <button 
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium transition-colors whitespace-nowrap ${
        active ? "text-white border-b-2 border-blue-500" : "text-gray-400 hover:text-white"
      }`}
    >
      {label}
    </button>
  );
}

function Card({ title, children, className = "", titleClassName = "" }: any) {
  return (
    <div
      className={`rounded-xl border border-white/10 bg-gradient-to-b from-[#141414] to-[#0b0b0b] p-6 shadow-[0_10px_30px_rgba(0,0,0,0.35)] ${className}`.trim()}
    >
      <h3 className={`text-lg font-medium text-white mb-4 ${titleClassName}`.trim()}>{title}</h3>
      {children}
    </div>
  );
}

function Toggle({
  label,
  description,
  value,
  onChange,
  disabled,
}: {
  label: string;
  description?: string;
  value: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!value)}
      className="w-full flex items-start justify-between gap-4 rounded border border-white/10 bg-black/30 px-3 py-3 text-left disabled:opacity-50"
    >
      <span className="min-w-0">
        <span className="block text-sm text-gray-200">{label}</span>
        {description && <span className="mt-1 block text-xs text-gray-500">{description}</span>}
      </span>
      <span
        className={`mt-0.5 h-5 w-10 shrink-0 rounded-full border transition-colors ${
          value ? "bg-green-600/70 border-green-500/40" : "bg-white/10 border-white/20"
        }`}
      >
        <span
          className={`block h-4 w-4 mt-0.5 rounded-full bg-white transition-transform ${
            value ? "translate-x-5" : "translate-x-1"
          }`}
        />
      </span>
    </button>
  );
}

function SystemHealthCheck() {
  const [status, setStatus] = useState<any>(null);
  
  const check = async () => {
    const res = await fetch(`${API_BASE}/health`, { credentials: "include" });
    setStatus(await res.json());
  }

  return (
    <Card title="Backend Status">
        <button onClick={check} className="mb-4 bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded text-sm text-white">Refresh Status</button>
        {status ? (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatusItem label="PostgreSQL" status={status.services?.postgres} />
                <StatusItem label="Redis" status={status.services?.redis} />
                <StatusItem label="MinIO" status={status.services?.minio} />
                <StatusItem label="RabbitMQ" status={status.services?.rabbitmq} />
            </div>
        ) : (
            <div className="text-gray-500">Click refresh to check connections.</div>
        )}
    </Card>
  )
}

function StatusItem({ label, status }: any) {
    const isOk = status === "ok";
    return (
        <div className={`p-4 rounded border ${isOk ? "border-green-500/30 bg-green-500/10" : "border-red-500/30 bg-red-500/10"}`}>
            <div className="text-xs text-gray-400 uppercase">{label}</div>
            <div className={`text-lg font-bold flex items-center gap-2 ${isOk ? "text-green-400" : "text-red-400"}`}>
                {isOk ? <CheckCircle size={18}/> : <AlertTriangle size={18}/>}
                {status?.toUpperCase()}
            </div>
        </div>
    )
}
