"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authLogin, authMe } from "@/app/lib/api";

export default function SignInPage() {
  const router = useRouter();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    authMe().then((u) => {
      if (u) router.replace("/");
    });
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

  return (
    <div className="min-h-screen bg-black text-white flex items-center justify-center p-6">
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0b0b0b] p-6 shadow-xl">
        <div className="mb-6">
          <div className="text-xs text-gray-500 uppercase tracking-wider">KAVIN</div>
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

        <div className="mt-6 flex items-center justify-between text-xs text-gray-500">
          <div>© KAVIN</div>
          <a href="/" className="hover:text-gray-300">Back</a>
        </div>
      </div>
    </div>
  );
}

