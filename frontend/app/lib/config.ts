// frontend/app/lib/config.ts

const rawBase =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export const API_BASE = rawBase.replace(/\/+$/, "");
