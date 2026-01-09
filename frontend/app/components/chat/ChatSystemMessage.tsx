"use client";

interface ChatSystemMessageProps {
  text: string;
}

export default function ChatSystemMessage({
  text,
}: ChatSystemMessageProps) {
  return (
    <div className="flex justify-center my-3">
      <div
        className="
          max-w-[90%]
          rounded-lg
          border border-white/10
          bg-white/5
          px-4 py-2
          text-center
          text-sm
          text-gray-400
          italic
          backdrop-blur
        "
      >
        {text}
      </div>
    </div>
  );
}
