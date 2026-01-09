"use client";

interface Props {
  open: boolean;
  title?: string;
  description?: string;
  onCancel: () => void;
  onConfirm: () => void;
}

export default function DeleteConfirmModal({
  open,
  title = "Delete chat?",
  description = "This will delete all messages in this chat permanently.",
  onCancel,
  onConfirm,
}: Props) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-confirm-title"
      aria-describedby="delete-confirm-description"
    >
      {/* Overlay */}
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onCancel}
      />

      {/* Modal */}
      <div
        className="
          relative w-full max-w-md
          rounded-xl border border-white/10
          bg-[#1a1a1a] p-6 shadow-2xl
        "
      >
        <h2
          id="delete-confirm-title"
          className="text-lg font-semibold text-white"
        >
          {title}
        </h2>

        <p
          id="delete-confirm-description"
          className="mt-2 text-sm text-gray-400"
        >
          {description}
        </p>

        {/* Actions */}
        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="
              rounded-lg px-4 py-2 text-sm
              text-gray-300 hover:bg-white/5
            "
          >
            Cancel
          </button>

          <button
            type="button"
            onClick={onConfirm}
            className="
              rounded-lg px-4 py-2 text-sm
              bg-red-600 text-white
              hover:bg-red-500
            "
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}
