import { useEffect, useRef, useState, type DragEvent } from "react";
import { api, type Detection, type ImportReport } from "../api";
import { Alert, ChecksList, Spinner, toast } from "./ui";

/** A file picked from a drop or a folder chooser, with its path relative to what was dropped. */
export interface Picked { file: File; path: string }

const JUNK = /(^|\/)(\.DS_Store|Thumbs\.db|desktop\.ini|\._[^/]*|\.[^/]*)$/;

async function readEntry(entry: FileSystemEntry, prefix: string): Promise<Picked[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((ok, no) => (entry as FileSystemFileEntry).file(ok, no));
    return [{ file, path: prefix + entry.name }];
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  const out: Picked[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((ok, no) => reader.readEntries(ok, no));
    if (!batch.length) break;
    for (const e of batch) out.push(...(await readEntry(e, prefix + entry.name + "/")));
  }
  return out;
}

/** Everything behind a drop: whole folders are walked, hidden files skipped. */
export async function fromDataTransfer(dt: DataTransfer): Promise<Picked[]> {
  const items = Array.from(dt.items ?? []);
  const entries = items.map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean) as FileSystemEntry[];
  let out: Picked[] = [];
  if (entries.length) for (const e of entries) out.push(...(await readEntry(e, "")));
  else out = Array.from(dt.files).map((f) => ({ file: f, path: f.name }));
  return out.filter((p) => !JUNK.test(p.path));
}

function fromInput(files: FileList): Picked[] {
  return Array.from(files)
    .map((f) => ({ file: f, path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name }))
    .filter((p) => !JUNK.test(p.path));
}

