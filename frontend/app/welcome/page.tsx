"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import Link from "next/link";
import {
  LayoutDashboard,
  MessageSquareText,
  BellRing,
  Sparkles,
  ArrowRight,
  Activity,
} from "lucide-react";
import { authLogout, authMe, type AuthUser } from "@/app/lib/api";
import { loadChats } from "@/app/lib/chat-store";
import { getRoleLabel } from "@/app/lib/org-role-catalog";

const CHAT_READ_KEY_PREFIX = "kavin-chat-read-at";
const WELCOME_SEEN_KEY_PREFIX = "kavin-welcome-seen";

type DashboardStats = {
  chats: number;
  unread: number;
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

function computeStats(user: AuthUser | null): DashboardStats {
  if (typeof window === "undefined" || !user) return { chats: 0, unread: 0 };

  const chats = loadChats().filter((chat) => chat.messages.length > 0);
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

  // Initialize missing read markers to avoid false unread spikes.
  const nextReadState = { ...readState };
  let mutated = false;
  for (const chat of chats) {
    if (typeof nextReadState[chat.id] !== "number") {
      nextReadState[chat.id] = getIncomingMessageTimestamp(chat);
      mutated = true;
    }
  }
  if (mutated) {
    window.localStorage.setItem(readStateKey, JSON.stringify(nextReadState));
    readState = nextReadState;
  }

  const unread = chats.reduce((sum, chat) => {
    const since = readState[chat.id] || 0;
    return sum + getIncomingMessageCountSince(chat, since);
  }, 0);

  return { chats: chats.length, unread };
}

export default function WelcomePage() {
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

  useEffect(() => {
    if (!user || typeof window === "undefined") return;
    const key = `${WELCOME_SEEN_KEY_PREFIX}:${(user.username || user.email || "default")
      .trim()
      .toLowerCase()}`;
    if (!window.localStorage.getItem(key)) {
      window.localStorage.setItem(key, new Date().toISOString());
    }
  }, [user]);

  const stats = useMemo(() => computeStats(user), [user]);

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
        Loading workspace...
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
              <div className="text-[11px] uppercase tracking-[0.18em] text-sky-300">KavinBase Base</div>
              <h1 className="text-xl font-semibold">Launch Workspace</h1>
            </div>
          </div>
          <button
            onClick={handleSignOut}
            className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-gray-200 hover:bg-white/10"
          >
            Sign out
          </button>
        </div>

        <div className="rounded-2xl border border-white/10 bg-[radial-gradient(circle_at_top_right,#1f2937_0%,#0f172a_35%,#0a0a0a_100%)] p-6 shadow-xl">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <p className="text-sm text-gray-300">
                Welcome back, <span className="font-semibold text-white">{user?.username || "User"}</span>
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
            <Stat icon={LayoutDashboard} label="Workspace" value="Ready" />
            <Stat icon={MessageSquareText} label="Chats" value={String(stats.chats)} />
            <Stat icon={BellRing} label="Unread" value={String(stats.unread)} />
            <Stat icon={Sparkles} label="Assistant" value="Online" />
          </div>
        </div>

        <div className="mt-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Link
            href="/"
            className="rounded-xl border border-white/10 bg-[#111] p-4 hover:border-white/20 hover:bg-[#151515]"
          >
            <div className="text-sm font-semibold">Go To Chat Workspace</div>
            <div className="mt-1 text-xs text-gray-400">Start a new session or continue existing chats.</div>
          </Link>
          <Link
            href="/usage"
            className="rounded-xl border border-white/10 bg-[#111] p-4 hover:border-white/20 hover:bg-[#151515]"
          >
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Activity size={16} className="text-emerald-300" />
              Usage Dashboard
            </div>
            <div className="mt-1 text-xs text-gray-400">
              View your chat usage, activity, and module stats.
            </div>
          </Link>
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
        <Icon size={12} className="text-sky-300" />
        <span>{label}</span>
      </div>
      <div className="mt-1 text-sm font-semibold text-white">{value}</div>
    </div>
  );
}
