"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function PmlSignInRedirectPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/signin");
  }, [router]);

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-black text-gray-400">
      Redirecting to sign in...
    </div>
  );
}
