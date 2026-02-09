"use client";

import type { ReactNode } from "react";

type ServiceStatus = "pending" | "ok" | "degraded" | "error";

interface ServiceItem {
  label: string;
  status: ServiceStatus;
}

interface Props {
  title: string;
  subtitle?: string;
  services: ServiceItem[];
  icon?: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
}

function statusPill(status: ServiceStatus) {
  switch (status) {
    case "ok":
      return "bg-green-500/20 text-green-300 border-green-500/30";
    case "degraded":
      return "bg-yellow-500/20 text-yellow-300 border-yellow-500/30";
    case "error":
      return "bg-red-500/20 text-red-300 border-red-500/30";
    default:
      return "bg-white/10 text-gray-300 border-white/10";
  }
}

export default function StartupLoader({
  title,
  subtitle = "Starting services. This usually takes a few seconds.",
  services,
  icon,
  actionLabel,
  onAction,
}: Props) {
  return (
    <div className="min-h-screen bg-black text-white flex items-center justify-center p-6">
      <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-[#0b0b0b] p-6 shadow-xl">
        <div className="flex items-center gap-3">
          {icon ? (
            <div className="h-9 w-9 rounded-full bg-white/10 flex items-center justify-center">
              {icon}
            </div>
          ) : (
            <div className="h-9 w-9 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
          )}
          <div>
            <div className="text-lg font-semibold">{title}</div>
            <div className="text-sm text-gray-400">{subtitle}</div>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 sm:grid-cols-2 gap-3">
          {services.map((service) => (
            <div
              key={service.label}
              className="flex items-center justify-between rounded-lg border border-white/10 bg-black/40 px-3 py-2"
            >
              <div className="text-sm text-gray-300">{service.label}</div>
              <div className={`text-[11px] px-2 py-0.5 rounded border ${statusPill(service.status)}`}>
                {service.status === "pending" ? "Starting" : service.status.toUpperCase()}
              </div>
            </div>
          ))}
        </div>

        {actionLabel && onAction && (
          <button
            onClick={onAction}
            className="mt-5 inline-flex items-center justify-center rounded-md bg-white px-4 py-2 text-sm font-medium text-black hover:bg-gray-200"
          >
            {actionLabel}
          </button>
        )}
      </div>
    </div>
  );
}
