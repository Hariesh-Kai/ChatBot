// frontend/app/lib/config.ts

const rawBase =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export const API_BASE = rawBase.replace(/\/+$/, "");
