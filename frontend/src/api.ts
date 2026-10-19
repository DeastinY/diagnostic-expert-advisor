import { currentTheme } from "./theme";
// Thin typed client for the DEA backend. Filters travel as repeated `where` params.
export type Where = string[];

export interface CohortInfo {
  name: string; title: string; n_encounters: number; n_variables: number; description: string;
  parent: string | null; derived_by: string | null; created_at: string; source: string;
  format: string | null; site_column: string | null; n_tags: number; n_filters: number; n_warnings: number;
  los_hist: number[] | null;
}
export interface Rule { label: string; where: string; description: string }
export interface Processing {
  resample_every: string | null; aggregate: string; fill: string;
  fill_overrides: Record<string, string>; drop_variables: string[];
  pre_admission_lead_hours: number;
}
export interface CohortConfig {
  name: string; description: string; filters: Rule[]; tags: Rule[]; plots: string[];
  variable_labels: Record<string, string>; variable_units: Record<string, string>;
  processing: Processing; parent: string | null; derived_by: string | null; variable_map: Record<string, string>;
  site_column: string | null;
}
export interface CohortDetail {
  name: string; config: CohortConfig; n_total: number;
  summary: { n_encounters: number; n_variables: number; n_observations: number; los_hours_median: number;
    los_hours_max: number; median_stay: string; longest_stay: string };
  variables: string[]; event_variables: string[]; interval_variables: string[]; static_columns: string[]; derived: string[];
  defaults: { static_column: string | null; variable: string | null };
}
export interface CatalogEntry {
  name: string; source: "static" | "stay" | "aggregate"; label: string; kind: "numeric" | "categorical" | "boolean" | "temporal";
  min?: number | null; max?: number | null; categories?: string[];
}
export interface Suggestion {
  id: string; kind: string; title: string; detail: string; action: Record<string, unknown> | null;
  confidence: number; variables: string[]; applicable: boolean; severity: "info" | "warning" | "error" | null;
}
export interface Check {
  id: string; severity: "info" | "warning" | "error"; title: string; detail: string; variables: string[];
  action: Record<string, unknown> | null; n: number; recommended: boolean;
  options?: { id: string; label: string; detail: string; action: Record<string, unknown> }[] | null;
  examples?: Record<string, unknown> | null;
}
export interface SiteComparison {
  column: string | null; candidates: string[]; configured: string | null;
  sizes?: { n: number; [k: string]: unknown }[];
  shown?: { sites: number; of: number; overlay_sites: number; variables: number; of_variables: number };
  profile: { site: string; variable: string; n_encounters: number; coverage: number; n_obs: number; median: number | null; p25: number | null; p75: number | null; interval_s: number | null }[];
  casemix?: Record<string, number | string | null>[];
  checks: Check[];
  charts: { distributions?: object | null; coverage?: object | null; sampling?: object | null; los?: object | null };
}
export interface UnitReport { variable: string; canonical: string; label: string; seen: Record<string, number>; converted: Record<string, number>; unconvertible: Record<string, number>; mixed: boolean; split: boolean; skipped: boolean }
export interface ImportReport {
  source: string; format: string; n_encounters: number; n_variables: number; n_observations: number; n_events: number;
  mapping: Record<string, string>; event_columns: string[]; dropped_columns: string[]; empty_columns?: string[]; warnings: string[];
  unit_reports: UnitReport[]; checks: Check[]; applied: string[]; n_intervals?: number;
  summary: { error: number; warning: number; info: number; fixable: number; recommended: number; units_converted: number; units_split: number; units_skipped: number };
}
export interface EncounterPage {
  rows: Record<string, unknown>[]; total: number; page: number; size: number; columns: string[]; tags: string[];
}
export interface EncounterDetail {
  id: string; static: Record<string, unknown>; los_hours: number; n_observations: number; variables: string[];
  events: { hours: number; variable: string; value: string }[]; plots: string[];
  intervals: { start_h: number; end_h: number; variable: string; value: number }[];
}
export interface Recommendation {
  summary: string; resample_every: string; aggregate: string; default_fill: string;
  variables: { variable: string; label: string; canonical_name: string; role: string; unit_guess: string; imputation: string; rationale: string }[];
  suggested_filters: { label: string; where: string; description: string }[];
  plot_variables: string[]; storage_notes: string[]; warnings: string[];
}
export interface Detection {
  format: string; table_kind: string | null; encounter_col: string | null; time_col: string | null;
  variable_col: string | null; value_col: string | null; unit_col: string | null; time_unit: string; columns: string[];
  dtypes: Record<string, string>; notes: string[]; preview: Record<string, unknown>[];
  // a folder of one table per encounter, or what a folder holds when nothing was recognized
  n_files: number; files: string[]; pattern: string | null; static_file: string | null; static_id_col: string | null;
  static_columns: string[]; static_candidates: string[]; tables: string[]; ignored: string[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

function token(): string | null {
  try { return localStorage.getItem("dea_token"); } catch { return null; }
}
export function authHeaders(): Record<string, string> {
  const t = token();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) } });
  if (res.status === 401 && typeof window !== "undefined") {
    const t = window.prompt("This DEA instance needs an access token:");
    if (t) { try { localStorage.setItem("dea_token", t); } catch { /* ignore */ } return request<T>(path, init); }
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* keep status text */ }
    throw new ApiError(res.status, String(detail));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function qs(params: Record<string, string | number | boolean | undefined | null>, where: Where = []): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  for (const w of where) p.append("where", w);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export const isDark = () => currentTheme() === "dark";

