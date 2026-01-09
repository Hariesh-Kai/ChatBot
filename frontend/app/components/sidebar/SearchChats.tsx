"use client";

interface Props {
  value: string;
  onChange: (v: string) => void;
}

export default function SearchChats({ value, onChange }: Props) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Search chats"
      className="
        w-full rounded-lg
        bg-[#111] px-3 py-2
        text-sm text-white
        placeholder:text-gray-500
        border border-white/10
        outline-none
        focus:border-white/20
      "
    />
  );
}
