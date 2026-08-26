"use client";

import { useEffect, useRef, useState } from "react";
import { AGENTS } from "@/lib/agents";
import { getPipeline, openPipelineSocket } from "@/lib/api";

type AgentState = {
  status: "pending" | "running" | "completed" | "failed";
  pct: number;
  message: string;
  summary?: string;
};

interface Props {
  projectId: string;
  initialStatus: string;
  onComplete?: () => void;
}

export default function PipelineView({ projectId, initialStatus, onComplete }: Props) {
  const [states, setStates] = useState<Record<string, AgentState>>(() =>
    Object.fromEntries(AGENTS.map((a) => [a.key, { status: "pending", pct: 0, message: "" }]))
  );
  const [pipelineStatus, setPipelineStatus] = useState(initialStatus);
  const [summary, setSummary] = useState<Record<string, any> | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Hydrate from REST first (covers reload / already-finished runs).
  useEffect(() => {
    getPipeline(projectId)
      .then((p) => {
        setPipelineStatus(p.project_status);
        setStates((prev) => {
          const next = { ...prev };
          for (const a of p.agents) {
            const key = a.agent;
            if (next[key]) {
              next[key] = {
                status: a.status as AgentState["status"],
                pct: a.progress_pct ?? (a.status === "completed" ? 100 : 0),
                message: a.error || "",
              };
            }
          }
          return next;
        });
      })
      .catch(() => {});
  }, [projectId]);

  // Live updates via WebSocket.
  useEffect(() => {
    if (initialStatus !== "processing") return;
    const ws = openPipelineSocket(projectId);
    if (!ws) return;
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      if (data.event === "agent.started") {
        update(data.agent, { status: "running", pct: 0 });
      } else if (data.event === "agent.progress") {
        update(data.agent, {
          status: "running",
          pct: data.progress_pct ?? 0,
          message: data.message || "",
        });
      } else if (data.event === "agent.completed") {
        update(data.agent, { status: "completed", pct: 100, summary: data.summary });
      } else if (data.event === "pipeline.complete") {
        setPipelineStatus("completed");
        setSummary(data.summary);
        onComplete?.();
      } else if (data.event === "pipeline.failed") {
        setPipelineStatus("failed");
      }
    };
    ws.onclose = () => { wsRef.current = null; };
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, initialStatus]);

  function update(key: string, patch: Partial<AgentState>) {
    setStates((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  }

  const doneCount = Object.values(states).filter((s) => s.status === "completed").length;

  return (
    <div>
      <div className="mb-5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              pipelineStatus === "processing"
                ? "bg-amber-400 animate-pulse"
                : pipelineStatus === "completed"
                ? "bg-emerald-400"
                : pipelineStatus === "failed"
                ? "bg-red-400"
                : "bg-slate-500"
            }`}
          />
          <span className="text-sm font-medium capitalize">{pipelineStatus}</span>
        </div>
        <span className="text-sm text-slate-400">{doneCount}/11 agents complete</span>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {AGENTS.map((a) => {
          const st = states[a.key];
          return (
            <div
              key={a.key}
              className="rounded-xl border border-border bg-surface p-4 transition"
              style={{ borderLeft: `3px solid ${a.color}`, opacity: st.status === "pending" ? 0.55 : 1 }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <span
                    className="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold"
                    style={{ background: `${a.color}33`, color: a.color }}
                  >
                    {a.num}
                  </span>
                  <span className="text-sm font-semibold">{a.label}</span>
                </div>
                <StatusPill status={st.status} />
              </div>

              <p className="mt-2 min-h-[16px] text-xs text-slate-400">
                {st.message || st.summary || a.desc}
              </p>

              {(st.status === "running" || st.status === "completed") && (
                <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-surface2">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${st.pct}%`, background: a.color }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {summary && (
        <div className="mt-6 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
          <p className="mb-2 text-sm font-semibold text-emerald-300">✓ Pipeline complete</p>
          <div className="flex flex-wrap gap-4 text-xs text-slate-300">
            <span>{summary.segments} segments</span>
            <span>{summary.words_transcribed} words</span>
            <span>{summary.emotional_peaks} emotional peaks</span>
            <span>{summary.story_beats} story beats</span>
            <span>{summary.formats} formats rendered</span>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: AgentState["status"] }) {
  const map = {
    pending: { t: "Pending", c: "bg-slate-500/20 text-slate-400" },
    running: { t: "Running", c: "bg-amber-500/20 text-amber-300 animate-pulse" },
    completed: { t: "Done", c: "bg-emerald-500/20 text-emerald-300" },
    failed: { t: "Failed", c: "bg-red-500/20 text-red-300" },
  }[status];
  return <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium ${map.c}`}>{map.t}</span>;
}
