import type { KavinModelId } from "./kavin-models";

type AvatarConfig = {
  label: string;
  className: string;
};

export function getModelAvatar(model?: KavinModelId): AvatarConfig {
  switch (model) {
    case "lite":
      return { label: "L", className: "bg-emerald-600 text-white" };
    case "base":
      return { label: "B", className: "bg-blue-600 text-white" };
    case "net":
      return { label: "N", className: "bg-purple-600 text-white" };
    default:
      return { label: "AI", className: "bg-green-600 text-white" };
  }
}

