import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type CohortInfo, type Detection, type ImportReport } from "../api";
import { Alert, Card, ChecksList, Spinner, Tabs, toast, useAsync } from "../components/ui";

const STEPS = [
  ["Throw data in", "CSV, Parquet, a MIMIC-IV directory or FHIR bundles. DEA detects the layout, the time unit and the units of every variable."],
  ["Check and harmonize", "Mixed units, implausible values, duplicates and site differences are caught before anything is written. LOINC, UCUM and ICD codes come for free."],
  ["Look, build, export", "Profiles, trajectories and per-site views out of the box. Build sub-cohorts with a click, then take a Polars or pandas frame to your models, or FHIR to your colleagues."],
];

export function Cohorts() {
  const nav = useNavigate();
  const { data, error, loading, reload } = useAsync(() => api.cohorts(), []);
  const [tab, setTab] = useState("Import");
  const [query, setQuery] = useState("");
  const tree = useMemo(() => buildTree(data ?? [], query), [data, query]);
  const totals = useMemo(() => ({
    cohorts: data?.length ?? 0,
    encounters: (data ?? []).reduce((a, c) => a + c.n_encounters, 0),
    warnings: (data ?? []).reduce((a, c) => a + c.n_warnings, 0),
  }), [data]);
  return (
    <>
      <section className="hero">
        <div>
          <div className="eyebrow">Diagnostic Expert Advisor</div>
          <h1 className="hero-title">Bring your cohort. Get structure, checks and views in minutes.</h1>
          <p className="muted hero-sub">A workbench for clinical time series: import anything, harmonize it to standards, see every patient and every site, and hand clean frames to your models. Everything runs locally; the AI advisor only ever sees aggregate statistics.</p>
        </div>
        <div className="hero-stats">
          <div><b>{totals.cohorts}</b><span>cohorts</span></div>
          <div><b>{totals.encounters.toLocaleString()}</b><span>encounters</span></div>
          <div><b>{totals.warnings}</b><span>open findings</span></div>
        </div>
      </section>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(320px, 2fr)", alignItems: "start" }}>
        <div className="stack">
          <div className="row spread">
            <h2 style={{ margin: 0 }}>Your cohorts</h2>
            {data && data.length > 3 && <input placeholder="filter by name…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ width: 220 }} />}
          </div>
          {error && <Alert kind="danger">{error}</Alert>}
          {loading && <Spinner />}
          {data?.length === 0 && (
            <div className="card empty">
              <h2>No cohorts yet</h2>
              <p className="muted">Import a file or folder on the right, or generate a demo cohort to see what DEA does.</p>
              <div className="steps">{STEPS.map(([t, d], i) => <div key={t} className="step"><span className="n">{i + 1}</span><div><b>{t}</b><div className="small muted">{d}</div></div></div>)}</div>
            </div>
          )}
          <div className="cohort-grid">
            {tree.map(({ c, depth }) => <CohortCard key={c.name} c={c} depth={depth} onOpen={() => nav(`/c/${c.name}`)} onDelete={async () => {
              if (!confirm(`Delete cohort “${c.title}” and all its files?`)) return;
              await api.deleteCohort(c.name); toast("Cohort deleted"); reload();
            }} />)}
          </div>
          {data && data.length > 0 && (
            <div className="steps compact">{STEPS.map(([t, d], i) => <div key={t} className="step"><span className="n">{i + 1}</span><div><b>{t}</b><div className="small muted">{d}</div></div></div>)}</div>
          )}
        </div>
        <Card title="Add data">
          <Tabs tabs={["Import", "Demo"]} value={tab} onChange={setTab} />
          {tab === "Demo" ? <DemoForm onDone={(n) => nav(`/c/${n}`)} /> : <ImportForm onDone={(n) => nav(`/c/${n}/profile`)} />}
        </Card>
      </div>
    </>
  );
}

function buildTree(list: CohortInfo[], query: string): { c: CohortInfo; depth: number }[] {
  const q = query.trim().toLowerCase();
  const byName = new Map(list.map((c) => [c.name, c]));
  const children = new Map<string | null, CohortInfo[]>();
  for (const c of list) {
    const parent = c.parent ? c.parent.split("/").pop()! : null;
    const key = parent && byName.has(parent) ? parent : null;
    (children.get(key) ?? children.set(key, []).get(key)!).push(c);
  }
  const out: { c: CohortInfo; depth: number }[] = [];
  const walk = (key: string | null, depth: number) => {
    for (const c of (children.get(key) ?? []).sort((a, b) => b.created_at.localeCompare(a.created_at))) {
      if (!q || c.title.toLowerCase().includes(q) || c.description.toLowerCase().includes(q)) out.push({ c, depth });
      walk(c.name, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

function Spark({ bins }: { bins: number[] | null }) {
  if (!bins || bins.length === 0) return <div className="spark empty" title="Length-of-stay distribution appears after the first profile" />;
  const max = Math.max(...bins, 1);
  return (
    <div className="spark" title="Length of stay, 0–30 days">
      {bins.map((b, i) => <span key={i} style={{ height: `${Math.max(8, (b / max) * 100)}%` }} />)}
    </div>
  );
}

function CohortCard({ c, depth, onOpen, onDelete }: { c: CohortInfo; depth: number; onOpen: () => void; onDelete: () => void }) {
  const when = new Date(c.created_at);
  return (
    <div className="card cohort-card" style={{ marginLeft: depth * 22 }} onClick={onOpen} role="link" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen()}>
      <div className="cohort-head">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            {depth > 0 && <span className="muted">↳</span>}
            <b className="cohort-title">{c.title}</b>
            {c.format && <span className="chip">{c.format}</span>}
            {c.site_column && <span className="chip on" title={`site column ${c.site_column}`}>multi-site</span>}
            {c.n_warnings > 0 && <span className="chip warn">{c.n_warnings} finding{c.n_warnings > 1 ? "s" : ""}</span>}
          </div>
          <div className="small muted ellipsis">{c.derived_by ? <code>{c.derived_by}</code> : (c.description || c.source || "—")}</div>
        </div>
        <button className="ghost sm" title="Delete cohort" onClick={(e) => { e.stopPropagation(); onDelete(); }}>×</button>
      </div>
      <div className="cohort-body">
        <div className="cohort-kpis">
          <div><b>{c.n_encounters.toLocaleString()}</b><span>encounters</span></div>
          <div><b>{c.n_variables}</b><span>variables</span></div>
          <div><b>{c.n_tags}</b><span>tags</span></div>
        </div>
        <Spark bins={c.los_hist} />
      </div>
      <div className="small muted cohort-foot">{when.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}{c.n_filters ? ` · ${c.n_filters} saved filter${c.n_filters > 1 ? "s" : ""}` : ""}</div>
    </div>
  );
}

function DemoForm({ onDone }: { onDone: (name: string) => void }) {
  const [name, setName] = useState("demo");
  const [n, setN] = useState(60);
  const [mimic, setMimic] = useState(false);
  const [sites, setSites] = useState(1);
  const [busy, setBusy] = useState(false);
  return (
    <div className="stack">
      <p className="muted small">A synthetic ICU cohort with realistic sampling rates, events and notes. No real patients.</p>
      <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Encounters<input type="number" min={5} max={5000} value={n} onChange={(e) => setN(Number(e.target.value))} /></label>
      <label className="row"><input type="checkbox" checked={mimic} onChange={(e) => setMimic(e.target.checked)} /> MIMIC-IV table layout (exercise the MIMIC importer)</label>
      {!mimic && <label className="field">Hospitals<select value={sites} onChange={(e) => setSites(Number(e.target.value))}>
        <option value={1}>one site</option><option value={2}>two sites (B charts °F, reads MAP high)</option><option value={3}>three sites (C exports 5-minute vitals, no lactate/CRP)</option>
      </select></label>}
      <button className="primary" disabled={busy || !name} onClick={async () => {
        setBusy(true);
        try { await api.demo({ name, n, mimic, sites: mimic ? 1 : sites }); toast("Demo cohort created"); onDone(name); }
        catch (e) { toast(String((e as Error).message)); } finally { setBusy(false); }
      }}>{busy ? <Spinner label="Generating…" /> : "Generate"}</button>
    </div>
  );
}

function ImportPreview({ report }: { report: ImportReport }) {
  return (
    <div className="stack" style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      <div className="row">
        <span className="chip on">{report.format}</span>
        <span className="chip">{report.n_encounters} encounters</span>
        <span className="chip">{report.n_variables} variables</span>
        <span className="chip">{report.n_observations.toLocaleString()} observations</span>
        {report.n_events > 0 && <span className="chip">{report.n_events.toLocaleString()} events</span>}
      </div>
      {report.warnings.map((w) => <Alert key={w}>{w}</Alert>)}
      {report.unit_reports.length > 0 && (
        <div>
          <b className="small">Units harmonized</b>
          <ul className="small muted" style={{ margin: "4px 0 0 16px" }}>
            {report.unit_reports.map((u) => (
              <li key={u.variable}>
                <b>{u.variable}</b> → {u.label || u.canonical}
                {Object.entries(u.converted).map(([k, n]) => ` · ${n.toLocaleString()} rows from ${k}`)}
                {Object.entries(u.unconvertible).map(([k, n]) => ` · ${n.toLocaleString()} rows in ${k} not convertible`)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div>
        <b className="small">Checks</b>
        <ChecksList checks={report.checks} empty="No findings. Nothing in the data stood out." />
      </div>
      <p className="muted small">Nothing has been written yet. Import to save the cohort; fixes are offered on its profile page.</p>
    </div>
  );
}

function ImportForm({ onDone }: { onDone: (name: string) => void }) {
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [det, setDet] = useState<Detection | null>(null);
  const [staged, setStaged] = useState<string | null>(null);
  const [ov, setOv] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<ImportReport | null>(null);

  const detect = async (p: string) => {
    setBusy("Inspecting…"); setErr(null);
    try { const d = await api.detect(p); setDet(d); setStaged(p); setOv({}); if (!name) setName(p.split("/").pop()?.replace(/\.[^.]+$/, "") ?? ""); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const upload = async (file: File) => {
    setBusy("Uploading…"); setErr(null);
    try { const r = await api.upload(file); setDet(r.detection); setStaged(r.staged); setOv({}); if (!name) setName(r.name); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const cols = det?.columns ?? [];
  const kind = ov.table_kind ?? det?.table_kind ?? "wide";
  const Sel = ({ k, label }: { k: string; label: string }) => (
    <label className="field">{label}
      <select value={ov[k] ?? (det as unknown as Record<string, string>)?.[k] ?? ""} onChange={(e) => setOv({ ...ov, [k]: e.target.value })}>
        {cols.map((c) => <option key={c}>{c}</option>)}
      </select>
    </label>
  );
  return (
    <div className="stack">
      <p className="muted small">CSV, TSV or Parquet tables (long or wide), a zip of the v1 CSV tree, a MIMIC-IV directory, or FHIR R4 resources (NDJSON or Bundle).</p>
      <div className="row">
        <input style={{ flex: 1 }} placeholder="/path/on/this/machine (file or folder)" value={path} onChange={(e) => setPath(e.target.value)} />
        <button onClick={() => path && detect(path)} disabled={!path}>Inspect</button>
      </div>
      <label className="row small muted">or upload a file <input type="file" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
      {busy && <Spinner label={busy} />}
      {err && <Alert kind="danger">{err}</Alert>}
      {det && (
        <div className="stack">
          <div className="row"><span className="chip on">{det.format}{det.table_kind ? ` / ${kind}` : ""}</span>{det.notes.map((n) => <span key={n} className="chip warn">{n}</span>)}</div>
          {det.format === "table" && (
            <>
              <div className="grid c2">
                <Sel k="encounter_col" label="Encounter id column" />
                <Sel k="time_col" label="Time column" />
                <label className="field">Time unit
                  <select value={ov.time_unit ?? det.time_unit} onChange={(e) => setOv({ ...ov, time_unit: e.target.value })}>
                    {["auto", "datetime", "s", "ms", "m", "h", "d"].map((u) => <option key={u}>{u}</option>)}
                  </select>
                </label>
                <label className="field">Shape
                  <select value={kind} onChange={(e) => setOv({ ...ov, table_kind: e.target.value })}><option value="long">long (one row per observation)</option><option value="wide">wide (one column per variable)</option></select>
                </label>
                {kind === "long" && <><Sel k="variable_col" label="Variable column" /><Sel k="value_col" label="Value column" />
                  <label className="field">Unit column (optional)
                    <select value={ov.unit_col ?? det.unit_col ?? ""} onChange={(e) => setOv({ ...ov, unit_col: e.target.value })}>
                      <option value="">none</option>{cols.map((c) => <option key={c}>{c}</option>)}
                    </select>
                  </label></>}
              </div>
              {det.preview.length > 0 && (
                <div className="table-wrap" style={{ maxHeight: 200 }}>
                  <table className="table"><thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                    <tbody>{det.preview.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c] ?? "")}</td>)}</tr>)}</tbody></table>
                </div>
              )}
            </>
          )}
          <label className="field">Cohort name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <div className="row">
            <button disabled={!name || det.format === "unknown" || !!busy} onClick={async () => {
              setBusy("Checking…"); setErr(null); setPreview(null);
              try { setPreview((await api.preview({ path: staged!, name, overrides: ov })).report); }
              catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
            }}>Check first</button>
            <button className="primary" disabled={!name || det.format === "unknown" || !!busy} onClick={async () => {
              setBusy("Importing…"); setErr(null);
              try {
                const r = await api.import({ path: staged!, name, overrides: ov });
                const rep = r.report;
                const n = rep.checks.filter((c) => c.severity !== "info").length;
                toast(`Imported ${rep.n_encounters} encounters, ${rep.n_variables} variables, ${rep.n_observations.toLocaleString()} observations${n ? ` · ${n} check(s) to review` : ""}`);
                onDone(r.name);
              } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
            }}>Import</button>
          </div>
          {preview && <ImportPreview report={preview} />}
        </div>
      )}
    </div>
  );
}
