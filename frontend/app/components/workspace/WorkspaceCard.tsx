"use client";

import type { ReactNode } from "react";

export default function WorkspaceCard({
  title,
  subtitle,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-[14px] border border-white/10 bg-[#1f2937] p-6 shadow-[0_4px_20px_rgba(0,0,0,0.25)] transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_10px_24px_rgba(0,0,0,0.32)] ${className}`}
    >
      <div className="mb-4 flex items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold tracking-tight text-white">{title}</h3>
          {subtitle && <p className="mt-1 text-xs text-slate-400">{subtitle}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}
