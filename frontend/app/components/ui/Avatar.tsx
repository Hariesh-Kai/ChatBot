export default function Avatar({
  role,
  label,
  assistantLabel,
  assistantClassName,
  size = "md",
}: {
  role: "user" | "assistant";
  label?: string;
  assistantLabel?: string;
  assistantClassName?: string;
  size?: "sm" | "md";
}) {
  const initial = (label || "").trim().charAt(0).toUpperCase() || "U";
  const sizeClass = size === "sm" ? "h-7 w-7 text-xs" : "h-8 w-8 text-xs";
  const assistantText = assistantLabel || "AI";
  const assistantBg = assistantClassName || "bg-green-600 text-white";

  return (
    <div
      className={`${sizeClass} shrink-0 rounded-full flex items-center justify-center font-semibold ${
        role === "assistant" ? assistantBg : "bg-gray-600 text-white"
      }`}
    >
      {role === "assistant" ? assistantText : initial}
    </div>
  );
}
