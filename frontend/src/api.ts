/* Thin typed client for the backend API. */

export type Json = any; // eslint-disable-line @typescript-eslint/no-explicit-any

export interface Option { label: string; path: string; value: Json; note?: string | null }
export interface Question { id: string; path: string; question: string; severity: string; options: Option[]; free_text_hint?: string | null }
export interface Section { key: string; title: string; lines: string[]; paths: string[]; missing: boolean; changed: boolean }
export interface Proposal { path: string; label: string; value: Json; quote: string | null; status: string; reason: string | null }
export interface Interpretation { engine: string; model: string | null; proposals: Proposal[]; questions: string[]; notes: string[];
  unsupported: { text: string; reason: string; acknowledged?: boolean }[]; error: string | null }
export interface Issue { id: number; scope: string; ref: string; severity: string; code: string; message: string; details: Json }
export interface Candle { time: number; open: number; high: number; low: number; close: number; i: number; in_session: boolean }
export interface ChartData { timezone: string; candles: Candle[]; indicators: { id: string; label: string; overlay: boolean; points: { time: number; value?: number }[] }[]; rolls: { time: number; from: string; to: string }[] }
export interface Overlay { n: number; direction: string; signal_time: number; entry_time: number; exit_time: number; planned_entry: number;
  entry_price: number; stop: number; target: number | null; exit_price: number; exit_reason: string; order_level: number | null;
  stop_history: { time: number; stop: number; why: string }[] }
export interface Trade { [k: string]: Json }
export interface Example { label: string; trade: Trade; chart: ChartData; overlay: Overlay }
export interface Preview { ok: boolean; missing?: string[]; message?: string | null; source?: string; spec_hash?: string; notes?: string[];
  examples?: Record<string, Example | null>; scanned?: Json; skipped_sample?: Json[] }
export interface Gate { can_approve: boolean; reasons: string[]; blocking_issues: Issue[]; open_unsupported: Json[] }
export interface StrategyState {
  strategy: { id: number; name: string; created_at: string };
  version: { version: number; spec_hash: string; parent_version: number | null; source: string; created_at: string };
  is_latest: boolean; spec: Json; summary: Section[]; changes: { path: string; before: Json; after: Json }[];
  questions: Question[]; interpretation: Interpretation | null;
  messages: { role: string; content: string; version: number; meta: Json; created_at: string }[];
  versions: { version: number; spec_hash: string; source: string; created_at: string; changes: number; approved: boolean }[];
  preview_requirements: string[]; preview: Preview | null;
  assumptions: { rows: { label: string; value: string; missing: boolean }[]; limitations: string[]; data_issues: Issue[] };
  gate: Gate; approval: { id: number; approved_at: string; spec_hash: string } | null;
  runs: { id: number; status: string; version: number; created_at: string; job_id: string }[];
}
export interface Job { id: string; kind: string; status: string; progress: number; message: string; log: string[]; result: Json }

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  let data: Json = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!r.ok) throw new Error((data && (data.detail?.toString?.() ?? JSON.stringify(data))) || `HTTP ${r.status}`);
  return data as T;
}

export const api = {
  health: () => call<Json>("GET", "/api/health"),
  files: () => call<{ files: Json[] }>("GET", "/api/data/files"),
  scan: () => call<{ files: Json[] }>("POST", "/api/data/scan"),
  ingest: (reingest = false) => call<{ job_id: string; files: number }>("POST", "/api/data/ingest", { reingest }),
  coverage: () => call<{ coverage: Json }>("GET", "/api/data/coverage"),
  issues: () => call<{ issues: Issue[] }>("GET", "/api/data/issues"),
  job: (id: string) => call<Job>("GET", `/api/jobs/${id}`),
  strategies: () => call<{ items: { id: number; name: string; created_at: string; latest: number }[] }>("GET", "/api/strategies"),
  create: (text: string) => call<StrategyState>("POST", "/api/strategies", { text }),
  get: (id: number, version?: number) => call<StrategyState>("GET", `/api/strategies/${id}${version ? `?version=${version}` : ""}`),
  message: (id: number, text: string) => call<StrategyState>("POST", `/api/strategies/${id}/messages`, { text }),
  patch: (id: number, ops: { path: string; value: Json }[], note?: string) => call<StrategyState>("POST", `/api/strategies/${id}/patch`, { ops, note }),
  ack: (id: number, v: number, idx: number) => call<StrategyState>("POST", `/api/strategies/${id}/versions/${v}/unsupported/${idx}/ack`),
  preview: (id: number, v: number) => call<StrategyState>("POST", `/api/strategies/${id}/versions/${v}/preview`),
  approve: (id: number, v: number, spec_hash: string) => call<StrategyState>("POST", `/api/strategies/${id}/versions/${v}/approve`, { spec_hash }),
  run: (id: number, v: number, spec_hash: string) => call<{ run_id: number; job_id: string }>("POST", `/api/strategies/${id}/versions/${v}/runs`, { spec_hash }),
  getRun: (id: number) => call<Json>("GET", `/api/runs/${id}`),
  trades: (id: number, p: { q?: string; direction?: string; outcome?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
    return call<{ total: number; items: Trade[] }>("GET", `/api/runs/${id}/trades?${qs}`);
  },
  trade: (id: number, n: number) => call<Json>("GET", `/api/runs/${id}/trades/${n}`),
};

export const money = (x: number | null | undefined) =>
  x === null || x === undefined ? "—" : `${x < 0 ? "−" : ""}$${Math.abs(x).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const num = (x: number | null | undefined, d = 2) => (x === null || x === undefined ? "—" : x.toLocaleString(undefined, { maximumFractionDigits: d }));
export const pct = (x: number | null | undefined) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(1)}%`);
export const shortHash = (h?: string) => (h ? h.slice(0, 12) : "");
