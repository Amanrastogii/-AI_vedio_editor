"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import BackendStatus from "@/components/BackendStatus";
import {
  Project,
  clearToken,
  createProject,
  deleteProject,
  getToken,
  listProjects,
} from "@/lib/api";
import { FORMAT_META } from "@/lib/agents";

const ALL_FORMATS = ["youtube", "shorts", "reels", "tiktok", "linkedin"];

const STATUS_STYLE: Record<string, string> = {
  created: "bg-slate-500/20 text-slate-300",
  uploading: "bg-blue-500/20 text-blue-300",
  processing: "bg-amber-500/20 text-amber-300 animate-pulse",
  completed: "bg-emerald-500/20 text-emerald-300",
  failed: "bg-red-500/20 text-red-300",
  cancelled: "bg-slate-500/20 text-slate-400",
};

export default function Dashboard() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [formats, setFormats] = useState<string[]>(["youtube", "shorts"]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  async function refresh() {
    try {
      setProjects(await listProjects());
    } catch (e: any) {
      if (String(e.message).includes("401")) router.push("/login");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    refresh();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setCreating(true);
    try {
      const p = await createProject(title, formats);
      router.push(`/projects/${p.id}`);
    } catch (e: any) {
      setError(e.message || "Failed to create project");
      setCreating(false);
    }
  }

  function toggleFormat(f: string) {
    setFormats((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]));
  }

  async function handleDelete(id: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("Delete this project?")) return;
    await deleteProject(id);
    refresh();
  }

  function logout() {
    clearToken();
    router.push("/login");
  }

  return (
    <main className="min-h-screen bg-bg">
      <header className="sticky top-0 z-10 border-b border-border bg-bg/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-accent to-accent4 text-lg">
              🎬
            </div>
            <div>
              <h1 className="text-base font-bold leading-tight">AI Video Editor</h1>
              <p className="text-xs text-slate-400">11-agent autonomous pipeline</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <BackendStatus />
            <button
              onClick={logout}
              className="rounded-lg border border-border bg-surface px-3 py-1.5 text-xs text-slate-300 transition hover:border-accent"
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="text-2xl font-bold">Your Projects</h2>
            <p className="text-sm text-slate-400">Create a project, upload clips, let the AI edit.</p>
          </div>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:opacity-90"
          >
            {showCreate ? "Cancel" : "+ New Project"}
          </button>
        </div>

        {showCreate && (
          <form
            onSubmit={handleCreate}
            className="mb-8 rounded-2xl border border-border bg-surface p-6"
          >
            <h3 className="mb-4 text-sm font-bold uppercase tracking-wider text-slate-300">
              New Project
            </h3>
            <div className="mb-4">
              <label className="mb-1 block text-xs font-medium text-slate-400">Title</label>
              <input
                required
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full rounded-lg border border-border bg-surface2 px-3 py-2 text-sm outline-none focus:border-accent"
                placeholder="My awesome video"
              />
            </div>
            <div className="mb-4">
              <label className="mb-2 block text-xs font-medium text-slate-400">Output formats</label>
              <div className="flex flex-wrap gap-2">
                {ALL_FORMATS.map((f) => (
                  <button
                    type="button"
                    key={f}
                    onClick={() => toggleFormat(f)}
                    className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                      formats.includes(f)
                        ? "border-accent bg-accent/20 text-white"
                        : "border-border bg-surface2 text-slate-400"
                    }`}
                  >
                    {FORMAT_META[f].label} · {FORMAT_META[f].ratio}
                  </button>
                ))}
              </div>
            </div>
            {error && <p className="mb-3 text-xs text-red-400">{error}</p>}
            <button
              type="submit"
              disabled={creating || formats.length === 0}
              className="rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {creating ? "Creating…" : "Create & Upload Clips →"}
            </button>
          </form>
        )}

        {loading ? (
          <div className="py-20 text-center text-slate-500">Loading projects…</div>
        ) : projects.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border bg-surface/50 py-20 text-center">
            <div className="mb-2 text-4xl">📂</div>
            <p className="font-semibold">No projects yet</p>
            <p className="text-sm text-slate-400">Click “New Project” to get started.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p) => (
              <Link
                key={p.id}
                href={`/projects/${p.id}`}
                className="group rounded-xl border border-border bg-surface p-5 transition hover:border-accent hover:bg-surface2"
              >
                <div className="mb-3 flex items-start justify-between">
                  <h3 className="font-semibold group-hover:text-accent">{p.title}</h3>
                  <button
                    onClick={(e) => handleDelete(p.id, e)}
                    className="text-slate-600 transition hover:text-red-400"
                    title="Delete"
                  >
                    ✕
                  </button>
                </div>
                <span
                  className={`inline-block rounded-full px-2.5 py-1 text-xs font-medium ${
                    STATUS_STYLE[p.status] || STATUS_STYLE.created
                  }`}
                >
                  {p.status}
                </span>
                <div className="mt-3 flex flex-wrap gap-1">
                  {(p.output_formats || []).map((f) => (
                    <span key={f} className="rounded bg-surface2 px-2 py-0.5 text-[10px] text-slate-400">
                      {FORMAT_META[f]?.label || f}
                    </span>
                  ))}
                </div>
                <p className="mt-3 text-xs text-slate-500">
                  {new Date(p.created_at).toLocaleString()}
                </p>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
