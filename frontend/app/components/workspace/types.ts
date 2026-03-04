export type HealthState = "online" | "degraded" | "offline" | "unknown";

export type DocumentIndexStatus = "Indexed" | "Processing" | "Failed";

export interface WorkspaceDocumentRow {
  id: string;
  projectId: string;
  projectName: string;
  documentId: string;
  name: string;
  revision: string;
  chunks: number;
  status: DocumentIndexStatus;
  lastUpdated: number;
}

export interface WorkspaceProjectSummary {
  id: string;
  name: string;
  description: string;
  lastUpdated: number;
  currentRevision: string;
  totalDocuments: number;
  documents: WorkspaceDocumentRow[];
}

export interface WorkspaceJobSummary {
  running: number;
  completed: number;
  failed: number;
  statuses: Record<string, string>;
}

export interface WorkspaceSystemHealth {
  backendApi: HealthState;
  ingestionAgent: HealthState;
  vectorDb: HealthState;
  modelLoaded: HealthState;
  embeddingModelLoaded: HealthState;
}

export interface WorkspaceStatusStripData {
  project: string;
  revision: string;
  embeddings: "ready" | "indexing" | "failed" | "idle";
  agent: "online" | "offline" | "degraded";
  activeJobs: number;
  vectorDb: "healthy" | "degraded" | "disconnected";
}

export interface WorkspaceTelemetry {
  strip: WorkspaceStatusStripData;
  system: WorkspaceSystemHealth;
  jobs: WorkspaceJobSummary;
  activeDocument: {
    companyDocumentId?: string;
    revisionNumber?: string;
    filename?: string;
  } | null;
  ingestionStatus: string;
  lastUpdated: number;
  error?: string | null;
}

