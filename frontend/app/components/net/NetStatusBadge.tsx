// frontend/components/net/NetStatusBadge.tsx

"use client";

import { useEffect, useState } from "react";
import { getNetStatus, NetStatus } from "@/app/lib/netStatus";

export default function NetStatusBadge() {
  const [status, setStatus] = useState<NetStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;

    async function load() {
      try {
        const s = await getNetStatus();
        if (mounted) setStatus(s);
      } finally {
        if (mounted) setLoading(false);
      }
    }

    load();

    const interval = setInterval(load, 30_000); // refresh every 30s
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  if (loading || !status) {
    return (
      <span className="px-2 py-1 rounded text-xs bg-gray-700 text-gray-300">
        NET: …
      </span>
    );
  }

  if (status.rateLimited) {
    return (
      <span className="px-2 py-1 rounded text-xs bg-yellow-600 text-black">
        NET: Rate limited
      </span>
    );
  }

  if (!status.available) {
    return (
      <span className="px-2 py-1 rounded text-xs bg-red-600 text-white">
        NET: Offline
      </span>
    );
  }

  return (
    <span className="px-2 py-1 rounded text-xs bg-green-600 text-white">
      NET: {status.provider?.toUpperCase()}
    </span>
  );
}
