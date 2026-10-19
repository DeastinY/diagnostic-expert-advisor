import { useEffect, useRef, useState, type DragEvent } from "react";
import { api, type Detection, type ImportBody, type ImportReport, type Previous } from "../api";
import { Findings, Verdict, entryFor, num, withChoice, type FixLoader, type SeriesLoader } from "./Findings";
import { Alert, Spinner, toast } from "./ui";

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
  file_tree: ["One file per encounter", "A folder of tables with the same columns, one per patient or stay; the encounter id is what differs between the file names. A static table with one row per encounter goes with them. Check the time column and the static table below."],
  unknown: ["Not recognized", "DEA could not tell the layout of this folder on its own. Below you can say how to read it: as one file per encounter, by naming the files, or as a single table."],
};

const STEPS = ["Add data", "What we found", "Review & fix", "Done"];

function Steps({ at }: { at: number }) {
  return (
    <ol className="wiz-steps">
      {STEPS.map((s, i) => <li key={s} className={i < at ? "done" : i === at ? "on" : ""}><span className="n">{i < at ? "✓" : i + 1}</span>{s}</li>)}
    </ol>
  );
}

// ---------------------------------------------------------------- review step

function Tiles({ r }: { r: ImportReport }) {
  const items: [number, string][] = [[r.n_encounters, "encounters"], [r.n_variables, "variables"], [r.n_observations, "observations"]];
  if (r.n_events) items.push([r.n_events, "events"]);
  if (r.n_intervals) items.push([r.n_intervals, "intervals"]);
  return <div className="tiles">{items.map(([v, l], i) => <div key={l} className={`tile rise d${i + 1}`}><b>{num(v)}</b><span>{l}</span></div>)}</div>;
}

function Review({ report, previous, selected, onChoose, busy, loadHist, loadSeries }: { report: ImportReport; previous: Previous | null; selected: Set<string>; onChoose: (id: string, entry: string | null) => void; busy: boolean; loadHist: FixLoader; loadSeries: SeriesLoader }) {
  const fixable = report.checks.filter((c) => c.action);
  const s = report.summary;
  const delta = previous && previous.n_observations != null ? report.n_observations - previous.n_observations : null;
  return (
    <div className="stack">
      <Tiles r={report} />
      <Verdict checks={report.checks} fixable={fixable} />
      <div className="sevbar rise d2">
        <span><i className="dot error" />{s.error} error{s.error === 1 ? "" : "s"}</span>
        <span><i className="dot warning" />{s.warning} warning{s.warning === 1 ? "" : "s"}</span>
        <span><i className="dot info" />{s.info} info</span>
        <span>· {s.units_converted} variable{s.units_converted === 1 ? "" : "s"} with converted units{s.units_split ? `, ${s.units_split} split` : ""}{s.units_skipped ? `, ${s.units_skipped} left as is` : ""}</span>
      </div>
      {previous && (
        <div className="previous rise">
          Imported before, on {previous.when.replace("T", " at ")}: {previous.n_encounters != null ? `${num(previous.n_encounters)} encounters, ` : ""}{previous.n_observations != null ? `${num(previous.n_observations)} observations` : ""}{delta != null && delta !== 0 ? ` (${delta > 0 ? "+" : ""}${num(delta)} now)` : ""}.
          {previous.applied.length ? ` You applied ${previous.applied.length} fix${previous.applied.length === 1 ? "" : "es"} then; the same ones are ticked.` : " You applied no fixes then; the same choice is kept."}
        </div>
      )}
      {report.applied.length > 0 && <div className="applied rise">✓ {report.applied.length} fix{report.applied.length === 1 ? "" : "es"} applied. What is listed below is what remains.</div>}
      {report.warnings.map((w) => <Alert key={w}>{w}</Alert>)}
      {report.empty_columns && report.empty_columns.length > 0 && (
        <div className="small muted">{num(report.empty_columns.length)} column{report.empty_columns.length === 1 ? " is" : "s are"} empty in every row and left out: {report.empty_columns.slice(0, 8).join(", ")}{report.empty_columns.length > 8 ? ", …" : ""}</div>
      )}
      <Findings checks={report.checks} total={report.n_observations} selected={selected} onChoose={onChoose} busy={busy} loadHist={loadHist} loadSeries={loadSeries} unitReports={report.unit_reports} laterNote="For later, on the profile page:" />
    </div>
  );
}

// ---------------------------------------------------------------- wizard

