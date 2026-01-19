// frontend/components/chat/ProcessingBubble.tsx
"use client";

import { Loader2, Check, FileText, Server, Database, BrainCircuit } from "lucide-react";

interface ProcessingBubbleProps {
  stepName: string; // e.g. "Chunking document..."
  progress: number; // 0 to 100
  isDone?: boolean;
}

export default function ProcessingBubble({ 
  stepName, 
  progress, 
  isDone = false 
}: ProcessingBubbleProps) {
  
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (progress / 100) * circumference;

  // Dynamic Icon based on the current step text
  const getIcon = () => {
    const lower = stepName.toLowerCase();
    if (isDone) return <Check size={18} className="text-green-500" />;
    if (lower.includes("upload") || lower.includes("back")) return <Server size={18} className="text-blue-400" />;
    if (lower.includes("chunk") || lower.includes("analyz")) return <FileText size={18} className="text-purple-400" />;
    if (lower.includes("index") || lower.includes("embed")) return <Database size={18} className="text-orange-400" />;
    return <BrainCircuit size={18} className="text-gray-400" />;
  };

  if (isDone) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-white/10 bg-[#111] px-4 py-3 animate-in fade-in zoom-in-95 duration-300">
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-green-500/10 text-green-500">
          <Check size={16} />
        </div>
        <span className="text-sm font-medium text-gray-200">Processing Complete</span>
      </div>
    );
  }

  return (
    <div className="flex w-full max-w-sm items-center gap-4 rounded-xl border border-white/10 bg-[#0a0a0a] px-5 py-4 shadow-sm transition-all">
      <div className="relative flex h-10 w-10 items-center justify-center">
        {/* Background Ring */}
        <svg className="absolute h-full w-full rotate-[-90deg]" viewBox="0 0 44 44">
          <circle cx="22" cy="22" r={radius} fill="none" stroke="currentColor" strokeWidth="3" className="text-white/10" />
          {/* Active Progress Ring */}
          <circle cx="22" cy="22" r={radius} fill="none" stroke="currentColor" strokeWidth="3" 
            strokeDasharray={circumference} strokeDashoffset={strokeDashoffset} strokeLinecap="round" 
            className="text-blue-500 transition-all duration-500 ease-out" />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center animate-pulse">
             {getIcon()}
        </div>
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-[10px] font-bold uppercase tracking-wider text-blue-500/80">System Status</span>
        <span className="text-sm font-medium text-gray-200 transition-all duration-300 min-w-[180px]">
          {stepName || "Initializing..."}
        </span>
      </div>
    </div>
  );
}