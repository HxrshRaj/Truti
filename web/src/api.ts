export interface Project { id: number; name: string; public_key: string; dsn: string; created_at: string }
export interface Issue {
  id: number; project_id: number; title: string; type: string | null; culprit: string | null; level: string;
  status: string; count: number; first_seen: string; last_seen: string; first_release: string | null;
  last_release: string | null; environment: string | null; sparkline?: number[];
}
export interface Frame {
  filename: string; function?: string; lineno?: number; colno?: number; context_line?: string;
  pre_context?: string[]; post_context?: string[]; in_app: boolean;
}
export interface TrutiEvent {
  id: number; event_id: string; timestamp: string; release: string | null; environment: string | null;
  level: string; message?: string; platform: string;
  exception?: { type: string; value: string; frames: Frame[] };
  breadcrumbs?: { timestamp?: string; category?: string; message?: string; level?: string }[];
  tags?: Record<string, string>; user?: Record<string, unknown> | null; request?: Record<string, any> | null;
}
export interface IssueDetail extends Issue { latest_event: TrutiEvent | null }
export interface Rule { id: number; kind: string; channel: "webhook" | "email"; threshold: number; window_seconds: number; webhook_url: string; email_to: string | null; last_fired: string | null }
export interface AlertEntry { id: number; rule_id: number; kind: string; issue_id: number; fired_at: string; delivered: boolean; detail: string }

const LS_URL = "truti.apiUrl";
const LS_TOKEN = "truti.token";

export const config = {
  get apiUrl() { return localStorage.getItem(LS_URL) ?? (import.meta.env.VITE_API_URL as string | undefined) ?? ""; },
  get token() { return localStorage.getItem(LS_TOKEN) ?? ""; },
  clear() { localStorage.removeItem(LS_TOKEN); },
  save(apiUrl: string, token: string) {
    localStorage.setItem(LS_URL, apiUrl.replace(/\/+$/, ""));
    localStorage.setItem(LS_TOKEN, token);
  },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${config.apiUrl}/api/0${path}`, {
    method,
    headers: { Authorization: `Bearer ${config.token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const qs = (o: Record<string, string | number | undefined>) =>
  Object.entries(o).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export const api = {
  projects: () => call<Project[]>("GET", "/projects"),
  createProject: (name: string) => call<Project>("POST", "/projects", { name }),
  issues: (pid: number, f: { query?: string; status?: string; environment?: string; release?: string; sort?: string }) =>
    call<{ total: number; items: Issue[] }>("GET", `/projects/${pid}/issues?${qs(f)}`),
  releases: (pid: number) => call<{ releases: { version: string; first_seen: string; event_count: number }[]; environments: string[] }>("GET", `/projects/${pid}/releases`),
  issue: (id: number) => call<IssueDetail>("GET", `/issues/${id}`),
  issueEvents: (id: number) => call<TrutiEvent[]>("GET", `/issues/${id}/events`),
  setStatus: (id: number, status: string) => call<Issue>("PATCH", `/issues/${id}`, { status }),
  rules: (pid: number) => call<Rule[]>("GET", `/projects/${pid}/alert-rules`),
  createRule: (pid: number, r: { kind: string; channel: string; webhook_url: string; email_to?: string; threshold: number; window_seconds: number }) =>
    call<Rule>("POST", `/projects/${pid}/alert-rules`, r),
  deleteRule: (id: number) => call<void>("DELETE", `/alert-rules/${id}`),
  mergeIssue: (id: number, into: number) => call<Issue>("POST", `/issues/${id}/merge`, { into }),
  alerts: (pid: number) => call<AlertEntry[]>("GET", `/projects/${pid}/alerts`),
};

/** Email/password sign-in. Returns the session token; the caller stores it with config.save. */
export async function authenticate(apiUrl: string, mode: "login" | "register", email: string, password: string): Promise<string> {
  const res = await fetch(`${apiUrl.replace(/\/+$/, "")}/api/0/auth/${mode}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }),
  });
  const j = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = j.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join("; ") : res.statusText);
  }
  return j.token as string;
}
