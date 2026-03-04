"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import {
  Activity,
  ArrowRight,
  BellRing,
  Clock3,
  Code2,
  MessageSquareText,
} from "lucide-react";

import { authLogout, authMe, type AuthUser } from "@/app/lib/api";
import { loadChats } from "@/app/lib/chat-store";
import { loadPmlChats } from "@/app/lib/pml-chat-store";
import { getRoleLabel } from "@/app/lib/org-role-catalog";

const CHAT_READ_KEY_PREFIX = "kavin-chat-read-at";

type UsageStats = {
  aiChats: number;
  pmlChats: number;
  unreadAi: number;
  prompts: number;
  responses: number;
  lastActivityLabel: string;
};

function getIncomingMessageTimestamp(chat: { messages: { role: string; createdAt: number }[] }) {
  return chat.messages.reduce((latest, msg) => {
    if (msg.role === "user") return latest;
    return Math.max(latest, msg.createdAt || 0);
  }, 0);
}

function getIncomingMessageCountSince(
  chat: { messages: { role: string; createdAt: number }[] },
  sinceTs: number
) {
  return chat.messages.reduce((count, msg) => {
    if (msg.role === "user") return count;
    return msg.createdAt > sinceTs ? count + 1 : count;
  }, 0);
}

function formatLastActivity(ts: number | null) {
  if (!ts) return "No activity yet";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return "No activity yet";
  }
}

function computeUsageStats(user: AuthUser | null): UsageStats {
  if (typeof window === "undefined" || !user) {
    return {
      aiChats: 0,
      pmlChats: 0,
      unreadAi: 0,
      prompts: 0,
      responses: 0,
      lastActivityLabel: "No activity yet",
    };
  }

  const aiChats = loadChats().filter((chat) => chat.messages.length > 0);
  const pmlChats = loadPmlChats().filter((chat) => chat.messages.length > 0);

  const readStateKey = `${CHAT_READ_KEY_PREFIX}:${(user.username || user.email || "default")
    .trim()
    .toLowerCase()}`;
  let readState: Record<string, number> = {};
  try {
    const raw = window.localStorage.getItem(readStateKey);
    const parsed = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      readState = parsed as Record<string, number>;
    }
  } catch {
    readState = {};
  }

  const unreadAi = aiChats.reduce((sum, chat) => {
    const since = readState[chat.id] || 0;
    return sum + getIncomingMessageCountSince(chat, since);
  }, 0);

  let prompts = 0;
  let responses = 0;
  let lastActivityTs: number | null = null;
  for (const chat of [...aiChats, ...pmlChats]) {
    for (const msg of chat.messages) {
      if (msg.role === "user") prompts += 1;
      if (msg.role === "assistant") responses += 1;
      if (typeof msg.createdAt === "number") {
        lastActivityTs = Math.max(lastActivityTs || 0, msg.createdAt);
      }
    }
    const latestIncoming = getIncomingMessageTimestamp(chat);
    if (latestIncoming > 0) {
      lastActivityTs = Math.max(lastActivityTs || 0, latestIncoming);
    }
  }

  return {
    aiChats: aiChats.length,
    pmlChats: pmlChats.length,
    unreadAi,
    prompts,
    responses,
    lastActivityLabel: formatLastActivity(lastActivityTs),
  };
}

export default function UsagePage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    authMe()
      .then((u) => {
        if (cancelled) return;
        if (!u) {
          router.replace("/signin");
          return;
        }
        setUser(u);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  const stats = useMemo(() => computeUsageStats(user), [user]);

  async function handleSignOut() {
    try {
      await authLogout();
    } finally {
      router.replace("/signin");
    }
  }

  if (loading) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-black text-gray-400">
        Loading usage dashboard...
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="mx-auto w-full max-w-5xl px-6 py-8">
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Image src="/kavin-logo.svg" alt="Kavin" width={30} height={30} />
            <div>
              <div className="text-[11px] uppercase tracking-[0.18em] text-emerald-300">
                User Analytics
              </div>
              <h1 className="text-xl font-semibold">Usage Dashboard</h1>
            </div>
          </div>
          <button
            onClick={handleSignOut}
            className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-gray-200 hover:bg-white/10"
          >
            Sign out
          </button>
        </div>

        <div className="rounded-2xl border border-white/10 bg-[radial-gradient(circle_at_top_right,#102818_0%,#0f172a_35%,#0a0a0a_100%)] p-6 shadow-xl">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-sm text-gray-300">
                Usage snapshot for <span className="font-semibold text-white">{user?.username || "User"}</span>
              </p>
              <p className="mt-1 text-xs text-gray-400">
                Signed in as {getRoleLabel(user?.role)}.
              </p>
            </div>
            <Link
              href="/"
              className="inline-flex items-center gap-2 rounded-lg bg-white px-4 py-2 text-sm font-semibold text-black hover:bg-gray-200"
            >
              Open KavinBase
              <ArrowRight size={16} />
            </Link>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat icon={MessageSquareText} label="AI Chats" value={String(stats.aiChats)} />
            <Stat icon={Code2} label="PML Chats" value={String(stats.pmlChats)} />
            <Stat icon={BellRing} label="Unread AI" value={String(stats.unreadAi)} />
            <Stat icon={Clock3} label="Last Activity" value={stats.lastActivityLabel} />
          </div>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-white/10 bg-[#111] p-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Activity size={16} className="text-emerald-300" />
              Message Usage
            </div>
            <div className="mt-2 text-xs text-gray-400">
              Prompts sent: <span className="text-gray-200">{stats.prompts}</span>
            </div>
            <div className="mt-1 text-xs text-gray-400">
              Assistant responses: <span className="text-gray-200">{stats.responses}</span>
            </div>
          </div>

          <div className="rounded-xl border border-white/10 bg-[#111] p-4">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Activity size={16} className="text-emerald-300" />
              Standard Access
            </div>
            <div className="mt-1 text-xs text-gray-400">
              Usage dashboard is available for all users.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-black/30 px-3 py-2">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-gray-400">
        <Icon size={12} className="text-emerald-300" />
        <span>{label}</span>
      </div>
      <div className="mt-1 text-sm font-semibold text-white truncate" title={value}>
        {value}
      </div>
    </div>
  );
}
