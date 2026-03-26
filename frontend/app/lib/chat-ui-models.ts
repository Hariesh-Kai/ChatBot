/**
 * chat-ui-models.ts
 *
 * Central model registry for Chat UI frontend.
 *
 * RULES:
 * - IDs MUST match backend ChatRequest.mode exactly
 * - Registry is IMMUTABLE
 * - Availability / rate-limit is RUNTIME STATE (external)
 * - UI must NEVER mutate model definitions
 */

export type ChatUIModelId = "lite" | "base" | "net";

/* =========================================================
   MODEL DEFINITIONS (IMMUTABLE)
========================================================= */

export interface ChatUIModel {
  id: ChatUIModelId;
  label: string;
  description?: string;
  requiresNet?: boolean;
  requiresGpu?: boolean;
}

/**
 * 🔒 STATIC REGISTRY (NEVER MUTATE)
 */
const _CHAT_UI_MODELS: Record<ChatUIModelId, ChatUIModel> = {
  lite: {
    id: "lite",
    label: "Chat UI Lite v1.0",
    description: "Fast local model (CPU / GGUF)",
  },

  base: {
    id: "base",
    label: "Chat UI Base v1.0",
    description: "Higher-quality local base model (GPU preferred)",
    requiresGpu: true,
  },

  net: {
    id: "net",
    label: "Chat UI Net v1.0",
    description: "External LLM (Groq / xAI)",
    requiresNet: true,
  },
};

/**
 *  Public immutable registry
 */
export const CHAT_UI_MODELS = Object.freeze({ ..._CHAT_UI_MODELS });

/* =========================================================
   RUNTIME AVAILABILITY STATE (EXTERNAL)
========================================================= */

type NetRuntimeState = {
  enabled: boolean;
  rateLimited: boolean;
  retryAfterSec?: number;
  provider?: string | null;
};

let _netState: NetRuntimeState = {
  enabled: false,
  rateLimited: false,
};

/**
 * Apply backend /net/status result.
 * MUST be called by UI layer after polling.
 */
export function applyNetStatus(status?: {
  enabled?: boolean;
  rateLimited?: boolean;
  retryAfterSec?: number;
  provider?: string | null;
}): void {
  _netState = {
    enabled: Boolean(status?.enabled),
    rateLimited: Boolean(status?.rateLimited),
    retryAfterSec: status?.retryAfterSec,
    provider: status?.provider ?? null,
  };
}

/**
 * Read-only Net runtime state
 */
export function getNetState(): NetRuntimeState {
  return { ..._netState };
}

/**
 * Convenience: whether Net is enabled at runtime.
 */
export function isNetEnabled(): boolean {
  return Boolean(_netState.enabled);
}

/* =========================================================
   UI-SAFE DERIVED MODEL STATE
========================================================= */

export interface ModelUIState {
  model: ChatUIModel;
  available: boolean;
  disabledReason?: string;
}

/**
 * Models with availability + reason (for selector UI)
 */
export function getModelUIStates(): ModelUIState[] {
  return Object.values(CHAT_UI_MODELS).map((model) => {
    if (model.id !== "net") {
      return {
        model,
        available: true,
      };
    }

    if (!_netState.enabled) {
      return {
        model,
        available: false,
        disabledReason: "Net unavailable",
      };
    }

    if (_netState.rateLimited) {
      return {
        model,
        available: false,
        disabledReason: _netState.retryAfterSec
          ? `Rate limited (${_netState.retryAfterSec}s)`
          : "Rate limited",
      };
    }

    return {
      model,
      available: true,
    };
  });
}

/* =========================================================
   SAFE HELPERS
========================================================= */

/**
 * Visible models (hide Net if never enabled)
 */
export function getVisibleModels(): ChatUIModel[] {
  return Object.values(CHAT_UI_MODELS).filter((m) => {
    if (m.id === "net") {
      return _netState.enabled;
    }
    return true;
  });
}

/**
 * Safely resolve a model ID.
 * Never returns invalid value.
 */
export function resolveModelId(id?: unknown): ChatUIModelId {
  if (id === "lite" || id === "base" || id === "net") {
    return id;
  }
  return "lite";
}

/**
 * Safe getter for a model definition.
 */
export function getModelById(id?: unknown): ChatUIModel {
  return CHAT_UI_MODELS[resolveModelId(id)];
}

/* =========================================================
   SMART AUTO-ROUTING (PHASE 9)
========================================================= */

/**
 * Decide best model automatically.
 * UI may override manually.
 */
export function autoSelectModel(opts: {
  text: string;
  hasDocuments: boolean;
  preferred?: ChatUIModelId;
}): ChatUIModelId {
  const { text, hasDocuments, preferred } = opts;

  // Explicit preference (if still allowed)
  if (preferred === "net") {
    if (_netState.enabled && !_netState.rateLimited) {
      return "net";
    }
    return "base";
  }

  if (hasDocuments) return "base";

  if (text.trim().split(/\s+/).length <= 6) {
    return "lite";
  }

  return "base";
}
