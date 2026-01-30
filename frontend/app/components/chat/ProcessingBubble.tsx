// frontend/components/chat/ProcessingBubble.tsx
"use client";

import { Loader2, FileText, Server, Database, BrainCircuit } from "lucide-react";

interface ProcessingBubbleProps {
  stepName: string;        // e.g. "Searching documents..."
  progress?: number;       // OPTIONAL for AI stages
}

export default function ProcessingBubble({ 
  stepName, 
  progress, 
}: ProcessingBubbleProps) {
  
  const radius = 18;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = progress !== undefined ? circumference - (progress / 100) * circumference : circumference;

  // Dynamic Icon based on the current step text
  const getIcon = () => {
  const lower = stepName.toLowerCase();

  if (lower.includes("upload")) return <Server size={18} className="text-blue-400" />;
  if (lower.includes("chunk") || lower.includes("analyz")) return <FileText size={18} className="text-purple-400" />;
  if (lower.includes("embed") || lower.includes("index")) return <Database size={18} className="text-orange-400" />;
  if (lower.includes("search") || lower.includes("retrieve")) return <Database size={18} className="text-cyan-400" />;
  if (lower.includes("generate") || lower.includes("reason")) return <BrainCircuit size={18} className="text-gray-300" />;

  return <Loader2 size={18} className="animate-spin text-gray-400" />;
};


  

  return (
    <div className="flex w-full max-w-sm items-center gap-4 rounded-xl border border-white/10 bg-[#0a0a0a] px-5 py-4 shadow-sm transition-all">
      <div className="relative flex h-10 w-10 items-center justify-center">
        {/* Background Ring */}
        <svg className="absolute h-full w-full rotate-[-90deg]" viewBox="0 0 44 44">
          <circle cx="22" cy="22" r={radius} fill="none" stroke="currentColor" strokeWidth="3" className="text-white/10" />
          {/* Active Progress Ring */}
          {progress !== undefined && (
            <circle cx="22" cy="22" r={radius} fill="none" stroke="currentColor" strokeWidth="3" strokeDasharray={circumference} strokeDashoffset={strokeDashoffset} strokeLinecap="round" className="text-blue-500 transition-all duration-500 ease-out"/>
          )}

        </svg>
        <div
        className={`absolute inset-0 flex items-center justify-center ${
          progress === undefined ? "animate-pulse" : ""
        }`}
      >

             {getIcon()}
        </div>
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-white/40">
          Processing
        </span>
        <span className="text-sm font-medium text-gray-200 transition-all duration-300 min-w-[180px]">
          {stepName || "Initializing..."}
        </span>
      </div>
    </div>
  );
}