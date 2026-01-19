// frontend/components/chat/InlineMetadataPrompt.tsx
"use client";

import { useEffect, useState } from "react";
import { MetadataRequestField } from "@/app/lib/llm-ui-events";
import { ArrowRight, Info } from "lucide-react"; // Optional icons

interface Props {
  fields: MetadataRequestField[];
  onSubmit: (values: Record<string, string>) => Promise<void> | void;
  disabled?: boolean;
}

export default function InlineMetadataPrompt({
  fields,
  onSubmit,
  disabled = false,
}: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  /* =========================================================
     RESET VALUES WHEN FIELDS CHANGE
  ========================================================= */

  useEffect(() => {
    const initial: Record<string, string> = {};
    for (const f of fields) {
      // ✅ FIX: Use the pre-filled value from the backend if available
      initial[f.key] = f.value || "";
    }
    setValues(initial);
  }, [fields]);

  /* =========================================================
     HELPERS
  ========================================================= */

  function updateValue(key: string, value: string) {
    setValues((prev) => ({
      ...prev,
      [key]: value,
    }));
  }

  async function handleSubmit(e?: React.FormEvent) {
    if (e) e.preventDefault();
    if (isBlocked || submitting) return;

    try {
      setSubmitting(true);
      await onSubmit(values);
    } finally {
      setSubmitting(false);
    }
  }

  /* =========================================================
     VALIDATION
  ========================================================= */

  const isBlocked =
    disabled ||
    submitting ||
    fields.some((f) => !values[f.key]?.trim());

  /* =========================================================
     RENDER
  ========================================================= */

  return (
    <div className="mx-2 my-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
      <div className="rounded-xl border border-blue-500/30 bg-blue-500/10 p-5 shadow-sm">
        
        {/* HEADER */}
        <div className="mb-4 flex items-center gap-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-blue-600 text-xs font-bold text-white shadow-md shadow-blue-500/20">
            ?
            </div>
            <h3 className="text-sm font-semibold text-blue-100">
            Information Required
            </h3>
        </div>

        <p className="mb-4 text-xs text-blue-200/70">
            I need a few details to index this document correctly.
        </p>

        {/* FORM */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {fields.map((field, index) => (
            <div key={field.key} className="space-y-1">
              <label className="ml-1 block text-[10px] font-bold uppercase tracking-wider text-blue-300/60">
                {field.label}
              </label>

              <input
                // ✅ UX: Auto-focus the first field so user can type immediately
                autoFocus={index === 0} 
                type="text"
                value={values[field.key] ?? ""}
                placeholder={field.placeholder}
                onChange={(e) => updateValue(field.key, e.target.value)}
                disabled={disabled || submitting}
                className={`
                  w-full rounded-lg border bg-black/60 px-4 py-2.5 text-sm text-white placeholder-blue-500/30 outline-none transition-all
                  ${disabled ? "opacity-50 cursor-not-allowed" : "focus:border-blue-500 focus:ring-1 focus:ring-blue-500 border-blue-500/20"}
                `}
              />
              
              {field.reason && (
                <div className="ml-1 flex items-center gap-1 text-[10px] text-blue-300/50">
                  <Info size={10} />
                  {field.reason}
                </div>
              )}
            </div>
          ))}

          {/* ACTIONS */}
          <div className="flex justify-end pt-2">
            <button
              type="submit"
              disabled={isBlocked}
              className={`
                flex items-center gap-2 rounded-lg px-5 py-2 text-sm font-medium transition-all
                ${
                  isBlocked
                    ? "bg-blue-500/20 text-blue-500/50 cursor-not-allowed"
                    : "bg-blue-600 text-white hover:bg-blue-500 shadow-lg shadow-blue-500/20 hover:scale-[1.02] active:scale-[0.98]"
                }
              `}
            >
              {submitting ? "Processing..." : "Submit Details"} 
              {!submitting && <ArrowRight size={14} />}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}