const mb = (n: number) => `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;

const FORMAT_TEXT: Record<string, [string, string]> = {
  mimic: ["MIMIC-IV folder", "One encounter per ICU stay. Chart, lab, input, procedure and output events, diagnoses and notes are read; units are converted per row, and ICD-9 and ICD-10 diagnoses become dx_ columns."],
  fhir: ["FHIR R4 resources", "Patient, Encounter, Observation, Condition and MedicationAdministration are read. Organizations become the site column."],
  csv_tree: ["DEA v1 tree", "static.csv plus one dynamic.csv per encounter."],
  dea: ["DEA cohort", "This is already a DEA cohort. It will be copied into the data directory."],
  table: ["Table", "One table with an encounter id, a time and either variable/value columns or one column per variable. Check the column mapping below."],
  unknown: ["Not recognized", "DEA could not tell what this is. Have a look at the notes; a single table, a MIMIC-IV folder or FHIR resources work."],
};

const STEPS = ["Add data", "What we found", "Checks", "Done"];

function Steps({ at }: { at: number }) {
  return (
    <ol className="wiz-steps">
      {STEPS.map((s, i) => <li key={s} className={i < at ? "done" : i === at ? "on" : ""}><span className="n">{i < at ? "✓" : i + 1}</span>{s}</li>)}
    </ol>
  );
}

function ImportPreview({ report }: { report: ImportReport }) {
  return (
    <div className="stack">
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
    </div>
  );
}

export function ImportWizard({ onDone, onMore, dropped }: { onDone: (name: string, where: "overview" | "profile") => void; onMore?: () => void; dropped?: Picked[] | null }) {
  const [step, setStep] = useState(0);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [det, setDet] = useState<Detection | null>(null);
  const [staged, setStaged] = useState<string | null>(null);
  const [nFiles, setNFiles] = useState<number | null>(null);
  const [ov, setOv] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [prog, setProg] = useState<{ files: number; of: number; bytes: number; ofBytes: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<ImportReport | null>(null);
  const [harmonize, setHarmonize] = useState(true);
  const [result, setResult] = useState<{ name: string; report: ImportReport; applied: number } | null>(null);
  const [over, setOver] = useState(false);
  const folderInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const fail = (e: unknown) => { setErr((e as Error).message); setBusy(null); setProg(null); };

  const inspectPath = async (p: string) => {
    setBusy("Inspecting…"); setErr(null);
    try {
      const d = await api.detect(p);
      setDet(d); setStaged(p); setNFiles(null); setOv({});
      if (!name) setName(p.replace(/\/+$/, "").split("/").pop()?.replace(/\.[^.]+$/, "") ?? "");
      setStep(1);
    } catch (e) { fail(e); } finally { setBusy(null); }
  };

  const uploadPicked = async (picked: Picked[]) => {
    if (!picked.length) return;
    setErr(null);
    const ofBytes = picked.reduce((a, p) => a + p.file.size, 0);
    setProg({ files: 0, of: picked.length, bytes: 0, ofBytes });
    setBusy("Uploading…");
    try {
      const { id } = await api.uploadStart();
      let batch: Picked[] = []; let size = 0; let files = 0; let bytes = 0;
      const flush = async () => {
        if (!batch.length) return;
        await api.uploadFiles(id, batch);
        files += batch.length; bytes += size;
        setProg({ files, of: picked.length, bytes, ofBytes });
        batch = []; size = 0;
      };
      for (const p of picked) {
        if (batch.length >= 40 || size + p.file.size > 64 * 1048576) await flush();
        batch.push(p); size += p.file.size;
      }
      await flush();
      setBusy("Looking at what arrived…");
      const top = picked[0].path.split("/")[0];
      const r = await api.uploadDone(id, picked.length > 1 && top && !top.includes(".") ? top : undefined);
      setDet(r.detection); setStaged(r.staged); setNFiles(r.n_files); setOv({});
      if (!name) setName(r.name.replace(/\.[^.]+$/, ""));
      setStep(1);
    } catch (e) { fail(e); } finally { setBusy(null); setProg(null); }
  };

  useEffect(() => { if (dropped?.length) void uploadPicked(dropped); }, [dropped]); // eslint-disable-line react-hooks/exhaustive-deps

  const onDrop = async (e: DragEvent) => {
    e.preventDefault(); setOver(false);
    try { await uploadPicked(await fromDataTransfer(e.dataTransfer)); } catch (x) { fail(x); }
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

  const runChecks = async () => {
    setBusy("Running the checks…"); setErr(null);
    try { setPreview((await api.preview({ path: staged!, name, overrides: ov })).report); setStep(2); }
    catch (e) { fail(e); } finally { setBusy(null); }
  };

  const doImport = async () => {
    setBusy("Writing the cohort…"); setErr(null);
    try {
      const r = await api.import({ path: staged!, name, overrides: ov });
      let applied = 0;
      if (harmonize) {
        setBusy("Applying the suggestions…");
        const safe = new Set(["harmonize", "static", "processing", "tag", "plot"]);
        const ids = (await api.suggestions(r.name)).filter((s) => s.applicable && safe.has(s.kind)).map((s) => s.id);
        if (ids.length) { await api.applySuggestions(r.name, ids); applied = ids.length; }
      }
      setResult({ name: r.name, report: r.report, applied });
      setStep(3);
      toast(`Imported ${r.report.n_encounters} encounters`);
    } catch (e) { fail(e); } finally { setBusy(null); }
  };

  const reset = () => { setStep(0); setDet(null); setStaged(null); setPreview(null); setResult(null); setOv({}); setErr(null); };
  const [fmtTitle, fmtText] = FORMAT_TEXT[det?.format ?? "unknown"] ?? FORMAT_TEXT.unknown;

  return (
    <div className="stack wizard">
      <Steps at={step} />
      {err && <Alert kind="danger">{err}</Alert>}

      {step === 0 && (
        <div className="stack">
          <div
            className={`drop${over ? " over" : ""}${busy ? " busy" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
            onClick={() => !busy && folderInput.current?.click()}
            role="button" tabIndex={0}
          >
            {busy ? (
              <div className="stack" style={{ gap: 8, alignItems: "center" }}>
                <Spinner label={busy} />
                {prog && (
                  <>
                    <div className="progress"><div style={{ width: `${prog.ofBytes ? Math.round((prog.bytes / prog.ofBytes) * 100) : 0}%` }} /></div>
                    <div className="small muted">{prog.files} of {prog.of} files · {mb(prog.bytes)} of {mb(prog.ofBytes)}</div>
                  </>
                )}
              </div>
            ) : (
              <>
                <div className="drop-icon">⤓</div>
                <b>Drop a folder or a file here</b>
                <div className="small muted">A MIMIC-IV download, a FHIR export, a CSV or Parquet table, or a zip. Or click to choose a folder.</div>
                <div className="row" style={{ justifyContent: "center", marginTop: 6 }} onClick={(e) => e.stopPropagation()}>
                  <button className="sm" onClick={() => folderInput.current?.click()}>Choose a folder</button>
                  <button className="sm" onClick={() => fileInput.current?.click()}>Choose files</button>
                </div>
              </>
            )}
            <input ref={folderInput} type="file" multiple style={{ display: "none" }} {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
              onChange={(e) => { if (e.target.files?.length) void uploadPicked(fromInput(e.target.files)); e.target.value = ""; }} />
            <input ref={fileInput} type="file" multiple style={{ display: "none" }}
              onChange={(e) => { if (e.target.files?.length) void uploadPicked(fromInput(e.target.files)); e.target.value = ""; }} />
          </div>
          <div className="row">
            <span className="small muted">Large folder already on this machine? Paste its path instead:</span>
          </div>
          <div className="row">
            <input style={{ flex: 1 }} placeholder="/path/on/this/machine (file or folder)" value={path} onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && path && inspectPath(path)} />
            <button onClick={() => path && inspectPath(path)} disabled={!path || !!busy}>Inspect</button>
          </div>
        </div>
      )}

      {step === 1 && det && (
        <div className="stack">
          <div>
            <div className="row">
              <span className="chip on">{fmtTitle}{det.table_kind ? ` / ${kind}` : ""}</span>
              {nFiles != null && <span className="chip">{nFiles} file{nFiles === 1 ? "" : "s"} received</span>}
              {det.notes.map((n) => <span key={n} className="chip warn">{n}</span>)}
            </div>
            <p className="small muted" style={{ marginTop: 8 }}>{fmtText}</p>
          </div>
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
          <label className="field">Cohort name<input value={name} onChange={(e) => setName(e.target.value.replace(/[^\w.-]+/g, "_"))} /></label>
          {busy && <Spinner label={busy} />}
          <div className="row spread">
            <button className="ghost" onClick={reset} disabled={!!busy}>← Start over</button>
            <button className="primary" disabled={!name || det.format === "unknown" || !!busy} onClick={runChecks}>Run the checks →</button>
          </div>
        </div>
      )}

      {step === 2 && preview && (
        <div className="stack">
          <ImportPreview report={preview} />
          <p className="small muted">Nothing has been written yet. Fixes for the findings are offered on the profile page after the import.</p>
          <label className="row"><input type="checkbox" checked={harmonize} onChange={(e) => setHarmonize(e.target.checked)} /> Apply the suggested harmonization right away (canonical variable names, static attributes, tags, processing defaults)</label>
          {busy && <Spinner label={busy} />}
          <div className="row spread">
            <button className="ghost" onClick={() => setStep(1)} disabled={!!busy}>← Back</button>
            <button className="primary" disabled={!!busy} onClick={doImport}>Import as “{name}” →</button>
          </div>
        </div>
      )}

      {step === 3 && result && (
        <div className="stack">
          <div className="row">
            <span className="chip good">imported</span>
            <span className="chip">{result.report.n_encounters} encounters</span>
            <span className="chip">{result.report.n_variables} variables</span>
            <span className="chip">{result.report.n_observations.toLocaleString()} observations</span>
            {result.applied > 0 && <span className="chip on">{result.applied} suggestion{result.applied === 1 ? "" : "s"} applied</span>}
          </div>
          <p className="small muted">The cohort is saved under <code>{result.name}</code> in the data directory. The overview, the distributions and the patient pages are ready; the import report stays next to the cohort.</p>
          <div className="row">
            <button className="primary" onClick={() => onDone(result.name, "overview")}>Open the cohort</button>
            <button onClick={() => onDone(result.name, "profile")}>Review findings and suggestions</button>
            <button className="ghost" onClick={() => { reset(); onMore?.(); }}>Add more data</button>
          </div>
        </div>
      )}
    </div>
  );
}
