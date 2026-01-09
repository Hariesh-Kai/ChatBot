"use client";

import { useEffect, useState } from "react";

export type MetadataField = {
  key: string;
  label: string;
  value?: string;
  placeholder?: string;
};

interface MetadataEditorModalProps {
  open: boolean;
  title?: string;
  fields: MetadataField[];
  onCancel: () => void;
  onSubmit: (updated: Record<string, string>) => void;
}

export default function MetadataEditorModal({
  open,
  title = "Additional information required",
  fields,
  onCancel,
  onSubmit,
}: MetadataEditorModalProps) {
  const [values, setValues] = useState<Record<string, string>>({});

  /* ---------------- Initialize form values ---------------- */
  useEffect(() => {
    if (!open) return;

    const initial: Record<string, string> = {};
    fields.forEach((f) => {
      initial[f.key] = f.value ?? "";
    });
    setValues(initial);
  }, [open, fields]);

  if (!open) return null;

  function handleChange(key: string, value: string) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit() {
    onSubmit(values);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* BACKDROP */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onCancel}
      />

      {/* MODAL */}
      <div className="relative z-10 w-full max-w-md rounded-xl border border-white/10 bg-black p-6 shadow-xl">
        {/* HEADER */}
        <div className="mb-4">
          <h2 className="text-lg font-semibold text-white">
            {title}
          </h2>
          <p className="mt-1 text-sm text-gray-400">
            Please provide the missing or unclear details so I can continue.
          </p>
        </div>

        {/* FORM */}
        <div className="space-y-4">
          {fields.map((field) => (
            <div key={field.key}>
              <label className="mb-1 block text-sm text-gray-300">
                {field.label}
              </label>
              <input
                type="text"
                value={values[field.key] ?? ""}
                placeholder={field.placeholder}
                onChange={(e) =>
                  handleChange(field.key, e.target.value)
                }
                className="
                  w-full rounded-md
                  border border-white/10
                  bg-transparent
                  px-3 py-2
                  text-sm text-white
                  outline-none
                  focus:border-white/30
                "
              />
            </div>
          ))}
        </div>

        {/* ACTIONS */}
        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="
              rounded-md px-4 py-2 text-sm
              text-gray-400 hover:text-white
              hover:bg-white/10
            "
          >
            Cancel
          </button>

          <button
            onClick={handleSubmit}
            className="
              rounded-md bg-white px-4 py-2 text-sm
              font-medium text-black
              hover:bg-gray-200
            "
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
