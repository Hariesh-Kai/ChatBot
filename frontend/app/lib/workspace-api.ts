import { API_BASE } from "@/app/lib/config";
import {
  fetchNetStatus,
  fetchUploadIngestionStatus,
  type UploadIngestionStatusResponse,
} from "@/app/lib/api";
import type { WorkspaceTelemetry, HealthState } from "@/app/components/workspace/types";

interface HealthResponse {
  status?: string;
  services?: Record<string, string>;
}

interface JobsResponse {
  active_jobs?: number;
  statuses?: Record<string, string>;
}

interface PmlStatusResponse {
  ok?: boolean;
  configured?: boolean;
}

function mapHealthFromService(value?: string): HealthState {
  const text = (value || "").trim().toLowerCase();
  if (!text) return "unknown";
  if (text.startsWith("ok")) return "online";
  if (text.startsWith("error")) return "offline";
  if (text.includes("disabled") || text.includes("pending")) return "degraded";
  return "degraded";
}

function mapEmbeddingsState(status?: string): "ready" | "indexing" | "failed" | "idle" {
  const value = (status || "").toUpperCase();
  if (value === "READY") return "ready";
  if (value === "PROCESSING" || value === "WAIT_FOR_METADATA") return "indexing";
  if (value === "ERROR") return "failed";
  return "idle";
}

function mapAgentState(
  outboxState: HealthState,
  rabbitState: HealthState
): "online" | "offline" | "degraded" {
  if (outboxState === "offline" || rabbitState === "offline") return "offline";
  if (outboxState === "degraded" || rabbitState === "degraded") return "degraded";
  return "online";
}

function mapVectorState(state: HealthState): "healthy" | "degraded" | "disconnected" {
  if (state === "online") return "healthy";
  if (state === "offline") return "disconnected";
  return "degraded";
}

function summarizeJobs(statuses: Record<string, string>) {
  let running = 0;
  let completed = 0;
  let failed = 0;

  for (const raw of Object.values(statuses)) {
    const status = String(raw || "").toUpperCase();
    if (status === "READY") {
      completed += 1;
      continue;
    }
    if (status === "ERROR") {
      failed += 1;
      continue;
    }
    if (status === "PROCESSING" || status === "WAIT_FOR_METADATA") {
      running += 1;
    }
  }

  return { running, completed, failed };
}

async function fetchHealth(): Promise<HealthResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as HealthResponse;
  } catch {
    return null;
  }
}

async function fetchJobs(): Promise<JobsResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/devtools/jobs`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as JobsResponse;
  } catch {
    return null;
  }
}

async function fetchPmlStatus(): Promise<PmlStatusResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/pml-chat/status`, {
      credentials: "include",
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as PmlStatusResponse;
  } catch {
    return null;
  }
}

export async function fetchWorkspaceTelemetry(params: {
  activeSessionId?: string | null;
  activeProject?: string;
  activeRevision?: string;
  fallbackActiveJobs?: number;
}): Promise<WorkspaceTelemetry> {
  const { activeSessionId, activeProject, activeRevision, fallbackActiveJobs = 0 } = params;

  const [healthResult, jobsResult, netResult, pmlResult, uploadResult] =
    await Promise.allSettled([
      fetchHealth(),
      fetchJobs(),
      fetchNetStatus(),
      fetchPmlStatus(),
      activeSessionId
        ? fetchUploadIngestionStatus({ sessionId: activeSessionId })
        : Promise.resolve(null),
    ]);

  const health =
    healthResult.status === "fulfilled" ? (healthResult.value as HealthResponse | null) : null;
  const jobs = jobsResult.status === "fulfilled" ? (jobsResult.value as JobsResponse | null) : null;
  const net = netResult.status === "fulfilled" ? netResult.value : null;
  const pml = pmlResult.status === "fulfilled" ? (pmlResult.value as PmlStatusResponse | null) : null;
  const upload =
    uploadResult.status === "fulfilled"
      ? (uploadResult.value as UploadIngestionStatusResponse | null)
      : null;

  const postgres = mapHealthFromService(health?.services?.postgres);
  const outbox = mapHealthFromService(health?.services?.minio_outbox);
  const rabbit = mapHealthFromService(health?.services?.rabbitmq);
  const backendApi: HealthState =
    health?.status === "ok" ? "online" : health ? "degraded" : "unknown";

  const ingestionAgent = mapAgentState(outbox, rabbit);
  const vectorDb = postgres;
  const netReady = Boolean(net?.ok && net?.enabled);
  const pmlReady = Boolean(pml?.ok && pml?.configured);
  const modelLoaded: HealthState = netReady || pmlReady ? "online" : "degraded";
  const embeddingModelLoaded: HealthState = vectorDb === "online" ? "online" : vectorDb;

  const jobStatuses = jobs?.statuses || {};
  const jobCounts = summarizeJobs(jobStatuses);
  const activeJobs = Math.max(
    jobs?.active_jobs ?? 0,
    jobCounts.running,
    Math.max(0, fallbackActiveJobs)
  );

  const embeddings = mapEmbeddingsState(upload?.status);
  const selectedRevision =
    activeRevision ||
    String(upload?.active_document?.revision_number || "").trim() ||
    "R-";
  const selectedProject =
    activeProject ||
    String(upload?.active_document?.company_document_id || "").trim() ||
    "No active project";

  let error: string | null = null;
  if (!health && healthResult.status === "rejected") {
    error = "Health endpoint unavailable";
  }

  return {
    strip: {
      project: selectedProject,
      revision: selectedRevision,
      embeddings,
      agent: ingestionAgent,
      activeJobs,
      vectorDb: mapVectorState(vectorDb),
    },
    system: {
      backendApi,
      ingestionAgent,
      vectorDb,
      modelLoaded,
      embeddingModelLoaded,
    },
    jobs: {
      running: jobCounts.running,
      completed: jobCounts.completed,
      failed: jobCounts.failed,
      statuses: jobStatuses,
    },
    activeDocument: upload?.active_document
      ? {
          companyDocumentId: upload.active_document.company_document_id,
          revisionNumber: upload.active_document.revision_number,
          filename: upload.active_document.filename,
        }
      : null,
    ingestionStatus: upload?.status || "UNKNOWN",
    lastUpdated: Date.now(),
    error,
  };
}
