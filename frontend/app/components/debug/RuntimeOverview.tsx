"use client";

import { RefreshCw } from "lucide-react";
import { type ReactNode } from "react";

type RuntimeOverviewProps = {
  runtimeData: any;
  runtimeBusy: boolean;
  runtimeError: string | null;
  onRefresh: () => void;
};

function Panel({
  title,
  children,
  className = "",
}: {
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-white/10 bg-gradient-to-b from-[#141414] to-[#0b0b0b] p-6 shadow-[0_10px_30px_rgba(0,0,0,0.35)] ${className}`.trim()}
    >
      <h3 className="text-lg font-medium text-white mb-4">{title}</h3>
      {children}
    </div>
  );
}

export default function RuntimeOverview({
  runtimeData,
  runtimeBusy,
  runtimeError,
  onRefresh,
}: RuntimeOverviewProps) {
  const gpu = runtimeData?.gpu || {};
  const rabbitmq = runtimeData?.rabbitmq || {};
  const workers = runtimeData?.workers || {};
  const software = runtimeData?.software || {};
  const softwareFunctions = Array.isArray(software?.functions) ? software.functions : [];
  const softwarePackages = software?.packages || {};
  const softwareQueue = software?.queue || {};

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm text-gray-400">
          Runtime, broker, worker, and package visibility.
        </div>
        <button
          onClick={onRefresh}
          disabled={runtimeBusy}
          className="inline-flex items-center gap-2 rounded bg-gray-800 hover:bg-gray-700 text-white px-3 py-2 text-sm disabled:opacity-50"
        >
          <RefreshCw size={14} className={runtimeBusy ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {runtimeError && (
        <div className="rounded border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-200 whitespace-pre-wrap">
          {runtimeError}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Panel title="Runtime Overview">
          <div className="space-y-2 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-400">LLM Device</span>
              <span className="text-gray-200 font-mono">{gpu?.llm_device || "unknown"}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-400">GPU</span>
              <span className="text-gray-200">
                {gpu?.available
                  ? `${gpu?.count || 0} detected`
                  : "not detected (CPU mode)"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-400">Auto GPU connect</span>
              <span className="text-gray-200">
                {gpu?.auto_selected ? "enabled" : "disabled / fallback"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-400">RabbitMQ</span>
              <span className="text-gray-200">{rabbitmq?.status || "unknown"}</span>
            </div>
            <div className="text-xs text-gray-500 break-all">
              {rabbitmq?.broker_url || "Broker URL not configured"}
            </div>
            {rabbitmq?.error && (
              <div className="text-xs text-red-300 whitespace-pre-wrap">{String(rabbitmq.error)}</div>
            )}
          </div>
        </Panel>

        <Panel title="Queue + Workers">
          <div className="space-y-2 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-400">Celery mode</span>
              <span className="text-gray-200">
                {workers?.celery_enabled ? "enabled" : "disabled"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-400">Outbox via Celery</span>
              <span className="text-gray-200">
                {workers?.outbox_via_celery ? "yes" : "no"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-gray-400">Active commit jobs</span>
              <span className="text-gray-200">{workers?.active_commit_jobs ?? 0}</span>
            </div>
            <div className="rounded border border-white/10 bg-black/20 px-3 py-2">
              <div className="text-xs text-gray-400 mb-1">Outbox counts</div>
              <div className="text-xs text-gray-300">
                pending: {workers?.outbox_counts?.pending ?? 0}
              </div>
              <div className="text-xs text-gray-300">
                uploading: {workers?.outbox_counts?.uploading ?? 0}
              </div>
              <div className="text-xs text-gray-300">
                failed: {workers?.outbox_counts?.failed ?? 0}
              </div>
            </div>
            {workers?.error && (
              <div className="text-xs text-red-300 whitespace-pre-wrap">{String(workers.error)}</div>
            )}
          </div>
        </Panel>
      </div>

      <Panel title="Software Used + Functions">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="rounded border border-white/10 bg-black/30 p-3">
            <div className="text-xs text-gray-400 mb-2">Function Modules</div>
            <div className="space-y-2">
              {softwareFunctions.map((fn: any) => (
                <div
                  key={String(fn?.id || fn?.name || "function-item")}
                  className="rounded border border-white/10 bg-black/20 px-3 py-2"
                >
                  <div className="text-sm text-white">{fn?.name || "Unknown function"}</div>
                  <div className="text-xs text-gray-400 mt-0.5">
                    Software: {fn?.software || "N/A"}
                  </div>
                  {fn?.task_name && (
                    <div className="text-xs text-gray-500 mt-0.5 font-mono">
                      Task: {String(fn.task_name)}
                    </div>
                  )}
                  {fn?.description && (
                    <div className="text-xs text-gray-500 mt-1">{String(fn.description)}</div>
                  )}
                </div>
              ))}
              {softwareFunctions.length === 0 && (
                <div className="text-xs text-gray-500 italic">No runtime function details available.</div>
              )}
            </div>
          </div>

          <div className="rounded border border-white/10 bg-black/30 p-3">
            <div className="text-xs text-gray-400 mb-2">Software Packages</div>
            <div className="space-y-1 text-sm">
              {Object.entries(softwarePackages).map(([name, ver]) => (
                <div key={name} className="flex items-center justify-between gap-3">
                  <span className="text-gray-300">{name}</span>
                  <span className="text-gray-500 font-mono">{String(ver || "not installed")}</span>
                </div>
              ))}
              {Object.keys(softwarePackages).length === 0 && (
                <div className="text-xs text-gray-500 italic">No package info available.</div>
              )}
            </div>
            <div className="mt-3 rounded border border-white/10 bg-black/20 px-3 py-2">
              <div className="text-xs text-gray-400 mb-1">Queue Configuration</div>
              <div className="text-xs text-gray-300">
                Celery: {softwareQueue?.celery_enabled ? "enabled" : "disabled"}
              </div>
              <div className="text-xs text-gray-300">
                Outbox via Celery: {softwareQueue?.outbox_via_celery ? "yes" : "no"}
              </div>
              <div className="text-xs text-gray-500 font-mono">
                Default queue: {String(softwareQueue?.default_queue || "kavin.default")}
              </div>
              <div className="text-xs text-gray-500">
                Broker source: {String(softwareQueue?.broker_source || "not configured")}
              </div>
            </div>
          </div>
        </div>
      </Panel>
    </div>
  );
}
