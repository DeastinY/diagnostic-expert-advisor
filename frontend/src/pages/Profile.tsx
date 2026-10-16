import { useMemo, useState } from "react";
import { useCohort } from "../App";
import { api, fmt, type Recommendation, type Suggestion } from "../api";
import { Alert, Card, ChecksList, Kpi, SeverityChip, Spinner, Tabs, toast, useAsync } from "../components/ui";
import { VegaChart } from "../components/VegaChart";

const KIND_TITLES: Record<string, string> = {
  harmonize: "Harmonize variable names", static: "Static attributes", drop: "Exclude from processing",
  processing: "Processing defaults", tag: "Clinical tags", filter: "Filters from static data", plot: "Default charts", quality: "Data quality checks",
};
const KIND_ORDER = ["quality", "processing", "static", "drop", "harmonize", "tag", "filter", "plot"];

export function Profile() {
  const { name, detail, where, reload } = useCohort();
  const prof = useAsync(() => api.profile(name, where), [name, where.join("|")]);
  const sugg = useAsync(() => api.suggestions(name), [name]);
  const ents = useAsync(() => api.entities(name), [name]);
  const pcharts = useAsync(() => api.profileCharts(name, where), [name, where.join("|")]);
  const [tab, setTab] = useState("Variables");
  const [open, setOpen] = useState<Record<string, boolean>>({ quality: true, processing: true, static: true });
  const [busy, setBusy] = useState(false);
  const groups = useMemo(() => {
    const g: Record<string, Suggestion[]> = {};
    for (const s of sugg.data ?? []) (g[s.kind] ??= []).push(s);
    return KIND_ORDER.filter((k) => g[k]).map((k) => [k, g[k]] as const);
  }, [sugg.data]);
  if (!detail) return <Spinner />;
  const p = prof.data;
  const applicable = (sugg.data ?? []).filter((s) => s.applicable);
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
        <Card title={`Suggestions${sugg.data ? ` (${sugg.data.length})` : ""}`} actions={
          <button className="primary sm" disabled={applicable.length === 0 || busy} onClick={() => apply(applicable.map((s) => s.id))}>Apply all {applicable.length}</button>}>
          {sugg.loading && <Spinner />}
          {sugg.data?.length === 0 && <p className="muted">Nothing to suggest. The cohort looks clean.</p>}
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
                      <div>{s.severity ? <><SeverityChip severity={s.severity} /> </> : null}<b>{s.title}</b>{!s.severity && s.confidence < 1 && <span className="muted small"> · {Math.round(s.confidence * 100)}%</span>}</div>
                      <div className="small muted">{s.detail}</div>
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

function ProcessingEditor() {
  const { name, detail, reload } = useCohort();
  const proc = detail!.config.processing;
  const [every, setEvery] = useState(proc.resample_every ?? "");
  const [agg, setAgg] = useState(proc.aggregate);
  const [fill, setFill] = useState(proc.fill);
  const [drops, setDrops] = useState<string[]>(proc.drop_variables);
  return (
    <div className="stack" style={{ gap: 10 }}>
      <p className="muted small">How observations become a regular wide frame for charts, exports and <code>cohort.wide()</code>.</p>
      <div className="grid c3">
        <label className="field">Resample every<input value={every} placeholder="e.g. 15m, 1h — empty keeps raw timestamps" onChange={(e) => setEvery(e.target.value)} /></label>
        <label className="field">Aggregate<select value={agg} onChange={(e) => setAgg(e.target.value)}>{["mean", "median", "first", "last", "min", "max"].map((a) => <option key={a}>{a}</option>)}</select></label>
        <label className="field">Fill gaps<select value={fill} onChange={(e) => setFill(e.target.value)}>{["none", "forward", "interpolate", "zero"].map((a) => <option key={a}>{a}</option>)}</select></label>
      </div>
      <div className="row"><span className="small muted">Drop:</span>{detail!.variables.map((v) => <span key={v} className={`chip${drops.includes(v) ? " warn" : ""}`} style={{ cursor: "pointer" }} onClick={() => setDrops(drops.includes(v) ? drops.filter((x) => x !== v) : [...drops, v])}>{v}</span>)}</div>
      {Object.keys(proc.fill_overrides).length > 0 && <div className="small muted">Per-variable fills: {Object.entries(proc.fill_overrides).map(([k, v]) => `${k} → ${v}`).join(", ")}</div>}
      <div><button className="primary" onClick={async () => {
        try { await api.processing(name, { resample_every: every || null, aggregate: agg, fill, drop_variables: drops }); toast("Processing settings saved"); reload(); }
        catch (e) { toast((e as Error).message); }
      }}>Save</button></div>
    </div>
  );
}

function AdvisorPanel() {
  const { name, where, reload } = useCohort();
  const status = useAsync(() => api.adviseStatus(name, where), [name, where.join("|")]);
  const [model, setModel] = useState(""); const [baseUrl, setBaseUrl] = useState(""); const [key, setKey] = useState("");
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [rename, setRename] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const st = status.data;
  const renames = rec?.variables.filter((v) => v.canonical_name && v.canonical_name !== v.variable) ?? [];
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="muted small">Optional. Any OpenAI-compatible endpoint (OpenAI, Azure, vLLM, Ollama, LiteLLM…). The model only ever receives the profile below: aggregate statistics, no encounter ids, timestamps or values.</p>
      <div className="grid c3">
        <label className="field">Model<input value={model} placeholder={st?.model ?? "model"} onChange={(e) => setModel(e.target.value)} /></label>
        <label className="field">Base URL<input value={baseUrl} placeholder={st?.base_url ?? "https://api.openai.com/v1"} onChange={(e) => setBaseUrl(e.target.value)} /></label>
        <label className="field">API key<input type="password" value={key} placeholder={st?.configured ? "from environment" : ""} onChange={(e) => setKey(e.target.value)} /></label>
      </div>
      <details><summary>What will be sent ({st ? `${JSON.stringify(st.payload).length.toLocaleString()} bytes` : "…"})</summary>
        <pre className="mono" style={{ maxHeight: 280, overflow: "auto", background: "var(--surface-2)", padding: 10, borderRadius: 8 }}>{st ? JSON.stringify(st.payload, null, 1) : ""}</pre></details>
      {st && !st.installed && <Alert>Install <code>dea[ai]</code> to enable the advisor.</Alert>}
      <div className="row">
        <button className="primary" disabled={busy || !st?.installed || (!st?.configured && !key && !baseUrl)} onClick={async () => {
          setBusy(true); setErr(null);
          try { setRec(await api.advise(name, where, { model: model || undefined, base_url: baseUrl || undefined, api_key: key || undefined })); }
          catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
        }}>{busy ? <Spinner label="Asking…" /> : "Get recommendations"}</button>
        {!st?.configured && !key && !baseUrl && st?.installed && <span className="small muted">set an API key or base URL</span>}
      </div>
      {err && <Alert kind="danger">{err}</Alert>}
      <MappingProposals name={name} where={where} creds={{ model: model || undefined, base_url: baseUrl || undefined, api_key: key || undefined }} enabled={!!st?.installed && (!!st?.configured || !!key || !!baseUrl)} onApplied={reload} />
      {rec && (
        <div className="stack" style={{ gap: 10 }}>
          <p>{rec.summary}</p>
          <div className="kpis"><Kpi label="recommended grid" value={rec.resample_every} /><Kpi label="aggregate" value={rec.aggregate} /><Kpi label="default fill" value={rec.default_fill} /></div>
          <div className="table-wrap" style={{ maxHeight: 300 }}><table className="table"><thead><tr><th>Variable</th><th>Label</th><th>Canonical</th><th>Role</th><th>Unit</th><th>Fill</th><th>Why</th></tr></thead>
            <tbody>{rec.variables.map((v) => <tr key={v.variable}><td>{v.variable}</td><td>{v.label}</td><td><code>{v.canonical_name}</code></td><td>{v.role}</td><td>{v.unit_guess}</td><td>{v.imputation}</td><td style={{ whiteSpace: "normal" }}>{v.rationale}</td></tr>)}</tbody></table></div>
          {rec.suggested_filters.map((f) => <div key={f.label} className="small"><b>{f.label}</b> <code>{f.where}</code> {f.description}</div>)}
          {rec.warnings.map((w) => <Alert key={w}>{w}</Alert>)}
          <div className="row">
            {renames.length > 0 && <label className="row small"><input type="checkbox" checked={rename} onChange={(e) => setRename(e.target.checked)} /> also rename {renames.length} variable(s) to canonical names (rewrites the cohort)</label>}
            <button className="primary" disabled={busy} onClick={async () => {
              setBusy(true);
              try { await api.adviseApply(name, rec, rename); toast("Recommendation applied"); setRec(null); reload(); }
              catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
            }}>Apply to cohort</button>
          </div>
        </div>
      )}
    </div>
  );
}