export interface PanelParamDef { name: string; type: "variable" | "static" | "static_categorical" | "every" | "number" | "bool"; required?: boolean; default?: unknown }
export interface PanelDef { kind: string; label: string; description: string; params: PanelParamDef[]; size: number }
export interface Panel { id: string; kind: string; params: Record<string, unknown>; size: number; title?: string | null }
export interface DashboardData { panels: Panel[]; default: boolean; catalog: PanelDef[] }
export interface PanelChart { spec: object | null; note: string | null }

export interface Health {
  status: string; data_dir: string; auth: boolean; version: string;
  update: { mode: string; version: string; commit: string | null; branch: string | null; latest: string | null;
    update_available: boolean | null; command: string[][] } | null;
}

export interface Hist {
  variable: string; label: string; unit: string; n: number; bins: { lo: number; hi: number; count: number }[]; range: [number, number] | null; concept: string | null;
  after?: { bins: { lo: number; hi: number; count: number }[]; n: number; n_changed: number; n_unchanged: number };
}
export interface Series { variable: string; encounter: string; unit: string; hours: number[]; values: (number | null)[] }
export interface Previous { applied: string[]; declined: string[]; n_encounters: number | null; n_observations: number | null; when: string }
export interface PreviewResult { name: string; report: ImportReport; previous: Previous | null }
export interface Job { id: string; kind: "preview" | "import"; status: "running" | "done" | "error"; stage: string; detail: string; elapsed: number; error: string | null; result?: unknown }
export type ImportBody = { path: string; name?: string; static_path?: string | null; overrides: Record<string, unknown>; fixes?: string[] };