function BusyLine({ busy, stage }: { busy: string; stage: { text: string; elapsed: number } | null }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      <Spinner label={busy} />
      {stage && <div className="small muted">{stage.text} · {stage.elapsed < 60 ? `${Math.round(stage.elapsed)} s` : `${Math.floor(stage.elapsed / 60)} min ${Math.round(stage.elapsed % 60)} s`}</div>}
      <div className="busy-bar" />
    </div>
  );
}

/** Cohort name proposed from a pasted path: its last segment, or the parent when that is
 *  only a version number ("…/mimic-iv-demo/2.2" → "mimic-iv-demo"). */
export function nameFromPath(p: string): string {
  const parts = p.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  const last = (parts.pop() ?? "").replace(/\.[^.]+$/, "");
  if (/[A-Za-z]/.test(last) || parts.length === 0) return last;
  const parent = parts[parts.length - 1];
  return /[A-Za-z]/.test(parent) ? parent : last;
}

export function ImportWizard({ onDone, onMore, onImported, dropped }: { onDone: (name: string, where: "overview" | "profile") => void; onMore?: () => void; onImported?: (name: string) => void; dropped?: Picked[] | null }) {
  const [step, setStep] = useState(0);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [det, setDet] = useState<Detection | null>(null);
  const [staged, setStaged] = useState<string | null>(null);
  const [nFiles, setNFiles] = useState<number | null>(null);
  const [staticPath, setStaticPath] = useState("");
  const staticInput = useRef<HTMLInputElement>(null);
  const [ov, setOv] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [prog, setProg] = useState<{ files: number; of: number; bytes: number; ofBytes: number } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [preview, setPreview] = useState<ImportReport | null>(null);
  const [previous, setPrevious] = useState<Previous | null>(null);
  const [stage, setStage] = useState<{ text: string; elapsed: number } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
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
      if (!name) setName(nameFromPath(p));
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

  const body = (fixes: string[]): ImportBody => ({ path: staged!, name, static_path: staticPath || null, overrides: ov, fixes });

  /** Run a preview or import as a background job and show its stage while it runs. */
  const runJob = async <T,>(kind: "preview" | "import", b: ImportBody, label: string): Promise<T> => {
    setBusy(label); setStage({ text: "starting", elapsed: 0 });
    const { id } = await api.startJob(kind, b);
    for (;;) {
      await new Promise((ok) => setTimeout(ok, 600));
      const j = await api.job(id);
      setStage({ text: j.stage + (j.detail ? `: ${j.detail}` : ""), elapsed: j.elapsed });
      if (j.status === "done") return j.result as T;
      if (j.status === "error") throw new Error(j.error ?? "failed");
    }
  };

  /** Run the import without writing, with the fixes applied so far plus the selection. */
  const check = async (fixes: string[], label: string) => {
    setErr(null);
    try {
      const r = await runJob<{ report: ImportReport; previous: Previous | null }>("preview", body(fixes), label);
      const rep = r.report;
      const known = new Set((preview?.checks ?? []).map((c) => c.id));
      const first = preview === null;
      setPreview(rep); setPrevious(r.previous);
      // first run: what was chosen last time for this cohort, else the recommended ones;
      // later runs: keep the user's choices for findings already listed, recommended for new ones
      setSelected((prev) => {
        const n = new Set<string>();
        for (const c of rep.checks) {
          if (!c.action) continue;
          let entry: string | null = c.recommended ? c.id : null;
          const was = first && r.previous ? r.previous.applied.find((e) => e === c.id || e.startsWith(c.id + "#")) : undefined;
          if (first && r.previous && (was || r.previous.declined.includes(c.id))) entry = was ?? null;
          else if (known.has(c.id)) entry = entryFor(prev, c.id);
          if (entry) n.add(entry);
        }
        return n;
      });
      setStep(2);
    } catch (e) { fail(e); } finally { setBusy(null); setStage(null); }
  };
  const runChecks = () => check([], "Running the checks…");
  const applied = preview?.applied ?? [];
  const toApply = [...applied, ...selected];
  const recheck = () => check(toApply, `Applying ${selected.size} fix${selected.size === 1 ? "" : "es"} and checking again…`);
  // the state the findings describe; with a fix, the histogram also shows its result
  const loadHist: FixLoader = (variable, fix) => api.previewHist({ ...body(applied), variable, where: fix?.where ?? null, expr: fix ? fix.expr : null });
  const loadSeries: SeriesLoader = (variable, encounter) => api.previewSeries({ ...body(applied), variable, encounter });

  const doImport = async () => {
    setErr(null);
    try {
      const r = await runJob<{ name: string; report: ImportReport }>("import", body(toApply), toApply.length ? `Applying ${toApply.length} fix${toApply.length === 1 ? "" : "es"} and writing the cohort…` : "Writing the cohort…");
      let sugg = 0;
      if (harmonize) {
        setBusy("Applying the suggested harmonization…");
        const safe = new Set(["harmonize", "static", "processing", "tag", "plot"]);
        const ids = (await api.suggestions(r.name)).filter((s) => s.applicable && safe.has(s.kind)).map((s) => s.id);
        if (ids.length) { await api.applySuggestions(r.name, ids); sugg = ids.length; }
      }
      setResult({ name: r.name, report: r.report, applied: sugg });
      setStep(3);
      onImported?.(r.name); // the cohort list and the summary below show the new cohort at once
      toast(`Imported ${r.report.n_encounters} encounters`);
    } catch (e) { fail(e); } finally { setBusy(null); setStage(null); }
  };

  const reset = () => { setStep(0); setDet(null); setStaged(null); setPreview(null); setPrevious(null); setResult(null); setOv({}); setErr(null); setSelected(new Set()); setStaticPath(""); };
  const [fmtTitle, fmtText] = FORMAT_TEXT[det?.format ?? "unknown"] ?? FORMAT_TEXT.unknown;

  return (
    <div className="stack wizard">
      <Steps at={step} />
      {err && <Alert kind="danger">{err}</Alert>}

      {step === 0 && (
        <div className="stack rise" key="s0">
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
        <div className="stack rise" key="s1">
          <div>
            <div className="row">
              <span className="chip on">{fmtTitle}{det.table_kind ? ` / ${kind}` : ""}</span>
              {nFiles != null && <span className="chip">{nFiles} file{nFiles === 1 ? "" : "s"} received</span>}
              {det.notes.map((n) => <span key={n} className="chip warn">{n}</span>)}
            </div>
            <p className="small muted" style={{ marginTop: 8 }}>{fmtText}</p>
          </div>
          {det.format === "file_tree" && (
            <div className="row" style={{ gap: 6 }}>
              <span className="small muted">for example</span>
              {det.files.slice(0, 2).map((f) => <span key={f} className="chip muted">{f}</span>)}
            </div>
          )}
          {(det.format === "table" || det.format === "file_tree") && (
            <>
              <div className="grid c2">
                {det.format === "table" && <Sel k="encounter_col" label="Encounter id column" />}
                {det.format === "file_tree" && <label className="field">Encounter id<input value="from the file name" disabled /></label>}
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
          {det.format === "file_tree" && (
            <div className="grid c2">
              <label className="field">Static table in the folder
                <select value={ov.static_file ?? det.static_file ?? ""} onChange={(e) => setOv({ ...ov, static_file: e.target.value, static_id_col: "" })}>
                  <option value="">none</option>
                  {det.static_candidates.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <span className="small muted">one row per encounter: age, sex, outcome, ...</span>
              </label>
              {(ov.static_file ?? det.static_file) && (
                <label className="field">Its encounter id column
                  <select value={ov.static_id_col ?? det.static_id_col ?? ""} onChange={(e) => setOv({ ...ov, static_id_col: e.target.value })}>
                    <option value="">find by matching ids</option>
                    {((ov.static_file ?? det.static_file) === det.static_file ? det.static_columns : []).map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
              )}
            </div>
          )}
          {det.format === "unknown" && (
            <div className="stack" style={{ gap: 10 }}>
              {det.tables.length > 0 && (
                <div className="field">
                  <span>Tables in this folder</span>
                  <div className="row">{det.tables.slice(0, 12).map((t) => <button key={t} className="sm" disabled={!!busy} title="Read this table on its own" onClick={() => void inspectPath(`${staged}/${t}`)}>{t}</button>)}{det.tables.length > 12 && <span className="small muted">and {det.tables.length - 12} more</span>}</div>
                </div>
              )}
              <div className="field">
                <span>One file per encounter: the files to take, as a pattern</span>
                <div className="row">
                  <input style={{ flex: 1 }} placeholder="e.g. patient_*.csv" value={ov.pattern ?? ""} onChange={(e) => setOv({ ...ov, pattern: e.target.value })} />
                  <button className="sm" disabled={!!busy || !(ov.pattern ?? "").trim()} onClick={async () => {
                    setBusy("Reading the files…"); setErr(null);
                    try { const d = await api.detect(staged!, { pattern: ov.pattern }); setDet(d); setOv({ pattern: ov.pattern! }); }
                    catch (e) { fail(e); } finally { setBusy(null); }
                  }}>Read as one file per encounter</button>
                </div>
                <span className="small muted">The encounter id is what differs between the file names. {det.ignored.length > 0 ? `Not tables: ${det.ignored.slice(0, 4).join(", ")}${det.ignored.length > 4 ? ", …" : ""}.` : ""}</span>
              </div>
            </div>
          )}
          {(det.format === "table" || det.format === "file_tree") && (
            <div className="field">
              <span>{det.format === "file_tree" ? "Or a static table from elsewhere (optional)" : "Static table (optional): one row per encounter with age, sex, outcome, hospital, ..."}</span>
              <div className="row">
                <input style={{ flex: 1 }} placeholder="/path/to/patients.csv" value={staticPath} onChange={(e) => setStaticPath(e.target.value)} />
                <button className="sm" disabled={!!busy} onClick={() => staticInput.current?.click()}>Upload</button>
                <input ref={staticInput} type="file" style={{ display: "none" }} onChange={async (e) => {
                  const f = e.target.files?.[0]; e.target.value = "";
                  if (!f) return;
                  setBusy("Uploading the static table…");
                  try { setStaticPath((await api.upload(f)).staged); } catch (x) { fail(x); } finally { setBusy(null); }
                }} />
              </div>
            </div>
          )}
          <label className="field">Cohort name<input value={name} onChange={(e) => setName(e.target.value.replace(/[^\w.-]+/g, "_"))} /></label>
          {busy && <BusyLine busy={busy} stage={stage} />}
          <div className="row spread">
            <button className="ghost" onClick={reset} disabled={!!busy}>← Start over</button>
            <button className="primary" disabled={!name || det.format === "unknown" || !!busy} onClick={runChecks}>Run the checks →</button>
          </div>
        </div>
      )}

      {step === 2 && preview && (
        <div className="stack rise" key="s2">
          <Review report={preview} previous={previous} selected={selected} busy={!!busy} loadHist={loadHist} loadSeries={loadSeries} onChoose={(id, entry) => setSelected((prev) => withChoice(prev, id, entry))} />
          <label className="row"><input type="checkbox" checked={harmonize} onChange={(e) => setHarmonize(e.target.checked)} /> After the import, also apply the suggested harmonization (canonical variable names, static attributes, tags, processing defaults)</label>
          {busy && <BusyLine busy={busy} stage={stage} />}
          <div className="row spread">
            <button className="ghost" onClick={() => setStep(1)} disabled={!!busy}>← Back</button>
            <div className="row">
              <button disabled={!!busy || selected.size === 0} onClick={recheck}>Apply {selected.size || ""} and check again</button>
              <button className="primary" disabled={!!busy} onClick={doImport}>Import{toApply.length ? ` with ${toApply.length} fix${toApply.length === 1 ? "" : "es"}` : ""} →</button>
            </div>
          </div>
        </div>
      )}

      {step === 3 && result && (
        <div className="stack rise" key="s3">
          <div className="row">
            <span className="done-mark"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7" /></svg></span>
            <div>
              <b style={{ fontSize: 16 }}>“{result.name}” is ready.</b>
              <div className="small muted">{num(result.report.n_encounters)} encounters · {num(result.report.n_variables)} variables · {num(result.report.n_observations)} observations</div>
            </div>
          </div>
          <div className="row">
            {result.report.applied.length > 0 && <span className="chip on">{result.report.applied.length} fix{result.report.applied.length === 1 ? "" : "es"} applied</span>}
            {result.applied > 0 && <span className="chip on">{result.applied} suggestion{result.applied === 1 ? "" : "s"} applied</span>}
            {result.report.summary.warning > 0 && <span className="chip warn">{result.report.summary.warning} warning{result.report.summary.warning === 1 ? "" : "s"} left to look at</span>}
            {result.report.summary.error > 0 && <span className="chip danger">{result.report.summary.error} error{result.report.summary.error === 1 ? "" : "s"} left</span>}
          </div>
          <p className="small muted">The cohort is saved in the data directory with its import report. Every fix is listed in cohort.yaml, so you can always see what was changed. The remaining findings are on the profile page.</p>
          <div className="row">
            <button className="primary" onClick={() => onDone(result.name, "overview")}>Open the cohort</button>
            <button onClick={() => onDone(result.name, "profile")}>Remaining findings</button>
            <button className="ghost" onClick={() => { reset(); onMore?.(); }}>Add more data</button>
          </div>
        </div>
      )}
    </div>
  );
}
