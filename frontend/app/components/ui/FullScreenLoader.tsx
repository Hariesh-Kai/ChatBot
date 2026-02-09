"use client";

interface Props {
  title?: string;
  subtitle?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export default function FullScreenLoader({
  title = "Warming up…",
  subtitle = "Starting services. This may take a few seconds.",
  actionLabel,
  onAction,
}: Props) {
  return (
    <div className="min-h-screen bg-black text-white flex items-center justify-center p-6">
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0b0b0b] p-6 shadow-xl text-center">
        <div className="mx-auto h-10 w-10 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
        <div className="mt-4 text-lg font-semibold">{title}</div>
        <div className="mt-2 text-sm text-gray-400">{subtitle}</div>
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
