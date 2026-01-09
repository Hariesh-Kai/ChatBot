type PromptCardProps = {
  title: string;
  description: string;
  onClick?: () => void;
};

export default function PromptCard({
  title,
  description,
  onClick,
}: PromptCardProps) {
  return (
    <button
      onClick={onClick}
      className="
        w-full rounded-xl border border-white/10
        bg-[#1a1a1a] p-4 text-left
        transition-all duration-200
        hover:border-white/20 hover:bg-[#222]
        active:scale-[0.98]
      "
    >
      <h3 className="text-sm font-medium text-white">{title}</h3>
      <p className="mt-1 text-xs text-gray-400">{description}</p>
    </button>
  );
}