function ImportCard({ name }: { name: string }) {
  const rep = useAsync(() => api.importReport(name), [name]);
  const r = rep.data && "format" in rep.data ? rep.data : null;
  if (!r) return null;
  const units = r.unit_reports ?? [];
  return (
    <Card title="At import">
      <div className="row" style={{ marginBottom: 8 }}>
        <span className="chip on">{r.format}</span>
        <span className="chip">{r.n_encounters} encounters</span>
        <span className="chip">{r.n_observations.toLocaleString()} observations</span>
        {Object.entries(r.mapping ?? {}).filter(([, v]) => v).map(([k, v]) => <span key={k} className="chip"><span className="k">{k}</span> {v}</span>)}
      </div>
      {r.warnings?.map((w) => <Alert key={w}>{w}</Alert>)}
      {units.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <b className="small">Units harmonized</b>
          <ul className="small muted" style={{ margin: "4px 0 0 16px" }}>
            {units.map((u) => (
              <li key={u.variable}><b>{u.variable}</b> → {u.label || u.canonical}
                {Object.entries(u.converted).map(([k, n]) => ` · ${n.toLocaleString()} rows from ${k}`)}
                {Object.entries(u.unconvertible).map(([k, n]) => ` · ${n.toLocaleString()} rows in ${k} not convertible`)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <b className="small">Checks at import</b>
      <ChecksList checks={r.checks ?? []} empty="No findings at import." />
    </Card>
  );
}


function MappingProposals({ name, where, creds, enabled, onApplied }: { name: string; where: string[]; creds: { model?: string; base_url?: string; api_key?: string }; enabled: boolean; onApplied: () => void }) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ proposals: { variable: string; concept: string; confidence: number; rationale: string }[]; mapping: Record<string, string>; n_unmatched: number } | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const selected = Object.entries(chosen).filter(([, on]) => on).map(([v]) => v);
  return (
    <div className="stack" style={{ gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      <div className="row spread">
        <div><b className="small">Bulk mapping</b><div className="small muted">Ask the model for a dictionary concept for every variable the offline matcher could not place. It sees names, units and ranges only.</div></div>
        <button className="sm" disabled={busy || !enabled} onClick={async () => {
          setBusy(true); setErr(null);
          try { const r = await api.adviseMap(name, where, creds); setRes(r); setChosen(Object.fromEntries(Object.keys(r.mapping).map((v) => [v, true]))); }
          catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
        }}>{busy ? <Spinner label="Mapping…" /> : "Propose mappings"}</button>
      </div>
      {err && <Alert kind="danger">{err}</Alert>}
      {res && res.n_unmatched === 0 && <p className="muted small">Every variable already matches the dictionary.</p>}
      {res && res.proposals.length > 0 && (
        <>
          <div className="table-wrap" style={{ maxHeight: 260 }}>
            <table className="table"><thead><tr><th></th><th>Variable</th><th>Concept</th><th>Confidence</th><th>Why</th></tr></thead>
              <tbody>{res.proposals.map((p) => (
                <tr key={p.variable}>
                  <td>{p.concept && <input type="checkbox" checked={!!chosen[p.variable]} onChange={(e) => setChosen({ ...chosen, [p.variable]: e.target.checked })} />}</td>
                  <td>{p.variable}</td><td>{p.concept ? <code>{p.concept}</code> : <span className="muted">no match</span>}</td>
                  <td className="num">{Math.round(p.confidence * 100)}%</td><td style={{ whiteSpace: "normal" }}>{p.rationale}</td>
                </tr>))}</tbody></table>
          </div>
          <div className="row">
            <button className="primary sm" disabled={busy || selected.length === 0} onClick={async () => {
              setBusy(true);
              try {
                const mapping = Object.fromEntries(res.proposals.filter((p) => chosen[p.variable] && p.concept).map((p) => [p.variable, p.concept]));
                const r = await api.applyMapping(name, { mapping }); toast(`Mapped ${r.applied.length} variable(s)`); setRes(null); onApplied();
              } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
            }}>Apply {selected.length} mapping(s)</button>
            <span className="small muted">rewrites the cohort; labels, units and LOINC codes follow the concept</span>
          </div>
        </>
      )}
    </div>
  );
}
