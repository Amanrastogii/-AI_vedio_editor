"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import Link from "next/link";
import PipelineView from "@/components/PipelineView";
import { FORMAT_META } from "@/lib/agents";
import {
  Clip,
  Output,
  Project,
  getProject,
  getToken,
  listClips,
  listOutputs,
  startProcessing,
  uploadClip,
} from "@/lib/api";

type Tab = "upload" | "pipeline" | "outputs";

export default function ProjectPage() {
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;

  const [project, setProject] = useState<Project | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [tab, setTab] = useState<Tab>("upload");
  const [uploading, setUploading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function load() {
    try {
      const [p, c] = await Promise.all([getProject(id), listClips(id)]);
      setProject(p);
      setClips(c);
      if (p.status === "processing") setTab("pipeline");
      else if (p.status === "completed") {
        setTab("outputs");
        setOutputs(await listOutputs(id));
      }
    } catch (e: any) {
      if (String(e.message).includes("401")) router.push("/login");
      else setError(e.message);
    }
  }

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError("");
    setUploading(true);
    try {
      for (const f of Array.from(files)) {
        await uploadClip(id, f);
      }
      setClips(await listClips(id));
    } catch (e: any) {
      setError(e.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleProcess() {
    setProcessing(true);
    setError("");
    try {
      await startProcessing(id);
      const p = await getProject(id);
      setProject(p);
      setTab("pipeline");
    } catch (e: any) {
      setError(e.message || "Failed to start processing");
    } finally {
      setProcessing(false);
    }
  }

  async function onPipelineComplete() {
    const [p, o] = await Promise.all([getProject(id), listOutputs(id)]);
    setProject(p);
    setOutputs(o);
  }

  if (!project) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-bg text-slate-500">
        {error ? <span className="text-red-400">{error}</span> : "Loading…"}
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-bg">
      <header className="sticky top-0 z-10 border-b border-border bg-bg/80 backdrop-blur">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <Link href="/" className="text-slate-400 transition hover:text-white">
              ←
            </Link>
            <div>
              <h1 className="text-base font-bold leading-tight">{project.title}</h1>
              <p className="text-xs capitalize text-slate-400">{project.status}</p>
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-8">
        {/* Tabs */}
        <div className="mb-6 flex gap-1 rounded-lg border border-border bg-surface p-1">
          {(["upload", "pipeline", "outputs"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => {
                setTab(t);
                if (t === "outputs") listOutputs(id).then(setOutputs);
              }}
              className={`flex-1 rounded-md px-4 py-2 text-sm font-medium capitalize transition ${
                tab === t ? "bg-accent text-white" : "text-slate-400 hover:text-white"
              }`}
            >
              {t === "upload" ? `Upload (${clips.length})` : t}
            </button>
          ))}
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        {/* Upload tab */}
        {tab === "upload" && (
          <div>
            <div
              onClick={() => fileRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                handleFiles(e.dataTransfer.files);
              }}
              className="flex min-h-[160px] cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-border bg-surface/50 p-8 text-center transition hover:border-accent"
            >
              <input
                ref={fileRef}
                type="file"
                accept="video/*"
                multiple
                hidden
                onChange={(e) => handleFiles(e.target.files)}
              />
              <div className="mb-2 text-3xl">⬆️</div>
              <p className="font-semibold">{uploading ? "Uploading…" : "Drop video clips or click to browse"}</p>
              <p className="mt-1 text-xs text-slate-400">MP4, MOV, WebM · multiple files supported</p>
            </div>

            {clips.length > 0 && (
              <div className="mt-6">
                <h3 className="mb-3 text-sm font-bold uppercase tracking-wider text-slate-300">
                  Uploaded clips
                </h3>
                <div className="space-y-2">
                  {clips.map((c) => (
                    <div
                      key={c.id}
                      className="flex items-center justify-between rounded-lg border border-border bg-surface px-4 py-3"
                    >
                      <div className="flex items-center gap-3">
                        <span className="flex h-8 w-8 items-center justify-center rounded bg-surface2 text-xs">
                          🎬
                        </span>
                        <div>
                          <p className="text-sm font-medium">{c.original_filename}</p>
                          <p className="text-xs text-slate-500">
                            {c.file_size_bytes
                              ? `${(c.file_size_bytes / 1e6).toFixed(1)} MB`
                              : "—"}{" "}
                            · order #{c.upload_order}
                          </p>
                        </div>
                      </div>
                      <span className="text-xs text-emerald-400">ready</span>
                    </div>
                  ))}
                </div>

                <button
                  onClick={handleProcess}
                  disabled={processing || project.status === "processing"}
                  className="mt-6 w-full rounded-lg bg-gradient-to-r from-accent to-accent4 py-3 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  {processing
                    ? "Starting…"
                    : project.status === "processing"
                    ? "Processing…"
                    : "🚀 Start AI Editing — Run 11-Agent Pipeline"}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Pipeline tab */}
        {tab === "pipeline" && (
          <PipelineView
            projectId={id}
            initialStatus={project.status}
            onComplete={onPipelineComplete}
          />
        )}

        {/* Outputs tab */}
        {tab === "outputs" && (
          <div>
            {outputs.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-border bg-surface/50 py-16 text-center text-slate-400">
                No outputs yet — run the pipeline first.
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                {outputs.map((o) => (
                  <div key={o.id} className="rounded-xl border border-border bg-surface p-4">
                    <div className="mb-3 flex items-center justify-between">
                      <div>
                        <h3 className="font-semibold">{FORMAT_META[o.format]?.label || o.format}</h3>
                        <p className="text-xs text-slate-400">
                          {o.aspect_ratio} · {o.width}×{o.height}
                          {o.duration_ms ? ` · ${(o.duration_ms / 1000).toFixed(0)}s` : ""}
                        </p>
                      </div>
                      {o.quality_score != null && (
                        <span className="rounded-full bg-emerald-500/20 px-2.5 py-1 text-xs font-medium text-emerald-300">
                          VMAF {o.quality_score}
                        </span>
                      )}
                    </div>
                    {o.download_url ? (
                      <video
                        src={o.download_url}
                        controls
                        className="aspect-video w-full rounded-lg bg-black"
                      />
                    ) : (
                      <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-black/50 text-xs text-slate-500">
                        rendering…
                      </div>
                    )}
                    <div className="mt-3 flex gap-2">
                      {o.download_url && (
                        <a
                          href={o.download_url}
                          download
                          className="flex-1 rounded-lg border border-border bg-surface2 py-2 text-center text-xs font-medium transition hover:border-accent"
                        >
                          ⬇ Download
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