export const api = {
  health: () => request<Health>("/api/health"),
  startJob: (kind: "preview" | "import", body: ImportBody) =>
    request<{ id: string }>(`/api/jobs/${kind}`, { method: "POST", body: JSON.stringify(body) }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  previewHist: (body: ImportBody & { variable: string; where?: string | null; expr?: string | null }) =>
    request<Hist>("/api/cohorts/preview/hist", { method: "POST", body: JSON.stringify(body) }),
  hist: (name: string, variable: string, fix?: { where: string; expr: string | null }) =>
    request<Hist>(`/api/cohorts/${name}/hist${qs({ variable, where: fix?.where, expr: fix ? fix.expr ?? undefined : undefined })}`),
  previewSeries: (body: ImportBody & { variable: string; encounter: string }) =>
    request<Series>("/api/cohorts/preview/series", { method: "POST", body: JSON.stringify(body) }),
  series: (name: string, variable: string, encounter: string) =>
    request<Series>(`/api/cohorts/${name}/series${qs({ variable, encounter })}`),
  applyChecks: (name: string, ids: string[]) =>
    request<{ applied: string[] }>(`/api/cohorts/${name}/checks/apply`, { method: "POST", body: JSON.stringify({ ids }) }),
  cohorts: () => request<CohortInfo[]>("/api/cohorts"),
  dashboard: (name: string) => request<DashboardData>(`/api/cohorts/${name}/dashboard`),
  setDashboard: (name: string, panels: Panel[]) =>
    request<{ panels: Panel[]; default: boolean }>(`/api/cohorts/${name}/dashboard`, { method: "PUT", body: JSON.stringify({ panels }) }),
  panel: (name: string, kind: string, params: Record<string, unknown>, where: Where = []) =>
    request<PanelChart>(`/api/cohorts/${name}/panel${qs({ kind, params: JSON.stringify(params), dark: isDark() }, where)}`),
  cohort: (name: string, where: Where = []) => request<CohortDetail>(`/api/cohorts/${name}${qs({}, where)}`),
  deleteCohort: (name: string) => request<void>(`/api/cohorts/${name}`, { method: "DELETE" }),
  demo: (body: { name: string; n: number; mimic: boolean; sites?: number }) =>
    request<{ name: string }>("/api/cohorts/demo", { method: "POST", body: JSON.stringify(body) }),
  detect: (path: string, overrides: Record<string, unknown> = {}) =>
    request<Detection>("/api/detect", { method: "POST", body: JSON.stringify({ path, overrides }) }),
  import: (body: ImportBody) =>
    request<{ name: string; report: ImportReport }>("/api/cohorts/import", { method: "POST", body: JSON.stringify(body) }),
  preview: (body: ImportBody) =>
    request<PreviewResult>("/api/cohorts/preview", { method: "POST", body: JSON.stringify(body) }),
  checks: (name: string) => request<Check[]>(`/api/cohorts/${name}/checks`),
  sites: (name: string, where: Where, column?: string) =>
    request<SiteComparison>(`/api/cohorts/${name}/sites${qs({ column, dark: isDark() }, where)}`),
  setSite: (name: string, column: string | null) =>
    request<{ site_column: string | null }>(`/api/cohorts/${name}/site`, { method: "PUT", body: JSON.stringify({ column }) }),
  importReport: (name: string) => request<ImportReport | Record<string, never>>(`/api/cohorts/${name}/import`),
  upload: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/cohorts/upload", { method: "POST", body: fd, headers: authHeaders() });
    if (!res.ok) throw new ApiError(res.status, (await res.json()).detail ?? res.statusText);
    return (await res.json()) as { staged: string; name: string; detection: Detection };
  },
  uploadStart: () => request<{ id: string }>("/api/uploads", { method: "POST" }),
  uploadFiles: async (id: string, batch: { file: File; path: string }[]) => {
    const fd = new FormData();
    for (const p of batch) { fd.append("files", p.file, p.file.name); fd.append("paths", p.path); }
    const res = await fetch(`/api/uploads/${id}/files`, { method: "POST", body: fd, headers: authHeaders() });
    if (!res.ok) throw new ApiError(res.status, (await res.json()).detail ?? res.statusText);
    return (await res.json()) as { received: number; bytes: number };
  },
  uploadDone: (id: string, name?: string) =>
    request<{ staged: string; name: string; n_files: number; detection: Detection }>(`/api/uploads/${id}/done${name ? `?name=${encodeURIComponent(name)}` : ""}`, { method: "POST" }),
  count: (name: string, where: Where) => request<{ n: number; n_total: number }>(`/api/cohorts/${name}/count${qs({}, where)}`),
  catalog: (name: string) => request<CatalogEntry[]>(`/api/cohorts/${name}/catalog`),
  addRule: (name: string, body: { kind: "filter" | "tag"; label: string; where: string; description?: string }) =>
    request(`/api/cohorts/${name}/rules`, { method: "POST", body: JSON.stringify(body) }),
  deleteRule: (name: string, kind: "filter" | "tag", label: string) =>
    request<void>(`/api/cohorts/${name}/rules/${kind}/${encodeURIComponent(label)}`, { method: "DELETE" }),
  derive: (name: string, body: { name: string; where: string[]; description?: string }) =>
    request<{ name: string; n_encounters: number }>(`/api/cohorts/${name}/derive`, { method: "POST", body: JSON.stringify(body) }),
  processing: (name: string, body: Partial<Processing>) =>
    request<Processing>(`/api/cohorts/${name}/processing`, { method: "PUT", body: JSON.stringify(body) }),
  overviewCharts: (name: string, where: Where, params: Record<string, string | undefined>) =>
    request<Record<string, object> & { coverage_shown?: { variables: number; of: number }; tags_shown?: { shown: number; of: number } }>(`/api/cohorts/${name}/charts/overview${qs({ ...params, dark: isDark() }, where)}`),
  profileCharts: (name: string, where: Where) =>
    request<{ gallery: object | null; observations?: object }>(`/api/cohorts/${name}/charts/profile${qs({ dark: isDark() }, where)}`),
  trajectory: (name: string, where: Where, params: Record<string, string | number | undefined>) =>
    request<{ spec: object | null; rows: number }>(`/api/cohorts/${name}/charts/trajectory${qs({ ...params, dark: isDark() }, where)}`),
  encounters: (name: string, where: Where, params: Record<string, string | number | boolean | undefined>) =>
    request<EncounterPage>(`/api/cohorts/${name}/encounters${qs(params, where)}`),
  encounter: (name: string, eid: string) => request<EncounterDetail>(`/api/cohorts/${name}/encounters/${encodeURIComponent(eid)}`),
  encounterChart: (name: string, eid: string, variables: string[], every?: string, band = true) => {
    const p = new URLSearchParams();
    variables.forEach((v) => p.append("variables", v));
    if (every) p.set("every", every);
    if (!band) p.set("band", "false");
    if (isDark()) p.set("dark", "true");
    return request<{ spec: object | null }>(`/api/cohorts/${name}/encounters/${encodeURIComponent(eid)}/chart?${p}`);
  },
  profile: (name: string, where: Where) => request<Record<string, any>>(`/api/cohorts/${name}/profile${qs({}, where)}`),  // eslint-disable-line @typescript-eslint/no-explicit-any
  suggestions: (name: string) => request<Suggestion[]>(`/api/cohorts/${name}/suggestions`),
  applySuggestions: (name: string, ids: string[]) =>
    request<{ applied: string[] }>(`/api/cohorts/${name}/suggestions/apply`, { method: "POST", body: JSON.stringify({ ids }) }),
  adviseStatus: (name: string, where: Where) => request<{ installed: boolean; configured: boolean; model: string; base_url: string | null; payload: object }>(`/api/cohorts/${name}/advise${qs({}, where)}`),
  advise: (name: string, where: Where, body: { model?: string; base_url?: string; api_key?: string }) =>
    request<Recommendation>(`/api/cohorts/${name}/advise${qs({}, where)}`, { method: "POST", body: JSON.stringify(body) }),
  adviseMap: (name: string, where: Where, body: { model?: string; base_url?: string; api_key?: string }) =>
    request<{ proposals: { variable: string; concept: string; confidence: number; rationale: string }[]; mapping: Record<string, string>; n_unmatched: number }>(`/api/cohorts/${name}/advise/map${qs({}, where)}`, { method: "POST", body: JSON.stringify(body) }),
  applyMapping: (name: string, body: { path?: string; mapping?: Record<string, string>; units?: Record<string, string> }) =>
    request<{ applied: string[]; unused: string[]; unmapped: string[] }>(`/api/cohorts/${name}/mapping`, { method: "POST", body: JSON.stringify(body) }),
  adviseApply: (name: string, recommendation: Recommendation, rename: boolean) =>
    request<{ variables: string[] }>(`/api/cohorts/${name}/advise/apply`, { method: "POST", body: JSON.stringify({ recommendation, rename }) }),
  entities: (name: string) => request<{ available: boolean; n_entities?: number; summary: Record<string, unknown>[] }>(`/api/cohorts/${name}/entities`),
  reportUrl: (name: string, where: Where) => `/api/cohorts/${name}/report${qs({ token: token() ?? undefined }, where)}`,
  exportUrl: (name: string, where: Where, every?: string, fill?: string) => `/api/cohorts/${name}/export${qs({ every, fill, token: token() ?? undefined }, where)}`,
  fhirExportUrl: (name: string, where: Where) => `/api/cohorts/${name}/export/fhir${qs({ token: token() ?? undefined }, where)}`,
  mlSummary: (name: string, where: Where, params: { label: string; cutoff?: number; split: string; test_size?: number }) =>
    request<{ n: number; n_features: number; positives: number; train: number; test: number; dropped: string[]; features: string[] }>(`/api/cohorts/${name}/ml/summary${qs(params, where)}`),
  mlUrl: (name: string, where: Where, params: { label: string; cutoff?: number; split: string; test_size?: number }) => `/api/cohorts/${name}/ml${qs({ ...params, token: token() ?? undefined }, where)}`,
};

export function useWhereParam(search: URLSearchParams): Where { return search.getAll("where"); }
export function fmt(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return Math.abs(n) >= 1000 ? Math.round(n).toLocaleString() : Number(n).toFixed(digits).replace(/\.0$/, "");
}
export function hours(h: number): string {
  if (h < 48) return `${h.toFixed(1)} h`;
  const d = Math.floor(h / 24); const r = Math.round(h - d * 24);
  return r ? `${d}d ${r}h` : `${d}d`;
}
