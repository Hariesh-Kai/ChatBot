"use client";

import { useState } from "react";

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
  if (!open) return null;

  const fieldsKey = fields
    .map((f) => `${f.key}:${f.value ?? ""}`)
    .join("|");

  return (
    <MetadataEditorModalContent
      key={fieldsKey}
      title={title}
      fields={fields}
      onCancel={onCancel}
      onSubmit={onSubmit}
    />
  );
}

function MetadataEditorModalContent({
  title,
  fields,
  onCancel,
  onSubmit,
}: Omit<MetadataEditorModalProps, "open">) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    fields.forEach((f) => {
      initial[f.key] = f.value ?? "";
    });
    return initial;
  });

  function handleChange(key: string, value: string) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function handleSubmit() {
    onSubmit(values);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onCancel}
      />

      <div className="relative z-10 w-full max-w-md rounded-xl border border-white/10 bg-black p-6 shadow-xl">
        <div className="mb-4">
          <h2 className="text-lg font-semibold text-white">
            {title}
          </h2>
          <p className="mt-1 text-sm text-gray-400">
            Please provide the missing or unclear details so I can continue.
          </p>
        </div>

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
