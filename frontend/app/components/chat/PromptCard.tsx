"use client";

type PromptCardProps = {
  title: string;
  description: string;
  onClick?: () => void;
  disabled?: boolean; //  NEW PROP
};

export default function PromptCard({
  title,
  description,
  onClick,
  disabled = false,
}: PromptCardProps) {
  return (
    <button
      onClick={() => !disabled && onClick?.()}
      disabled={disabled}
      className={`
        w-full rounded-xl border p-4 text-left transition-all duration-200
        ${
          disabled
            ? "border-white/5 bg-[#1a1a1a]/50 text-gray-600 cursor-not-allowed" // Disabled style
            : "border-white/10 bg-[#1a1a1a] hover:border-white/20 hover:bg-[#222] active:scale-[0.98] cursor-pointer"
        }
      `}
    >
      <h3 className={`text-sm font-medium ${disabled ? "text-gray-500" : "text-white"}`}>
        {title}
      </h3>
      <p className={`mt-1 text-xs ${disabled ? "text-gray-600" : "text-gray-400"}`}>
        {description}
      </p>
    </button>
  );
}