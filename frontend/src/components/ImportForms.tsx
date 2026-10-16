import { useState } from "react";
import { api, type Detection, type ImportReport } from "../api";
import { Alert, ChecksList, Spinner, toast } from "./ui";

export function DemoForm({ onDone }: { onDone: (name: string) => void }) {
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

export function ImportForm({ onDone }: { onDone: (name: string) => void }) {
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
