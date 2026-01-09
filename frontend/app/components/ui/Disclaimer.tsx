"use client";

interface DisclaimerProps {
  text?: string;
}

export default function Disclaimer({
  text = "KavinBase may make mistakes. Please verify important information.",
}: DisclaimerProps) {
  return (
    <div className="px-4 py-2 text-center text-[11px] text-gray-500">
      {text}
    </div>
  );
}
