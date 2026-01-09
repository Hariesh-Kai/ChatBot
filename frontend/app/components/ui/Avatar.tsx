export default function Avatar({ role }: { role: "user" | "assistant" }) {
  return (
    <div
      className={`h-8 w-8 shrink-0 rounded-full flex items-center justify-center text-xs font-semibold ${
        role === "assistant"
          ? "bg-green-600 text-white"
          : "bg-gray-600 text-white"
      }`}
    >
      {role === "assistant" ? "AI" : "U"}
    </div>
  );
}
