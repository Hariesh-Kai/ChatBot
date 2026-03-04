"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { authMe } from "@/app/lib/api";

const WORKSPACE_MODE_KEY_PREFIX = "kavin-workspace-mode";

export default function PmlEntryRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;

    authMe()
      .then((user) => {
        if (cancelled) return;
        if (!user) {
          router.replace("/signin");
          return;
        }

        if (typeof window !== "undefined") {
          const key = `${WORKSPACE_MODE_KEY_PREFIX}:${(user.username || user.email || "default")
            .trim()
            .toLowerCase()}`;
          window.localStorage.setItem(key, "pml");
        }
        router.replace("/");
      })
      .catch(() => {
        if (!cancelled) router.replace("/signin");
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-black text-gray-400">
      Redirecting to workspace...
    </div>
  );
}
