import type { ChatUIModelId } from "./chat-ui-models";

type AvatarConfig = {
  label: string;
  className: string;
};

export function getModelAvatar(model?: ChatUIModelId): AvatarConfig {
  switch (model) {
    case "lite":
      return { label: "L", className: "bg-sky-600 text-white" };
    case "base":
      return { label: "B", className: "bg-blue-600 text-white" };
    case "net":
      return { label: "N", className: "bg-indigo-600 text-white" };
    default:
      return { label: "AI", className: "bg-slate-600 text-white" };
  }
}

