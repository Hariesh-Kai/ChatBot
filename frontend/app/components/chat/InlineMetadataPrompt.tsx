// frontend/components/chat/InlineMetadataPrompt.tsx
"use client";

import { useEffect, useState } from "react";
import { MetadataRequestField } from "@/app/lib/llm-ui-events";

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
      initial[f.key] = "";
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

  async function handleSubmit() {
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
    <div className="rounded-xl border border-white/10 bg-[#1a1a1a] px-4 py-3">
      <div className="mb-3 text-sm text-gray-300">
        I need a bit more information to continue:
      </div>

      <div className="space-y-3">
        {fields.map((field) => (
          <div key={field.key}>
            <label className="mb-1 block text-xs text-gray-400">
              {field.label}
            </label>

            {field.reason && (
              <div className="mb-1 text-[11px] text-gray-500">
                {field.reason}
              </div>
            )}

            <input
              type="text"
              value={values[field.key] ?? ""}
              placeholder={field.placeholder}
              onChange={(e) =>
                updateValue(field.key, e.target.value)
              }
              disabled={disabled || submitting}
              className="
                w-full rounded-md
                border border-white/10
                bg-black px-3 py-2
                text-sm text-white
                placeholder-gray-500
                outline-none
                focus:border-blue-500
                disabled:opacity-60
              "
            />
          </div>
        ))}
      </div>

      <div className="mt-4 flex justify-end">
        <button
          onClick={handleSubmit}
          disabled={isBlocked}
          className="
            rounded-md bg-blue-600
            px-4 py-2 text-sm font-medium
            text-white
            hover:bg-blue-500
            disabled:opacity-50
            disabled:cursor-not-allowed
          "
        >
          {submitting ? "Submitting…" : "Continue"}
        </button>
      </div>
    </div>
  );
}
