"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import Image from "next/image";
import { authLogin, authMe, waitForBackendReady } from "@/app/lib/api";
import FullScreenLoader from "@/app/components/ui/FullScreenLoader";

export default function SignInPage() {
  const router = useRouter();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [warming, setWarming] = useState(true);
  const [warmupError, setWarmupError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const runWarmup = async () => {
      setWarming(true);
      setWarmupError(null);
      const ok = await waitForBackendReady({ timeoutMs: 20000 });
      if (cancelled) return;
      if (!ok) {
        setWarmupError("Services are still starting. Please try again.");
        setWarming(false);
        return;
      }
      const u = await authMe();
      if (!cancelled && u) router.replace("/");
      if (!cancelled) setWarming(false);
    };
    runWarmup();
    return () => {
      cancelled = true;
    };
  }, [router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await authLogin(identifier, password);
      router.replace("/");
    } catch (err: any) {
      setError(err?.message || "Sign in failed");
    } finally {
      setBusy(false);
    }
  }

  if (warming) {
    return (
      <FullScreenLoader
        title="Preparing sign in"
        subtitle="Starting services. This usually takes a few seconds."
      />
    );
  }

  if (warmupError) {
    return (
      <FullScreenLoader
        title="Service startup in progress"
        subtitle={warmupError}
        actionLabel="Retry"
        onAction={() => {
          setWarmupError(null);
          setWarming(true);
          waitForBackendReady({ timeoutMs: 20000 }).then((ok) => {
            if (!ok) {
              setWarmupError("Services are still starting. Please try again.");
              setWarming(false);
              return;
            }
            authMe().then((u) => {
              if (u) router.replace("/");
              setWarming(false);
            });
          });
        }}
      />
    );
  }

  return (
    <div className="min-h-screen bg-black text-white flex items-center justify-center p-6">
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0b0b0b] p-6 shadow-xl">
        <div className="mb-6">
          <div className="flex items-center gap-3">
            <Image src="/kavin-logo.svg" alt="Kavin Engineering" width={32} height={32} />
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-wider">Kavin Engineering</div>
            </div>
          </div>
          <h1 className="text-2xl font-semibold">Sign in</h1>
          <div className="mt-1 text-sm text-gray-400">
            Use your admin username/email and password configured on the backend.
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-200">
            {error}
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="block text-xs text-gray-400 mb-2">Username or email</label>
            <input
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              autoComplete="username"
              className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-white/30"
              placeholder="e.g. hariesh-kavin"
              disabled={busy}
            />
          </div>

          <div>
            <label className="block text-xs text-gray-400 mb-2">Password</label>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              autoComplete="current-password"
              className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-2 text-sm text-white outline-none focus:border-white/30"
              placeholder="Your password"
              disabled={busy}
            />
          </div>

          <button
            type="submit"
            disabled={busy || !identifier.trim() || !password}
            className="w-full rounded-md bg-white px-4 py-2 text-sm font-medium text-black hover:bg-gray-200 disabled:opacity-50"
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <div className="mt-6 flex items-center justify-end text-xs text-gray-500">
          <Link href="/" className="hover:text-gray-300">Back</Link>
        </div>
      </div>
    </div>
  );
}
