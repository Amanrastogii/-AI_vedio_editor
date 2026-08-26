"use client";

import { useEffect, useState } from "react";
import { getHealth, HealthStatus } from "@/lib/api";

export default function BackendStatus() {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    const check = async () => {
      const h = await getHealth();
      if (active) {
        setHealth(h);
        setLoading(false);
      }
    };
    check();
    const interval = setInterval(check, 5000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const online = !!health;
  const dbReady = health?.db_ready ?? false;

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs">
        <span
          className={`h-2 w-2 rounded-full ${
            loading
              ? "bg-yellow-400 animate-pulse"
              : online
              ? "bg-emerald-400"
              : "bg-red-400"
          }`}
        />
        <span className="text-slate-300">
          API {loading ? "checking…" : online ? `online · v${health?.version}` : "offline"}
        </span>
      </div>
      <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs">
        <span className={`h-2 w-2 rounded-full ${dbReady ? "bg-emerald-400" : "bg-orange-400"}`} />
        <span className="text-slate-300">DB {dbReady ? "ready" : "waiting"}</span>
      </div>
    </div>
  );
}
