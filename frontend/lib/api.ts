export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Token management ──────────────────────────────────────────────────────────
const TOKEN_KEY = "av_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ── Types ─────────────────────────────────────────────────────────────────────
export interface HealthStatus {
  status: string;
  version: string;
  db_ready: boolean;
}
export interface Project {
  id: string;
  title: string;
  status: string;
  target_duration_sec: number | null;
  output_formats: string[] | null;
  created_at: string;
  completed_at: string | null;
  error_message: string | null;
}
export interface Clip {
  id: string;
  filename: string;
  original_filename: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  file_size_bytes: number | null;
  upload_order: number;
  is_ingested: boolean;
}
export interface AgentTaskStatus {
  agent: string;
  status: string;
  progress_pct: number | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}
export interface PipelineStatus {
  project_id: string;
  project_status: string;
  agents: AgentTaskStatus[];
}
export interface StoryEntry {
  position: number;
  narrative_role: string;
  segment_id: string | null;
  trim_start_ms: number | null;
  trim_end_ms: number | null;
  transition_in: string;
  edit_reasoning: string | null;
}
export interface Output {
  id: string;
  format: string;
  aspect_ratio: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  file_size_bytes: number | null;
  quality_score: number | null;
  download_url: string | null;
}

// ── Health ──────────────────────────────────────────────────────────────────
export async function getHealth(): Promise<HealthStatus | null> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as HealthStatus;
  } catch {
    return null;
  }
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export async function register(email: string, password: string, fullName?: string) {
  const res = await fetch(`${API_URL}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, full_name: fullName }),
  });
  return handle<{ access_token: string }>(res);
}
export async function login(email: string, password: string) {
  const res = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handle<{ access_token: string }>(res);
}

// ── Projects ──────────────────────────────────────────────────────────────────
export async function listProjects() {
  const res = await fetch(`${API_URL}/api/v1/projects`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Project[]>(res);
}
export async function getProject(id: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${id}`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Project>(res);
}
export async function createProject(title: string, outputFormats: string[]) {
  const res = await fetch(`${API_URL}/api/v1/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ title, output_formats: outputFormats }),
  });
  return handle<Project>(res);
}
export async function deleteProject(id: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return handle<void>(res);
}

// ── Uploads ─────────────────────────────────────────────────────────────────
export async function uploadClip(projectId: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/uploads/local`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  return handle<Clip>(res);
}
export async function listClips(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/uploads`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Clip[]>(res);
}

// ── Processing ──────────────────────────────────────────────────────────────
export async function startProcessing(projectId: string, outputFormats?: string[]) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(outputFormats ? { output_formats: outputFormats } : {}),
  });
  return handle<{ project_id: string; celery_task_id: string; message: string }>(res);
}
export async function getPipeline(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/pipeline`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<PipelineStatus>(res);
}
export async function getStory(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/story`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<StoryEntry[]>(res);
}

// ── Outputs ───────────────────────────────────────────────────────────────────
export async function listOutputs(projectId: string) {
  const res = await fetch(`${API_URL}/api/v1/projects/${projectId}/outputs`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handle<Output[]>(res);
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
export function openPipelineSocket(projectId: string): WebSocket | null {
  const token = getToken();
  if (!token) return null;
  const wsBase = API_URL.replace(/^http/, "ws");
  return new WebSocket(`${wsBase}/ws/projects/${projectId}?token=${token}`);
}
