// Thin typed client for the DEA backend. Filters travel as repeated `where` params.
export type Where = string[];

export interface CohortInfo {
  name: string; title: string; n_encounters: number; description: string;
  parent: string | null; derived_by: string | null; created_at: string; source: string;
}
export interface Rule { label: string; where: string; description: string }
export interface Processing {
  resample_every: string | null; aggregate: string; fill: string;
  fill_overrides: Record<string, string>; drop_variables: string[];
}
export interface CohortConfig {
  name: string; description: string; filters: Rule[]; tags: Rule[]; plots: string[];
  variable_labels: Record<string, string>; variable_units: Record<string, string>;
  processing: Processing; parent: string | null; derived_by: string | null; variable_map: Record<string, string>;
}
export interface CohortDetail {
  name: string; config: CohortConfig; n_total: number;
  summary: { n_encounters: number; n_variables: number; n_observations: number; los_hours_median: number;
    los_hours_max: number; median_stay: string; longest_stay: string };
  variables: string[]; event_variables: string[]; static_columns: string[]; derived: string[];
}
export interface CatalogEntry {
  name: string; source: "static" | "stay" | "aggregate"; label: string; kind: "numeric" | "categorical" | "boolean" | "temporal";
  min?: number | null; max?: number | null; categories?: string[];
}
export interface Suggestion {
  id: string; kind: string; title: string; detail: string; action: Record<string, unknown> | null;
  confidence: number; variables: string[]; applicable: boolean;
}
export interface EncounterPage {
  rows: Record<string, unknown>[]; total: number; page: number; size: number; columns: string[]; tags: string[];
}
export interface EncounterDetail {
  id: string; static: Record<string, unknown>; los_hours: number; n_observations: number; variables: string[];
  events: { hours: number; variable: string; value: string }[]; plots: string[];
}
export interface Recommendation {
  summary: string; resample_every: string; aggregate: string; default_fill: string;
  variables: { variable: string; label: string; canonical_name: string; role: string; unit_guess: string; imputation: string; rationale: string }[];
  suggested_filters: { label: string; where: string; description: string }[];
  plot_variables: string[]; storage_notes: string[]; warnings: string[];
}
export interface Detection {
  format: string; table_kind: string | null; encounter_col: string | null; time_col: string | null;
  variable_col: string | null; value_col: string | null; time_unit: string; columns: string[];
  dtypes: Record<string, string>; notes: string[]; preview: Record<string, unknown>[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
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

export const isDark = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;

export const api = {
  cohorts: () => request<CohortInfo[]>("/api/cohorts"),
  cohort: (name: string, where: Where = []) => request<CohortDetail>(`/api/cohorts/${name}${qs({}, where)}`),
  deleteCohort: (name: string) => request<void>(`/api/cohorts/${name}`, { method: "DELETE" }),
  demo: (body: { name: string; n: number; mimic: boolean }) =>
    request<{ name: string }>("/api/cohorts/demo", { method: "POST", body: JSON.stringify(body) }),
  detect: (path: string) => request<Detection>("/api/detect", { method: "POST", body: JSON.stringify({ path }) }),
  import: (body: { path: string; name?: string; overrides: Record<string, unknown> }) =>
    request<{ name: string; report: Record<string, unknown> }>("/api/cohorts/import", { method: "POST", body: JSON.stringify(body) }),
  upload: async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/cohorts/upload", { method: "POST", body: fd });
    if (!res.ok) throw new ApiError(res.status, (await res.json()).detail ?? res.statusText);
    return (await res.json()) as { staged: string; name: string; detection: Detection };
  },
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
    request<Record<string, object>>(`/api/cohorts/${name}/charts/overview${qs({ ...params, dark: isDark() }, where)}`),
  profileCharts: (name: string, where: Where) =>
    request<{ gallery: object | null; observations?: object }>(`/api/cohorts/${name}/charts/profile${qs({ dark: isDark() }, where)}`),
  trajectory: (name: string, where: Where, params: Record<string, string | number | undefined>) =>
    request<{ spec: object | null; rows: number }>(`/api/cohorts/${name}/charts/trajectory${qs({ ...params, dark: isDark() }, where)}`),
  encounters: (name: string, where: Where, params: Record<string, string | number | boolean | undefined>) =>
    request<EncounterPage>(`/api/cohorts/${name}/encounters${qs(params, where)}`),
  encounter: (name: string, eid: string) => request<EncounterDetail>(`/api/cohorts/${name}/encounters/${encodeURIComponent(eid)}`),
  encounterChart: (name: string, eid: string, variables: string[], every?: string) => {
    const p = new URLSearchParams();
    variables.forEach((v) => p.append("variables", v));
    if (every) p.set("every", every);
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
  adviseApply: (name: string, recommendation: Recommendation, rename: boolean) =>
    request<{ variables: string[] }>(`/api/cohorts/${name}/advise/apply`, { method: "POST", body: JSON.stringify({ recommendation, rename }) }),
  entities: (name: string) => request<{ available: boolean; n_entities?: number; summary: Record<string, unknown>[] }>(`/api/cohorts/${name}/entities`),
  reportUrl: (name: string, where: Where) => `/api/cohorts/${name}/report${qs({}, where)}`,
  exportUrl: (name: string, where: Where, every?: string, fill?: string) => `/api/cohorts/${name}/export${qs({ every, fill }, where)}`,
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
