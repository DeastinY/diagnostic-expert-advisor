import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Detection } from "../api";
import { Alert, Card, Spinner, Tabs, toast, useAsync } from "../components/ui";

export function Cohorts() {
  const nav = useNavigate();
  const { data, error, loading, reload } = useAsync(() => api.cohorts(), []);
  const [tab, setTab] = useState("Import");
  return (
    <>
      <div className="topbar">
        <div><h1>Diagnostic Expert Advisor</h1><p className="muted">Bring your cohort. Get structure, profiles and views in minutes.</p></div>
      </div>
      <div className="grid" style={{ gridTemplateColumns: "3fr 2fr" }}>
        <div className="stack">
          <h2>Your cohorts</h2>
          {error && <Alert kind="danger">{error}</Alert>}
          {loading && <Spinner />}
          {data?.length === 0 && <div className="card empty"><h2>No cohorts yet</h2><p>Import data or create a demo cohort on the right.</p></div>}
          {data?.map((c) => (
            <div key={c.name} className="card cohort-card" onClick={() => nav(`/c/${c.name}`)}>
              <div>
                <div className="row"><b>{c.title}</b>{c.parent && <span className="chip">sub-cohort of {c.parent.split("/").pop()}</span>}</div>
                <div className="small muted">{c.description || c.source}</div>
                {c.derived_by && <div className="small"><code>{c.derived_by}</code></div>}
              </div>
              <div className="row">
                <div className="kpi"><div className="v" style={{ fontSize: 20 }}>{c.n_encounters.toLocaleString()}</div><div className="l">encounters</div></div>
                <button className="danger sm" onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete cohort “${c.title}” and all its files?`)) return;
                  await api.deleteCohort(c.name); toast("Cohort deleted"); reload();
                }}>Delete</button>
              </div>
            </div>
          ))}
        </div>
        <Card title="Add data">
          <Tabs tabs={["Import", "Demo"]} value={tab} onChange={setTab} />
          {tab === "Demo" ? <DemoForm onDone={(n) => nav(`/c/${n}`)} /> : <ImportForm onDone={(n) => nav(`/c/${n}/profile`)} />}
        </Card>
      </div>
    </>
  );
}

function DemoForm({ onDone }: { onDone: (name: string) => void }) {
  const [name, setName] = useState("demo");
  const [n, setN] = useState(60);
  const [mimic, setMimic] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <div className="stack">
      <p className="muted small">A synthetic ICU cohort with realistic sampling rates, events and notes. No real patients.</p>
      <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Encounters<input type="number" min={5} max={5000} value={n} onChange={(e) => setN(Number(e.target.value))} /></label>
      <label className="row"><input type="checkbox" checked={mimic} onChange={(e) => setMimic(e.target.checked)} /> MIMIC-IV table layout (exercise the MIMIC importer)</label>
      <button className="primary" disabled={busy || !name} onClick={async () => {
        setBusy(true);
        try { await api.demo({ name, n, mimic }); toast("Demo cohort created"); onDone(name); }
        catch (e) { toast(String((e as Error).message)); } finally { setBusy(false); }
      }}>{busy ? <Spinner label="Generating…" /> : "Generate"}</button>
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
      <p className="muted small">CSV, TSV or Parquet tables (long or wide), a zip of the v1 CSV tree, or a MIMIC-IV directory.</p>
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
                {kind === "long" && <><Sel k="variable_col" label="Variable column" /><Sel k="value_col" label="Value column" /></>}
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
          <button className="primary" disabled={!name || det.format === "unknown" || !!busy} onClick={async () => {
            setBusy("Importing…"); setErr(null);
            try {
              const r = await api.import({ path: staged!, name, overrides: ov });
              const rep = r.report as { n_encounters: number; n_variables: number; n_observations: number };
              toast(`Imported ${rep.n_encounters} encounters, ${rep.n_variables} variables, ${rep.n_observations.toLocaleString()} observations`);
              onDone(r.name);
            } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
          }}>Import</button>
        </div>
      )}
    </div>
  );
}
