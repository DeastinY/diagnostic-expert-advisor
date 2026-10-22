import { useMemo, useState } from "react";
import { useCohort } from "../App";
import { api, fmt, type Suggestion } from "../api";
import { AdvisorPanel } from "../components/AdvisorPanel";
import { Findings, Verdict, withChoice } from "../components/Findings";
import { ImportCard } from "../components/ImportCard";
import { Alert, Card, Kpi, SeverityChip, Spinner, Tabs, toast, useAsync } from "../components/ui";
import { VegaChart } from "../components/VegaChart";

const KIND_TITLES: Record<string, string> = {
  harmonize: "Harmonize variable names", static: "Static attributes", drop: "Exclude from processing",
  processing: "Processing defaults", tag: "Clinical tags", filter: "Filters from static data", plot: "Default charts", quality: "Data quality checks",
};
const KIND_ORDER = ["processing", "static", "drop", "harmonize", "tag", "filter", "plot"]; // quality findings have their own card

export function Profile() {
  const { name, detail, where, reload } = useCohort();
  const prof = useAsync(() => api.profile(name, where), [name, where.join("|")]);
  const sugg = useAsync(() => api.suggestions(name), [name]);
  const ents = useAsync(() => api.entities(name), [name]);
  const pcharts = useAsync(() => api.profileCharts(name, where), [name, where.join("|")]);
  const [tab, setTab] = useState("Variables");
  const [open, setOpen] = useState<Record<string, boolean>>({ processing: true, static: true });
  const checks = useAsync(() => api.checks(name), [name]);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => {
    const g: Record<string, Suggestion[]> = {};
    for (const s of sugg.data ?? []) (g[s.kind] ??= []).push(s);
    return KIND_ORDER.filter((k) => g[k]).map((k) => [k, g[k]] as const);
  }, [sugg.data]);
  if (!detail) return <Spinner />;
  const p = prof.data;
  const applicable = (sugg.data ?? []).filter((s) => s.applicable && s.kind !== "quality");
  const fixable = (checks.data ?? []).filter((c) => c.action);
  const selected = picked ?? new Set(fixable.filter((c) => c.recommended).map((c) => c.id));
  const applyChecks = async () => {
    setBusy(true);
    try {
      const r = await api.applyChecks(name, [...selected]);
      toast(`Applied ${r.applied.length} fix${r.applied.length === 1 ? "" : "es"}`);
      setPicked(null); reload(); checks.reload(); sugg.reload(); prof.reload(); pcharts.reload();
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const apply = async (ids: string[]) => {
    setBusy(true);
    try { const r = await api.applySuggestions(name, ids); toast(`Applied ${r.applied.length} suggestion(s)`); reload(); sugg.reload(); prof.reload(); }
    catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const num = (v: unknown) => (typeof v === "number" ? fmt(v, Math.abs(v) < 10 ? 2 : 1) : "–");
  return (
    <>
      <div className="topbar"><div><h1>Profile &amp; suggestions</h1><p className="muted">Computed from aggregate statistics only; the suggestions run offline.</p></div></div>
      <div className="stack">
        {p && (
          <Card>
            <div className="kpis">
              <Kpi label="typical interval" value={p.sampling.median_interval_s ? `${Math.round(p.sampling.median_interval_s / 60)} min` : "n/a"} />
              <Kpi label="regular sampling" value={p.sampling.regular_fraction != null ? `${Math.round(p.sampling.regular_fraction * 100)}%` : "n/a"} />
              <Kpi label="suggested grid" value={p.sampling.suggested_resample ?? "n/a"} />
              <Kpi label="current grid" value={detail.config.processing.resample_every ?? "raw"} />
              <Kpi label="fill" value={detail.config.processing.fill} />
            </div>
            {p.warnings.map((w: string) => <Alert key={w}>{w}</Alert>)}
          </Card>
        )}
        <Card title={`Findings${checks.data ? ` (${checks.data.length})` : ""}`} actions={
          <button className="primary sm" disabled={selected.size === 0 || busy} onClick={applyChecks}>Apply {selected.size || ""} fix{selected.size === 1 ? "" : "es"}</button>}>
          {checks.loading && <Spinner />}
          {checks.data && (
            <div className="stack">
              <Verdict checks={checks.data} fixable={fixable} />
              <Findings checks={checks.data} total={detail.summary.n_observations} selected={selected} busy={busy} loadHist={(v, fix) => api.hist(name, v, fix)} loadSeries={(v, e) => api.series(name, v, e)}
                onChoose={(id, entry) => setPicked((prev) => withChoice(prev ?? selected, id, entry))} laterNote="Without a fix:" />
            </div>
          )}
        </Card>
        <Card title={`Suggestions${sugg.data ? ` (${sugg.data.length})` : ""}`} actions={
          <button className="primary sm" disabled={applicable.length === 0 || busy} onClick={() => apply(applicable.map((s) => s.id))}>Apply all {applicable.length}</button>}>
          {sugg.loading && <Spinner />}
          {sugg.data && groups.length === 0 && <p className="muted">Nothing to suggest. The cohort looks clean.</p>}
          {groups.map(([kind, items]) => {
            const ids = items.filter((s) => s.applicable).map((s) => s.id);
            const isOpen = open[kind] ?? items.length <= 3;
            return (
              <div key={kind} style={{ borderTop: "1px solid var(--border)", padding: "8px 0" }}>
                <div className="row spread">
                  <button className="ghost" onClick={() => setOpen({ ...open, [kind]: !isOpen })} style={{ padding: "4px 0", fontWeight: 600 }}>
                    {isOpen ? "▾" : "▸"} {KIND_TITLES[kind] ?? kind} <span className="chip">{items.length}</span>
                  </button>
                  {ids.length > 1 && <button className="sm" disabled={busy} onClick={() => apply(ids)}>Apply {ids.length}</button>}
                </div>
                {isOpen && items.map((s) => (
                  <div key={s.id} className="sugg">
                    <span className="muted">·</span>
                    <div>
                      {s.concept ? <Rename s={s} /> : <>
                        <div>{s.severity ? <><SeverityChip severity={s.severity} /> </> : null}<b>{s.title}</b>{!s.severity && s.confidence < 1 && <span className="muted small"> · {Math.round(s.confidence * 100)}%</span>}</div>
                        <div className="small muted">{s.detail}</div>
                      </>}
                    </div>
                    {s.applicable ? <button className="sm" disabled={busy} onClick={() => apply([s.id])}>Apply</button> : <span className="chip">info</span>}
                  </div>
                ))}
              </div>
            );
          })}
        </Card>
        <ImportCard name={name} />
        <Card title="Distributions">
          <p className="muted small">Each variable on its own scale, 1st to 99th percentile, from a sample of encounters.</p>
          {pcharts.loading ? <Spinner /> : <VegaChart spec={pcharts.data?.gallery} />}
        </Card>
        <Card>
          <Tabs tabs={["Variables", "Events", "Static", "Processing", "AI advisor", "Entities"]} value={tab} onChange={setTab} />
          {prof.loading && tab !== "Processing" && tab !== "AI advisor" && <Spinner />}
          {p && tab === "Variables" && pcharts.data?.observations && <div style={{ marginBottom: 12 }}><VegaChart spec={pcharts.data.observations} /></div>}
          {p && tab === "Variables" && (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Variable</th><th>Coverage</th><th>Observations</th><th>Interval</th><th>Min</th><th>Median</th><th>Max</th><th>Flags</th></tr></thead>
              <tbody>{p.variables.map((v: Record<string, unknown>) => (
                <tr key={String(v.name)}><td>{detail.config.variable_labels[String(v.name)] ?? String(v.name)}</td>
                  <td className="num">{Math.round(Number(v.coverage) * 100)}%</td><td className="num">{Number(v.n_observations).toLocaleString()}</td>
                  <td>{v.median_interval_s ? `${Math.round(Number(v.median_interval_s) / 60)} min` : "–"}</td>
                  <td className="num">{num(v.min)}</td><td className="num">{num(v.median)}</td><td className="num">{num(v.max)}</td>
                  <td>{(v.flags as string[]).map((f) => <span key={f} className={`chip${f.includes("identifier") || f.includes("constant") ? " warn" : ""}`}>{f}</span>)}</td></tr>
              ))}</tbody></table></div>
          )}
          {p && tab === "Events" && (
            p.events.length === 0 ? <p className="muted">No string-valued observations.</p> :
            <div className="table-wrap"><table className="table"><thead><tr><th>Event</th><th>Coverage</th><th>Observations</th><th>Distinct</th><th>Frequent values</th></tr></thead>
              <tbody>{p.events.map((e: Record<string, unknown>) => <tr key={String(e.name)}><td>{String(e.name)}</td><td className="num">{Math.round(Number(e.coverage) * 100)}%</td><td className="num">{Number(e.n_observations).toLocaleString()}</td><td className="num">{String(e.n_unique)}</td><td>{(e.top_values as [string, number][]).map(([v, n]) => `${v} (${n})`).join(", ") || "free text"}</td></tr>)}</tbody></table></div>
          )}
          {p && tab === "Static" && (
            <div className="table-wrap"><table className="table"><thead><tr><th>Column</th><th>Kind</th><th>Missing</th><th>Distinct</th><th>Min</th><th>Max</th><th>Values</th></tr></thead>
              <tbody>{p.static_columns.map((s: Record<string, unknown>) => <tr key={String(s.name)}><td>{String(s.name)}</td><td><span className="chip">{String(s.kind)}</span></td><td className="num">{String(s.n_missing)}</td><td className="num">{String(s.n_unique)}</td><td className="num">{num(s.min)}</td><td className="num">{num(s.max)}</td><td>{(s.top_values as [string, number][]).map(([v, n]) => `${v} (${n})`).join(", ")}</td></tr>)}</tbody></table></div>
          )}
          {tab === "Processing" && <ProcessingEditor />}
          {tab === "AI advisor" && <AdvisorPanel />}
          {tab === "Entities" && (
            ents.data?.available ? (
              <div className="table-wrap"><table className="table"><thead><tr><th>Label</th><th>Entity</th><th>Encounters</th><th>Mentions</th></tr></thead>
                <tbody>{ents.data.summary.map((r, i) => <tr key={i}><td><span className="chip">{String(r.label)}</span></td><td>{String(r.entity)}</td><td className="num">{String(r.encounters)}</td><td className="num">{String(r.mentions)}</td></tr>)}</tbody></table></div>
            ) : <p className="muted">No entities yet. Run <code>dea nlp run {name} --connector spacy</code> (or gliner, transformers, presidio, llm) to annotate free text with a local model.</p>
          )}
        </Card>
      </div>
    </>
  );
}

/** A rename: the local name, the canonical name it becomes, and the codes that come with it. */
function Rename({ s }: { s: Suggestion }) {
  const c = s.concept!;
  const codes: [string, string][] = [];
  if (c.loinc) codes.push(["LOINC", c.loinc]);
  if (c.rxnorm) codes.push(["RxNorm", c.rxnorm]);
  if (c.atc) codes.push(["ATC", c.atc]);
  if (c.ucum) codes.push(["UCUM", c.ucum]);
  return (
    <>
      <div className="rename">
        <b>{s.variables[0]}</b><span className="arrow">→</span><code className="canon">{c.name}</code>
        <span className="label">{c.label}{c.unit ? ` (${c.unit})` : ""}</span>
        {s.confidence < 1 && <span className="muted small">· {Math.round(s.confidence * 100)}%</span>}
      </div>
      <div className="codes">
        {codes.map(([k, v]) => <span key={k} className="chip code"><span className="k">{k}</span> {v}</span>)}
        {c.joins.length > 0 && <span className="chip">with {c.joins.map((j) => `'${j}'`).join(", ")}</span>}
      </div>
      <div className="small muted">{s.detail}</div>
    </>
  );
}

function ProcessingEditor() {
  const { name, detail, reload } = useCohort();
  const proc = detail!.config.processing;
  const [every, setEvery] = useState(proc.resample_every ?? "");
  const [agg, setAgg] = useState(proc.aggregate);
  const [fill, setFill] = useState(proc.fill);
  const [drops, setDrops] = useState<string[]>(proc.drop_variables);
  const [lead, setLead] = useState<string>(String(proc.pre_admission_lead_hours ?? 24));
  return (
    <div className="stack" style={{ gap: 10 }}>
      <p className="muted small">How observations become a regular wide frame for charts, exports and <code>cohort.wide()</code>.</p>
      <div className="grid c3">
        <label className="field">Resample every<input value={every} placeholder="e.g. 15m, 1h — empty keeps raw timestamps" onChange={(e) => setEvery(e.target.value)} /></label>
        <label className="field">Aggregate<select value={agg} onChange={(e) => setAgg(e.target.value)}>{["mean", "median", "first", "last", "min", "max"].map((a) => <option key={a}>{a}</option>)}</select></label>
        <label className="field">Fill gaps<select value={fill} onChange={(e) => setFill(e.target.value)}>{["none", "forward", "interpolate", "zero"].map((a) => <option key={a}>{a}</option>)}</select></label>
      </div>
      <div className="grid c3">
        <label className="field">Run-up before admission (hours)
          <input type="number" min={0} step={1} value={lead} onChange={(e) => setLead(e.target.value)} />
          <span className="small muted">Data up to this long before the admission time belongs to the stay (emergency department labs). Anything earlier is flagged as a foreign record; the findings on this page use the window.</span>
        </label>
      </div>
      <div className="row"><span className="small muted">Drop:</span>{detail!.variables.map((v) => <span key={v} className={`chip${drops.includes(v) ? " warn" : ""}`} style={{ cursor: "pointer" }} onClick={() => setDrops(drops.includes(v) ? drops.filter((x) => x !== v) : [...drops, v])}>{v}</span>)}</div>
      {Object.keys(proc.fill_overrides).length > 0 && <div className="small muted">Per-variable fills: {Object.entries(proc.fill_overrides).map(([k, v]) => `${k} → ${v}`).join(", ")}</div>}
      <div><button className="primary" onClick={async () => {
        try { await api.processing(name, { resample_every: every || null, aggregate: agg, fill, drop_variables: drops, pre_admission_lead_hours: lead === "" ? undefined : Number(lead) }); toast("Processing settings saved"); reload(); }
        catch (e) { toast((e as Error).message); }
      }}>Save</button></div>
    </div>
  );
}
