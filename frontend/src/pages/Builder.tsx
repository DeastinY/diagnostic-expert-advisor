import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCohort } from "../App";
import { api } from "../api";
import { ConditionBuilder, FilterBar } from "../components/FilterBuilder";
import { Alert, Card, Kpi, Spinner, toast, useAsync } from "../components/ui";

export function Builder() {
  const { name, detail, where, setWhere, reload } = useCohort();
  const nav = useNavigate();
  const catalog = useAsync(() => api.catalog(name), [name]);
  const count = useAsync(() => api.count(name, where), [name, where.join("|")]);
  const [subName, setSubName] = useState("");
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  if (!detail) return <Spinner />;
  const save = async (cond: string, kind: "filter" | "tag", label: string) => {
    await api.addRule(name, { kind, label, where: cond }); reload();
  };
  return (
    <>
      <div className="topbar"><div><h1>Build a cohort</h1><p className="muted">Combine conditions on static data, stay statistics and measurement aggregates. Save them as reusable filters or tags, or carve out a sub-cohort.</p></div></div>
      <div className="stack">
        <Card title="Conditions (combined with AND)">
          <FilterBar where={where} configFilters={detail.config.filters} setWhere={setWhere} />
          <div style={{ height: 12 }} />
          {catalog.error && <Alert kind="danger">{catalog.error}</Alert>}
          {catalog.data ? <ConditionBuilder cohort={name} where={where} catalog={catalog.data} onAdd={(c) => setWhere([...where, c])} onSave={save} /> : <Spinner />}
        </Card>
        <div className="grid c2">
          <Card title="Selection">
            <div className="kpis">
              <Kpi label="encounters selected" value={count.data ? count.data.n.toLocaleString() : "…"} delta={count.data ? `of ${count.data.n_total.toLocaleString()}` : undefined} />
              <Kpi label="conditions" value={where.length} />
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <button onClick={() => nav(`/c/${name}?${new URLSearchParams(where.map((w) => ["where", w]))}`)}>View overview</button>
              <button onClick={() => nav(`/c/${name}/encounters?${new URLSearchParams(where.map((w) => ["where", w]))}`)}>View encounters</button>
            </div>
          </Card>
          <Card title="Save as sub-cohort">
            <p className="muted small">Writes the selected encounters as a new cohort with its parent and rule recorded.</p>
            <div className="stack" style={{ gap: 8 }}>
              <input placeholder="name (folder name)" value={subName} onChange={(e) => setSubName(e.target.value.replace(/[^a-zA-Z0-9_-]/g, "_"))} />
              <input placeholder="description (optional)" value={desc} onChange={(e) => setDesc(e.target.value)} />
              <button className="primary" disabled={!subName || where.length === 0 || busy} onClick={async () => {
                setBusy(true);
                try { const r = await api.derive(name, { name: subName, where, description: desc }); toast(`Created ${r.name} with ${r.n_encounters} encounters`); nav(`/c/${r.name}`); }
                catch (e) { toast((e as Error).message); } finally { setBusy(false); }
              }}>{busy ? <Spinner label="Writing…" /> : "Create sub-cohort"}</button>
            </div>
          </Card>
        </div>
        <DatasetCard />
        <Card title="Saved filters and tags">
          <div className="grid c2">
            {(["filters", "tags"] as const).map((k) => (
              <div key={k}>
                <h3>{k}</h3>
                {detail.config[k].length === 0 && <p className="muted small">none yet</p>}
                {detail.config[k].map((r) => (
                  <div key={r.label} className="row spread" style={{ padding: "6px 0", borderBottom: "1px solid var(--border)" }}>
                    <div><b>{r.label}</b> <code>{r.where}</code>{r.description && <div className="small muted">{r.description}</div>}</div>
                    <button className="ghost sm" onClick={async () => { await api.deleteRule(name, k === "filters" ? "filter" : "tag", r.label); reload(); }}>remove</button>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </Card>
      </div>
      <ExportCard />
    </>
  );
}


/** Downloads of the current selection; they used to crowd the overview's top bar. */
function ExportCard() {
  const { name, detail, where } = useCohort();
  if (!detail) return null;
  const cfg = detail.config;
  return (
    <Card title="Export and report">
      <p className="muted small">The current selection as a wide Parquet table on the processing grid, as FHIR R4 NDJSON, or as a one-page HTML report.</p>
      <div className="row">
        <a className="btn sm" href={api.exportUrl(name, where, cfg.processing.resample_every ?? "1h", cfg.processing.fill)}>Export Parquet</a>
        <a className="btn sm" href={api.fhirExportUrl(name, where)}>Export FHIR</a>
        <a className="btn sm" href={api.reportUrl(name, where)} target="_blank" rel="noreferrer">Report</a>
      </div>
    </Card>
  );
}


function DatasetCard() {
  const { name, detail, where } = useCohort();
  const tags = detail?.config.tags ?? [];
  const [label, setLabel] = useState("");
  const [cutoff, setCutoff] = useState<string>("24");
  const [split, setSplit] = useState("encounter");
  const chosen = label || tags[0]?.label || "";
  const params = { label: chosen, cutoff: cutoff ? Number(cutoff) : undefined, split };
  const sum = useAsync(() => (detail && chosen ? api.mlSummary(name, where, params) : Promise.resolve(null)), [name, where.join("|"), chosen, cutoff, split, !!detail]);
  if (!detail) return null;
  return (
    <Card title="Dataset for modelling">
      <p className="muted small">One row per encounter: features from before the cutoff only, a boolean label evaluated on the whole stay, and a train/test split. Outcome-like and label columns are dropped.</p>
      <div className="grid c3">
        <label className="field">Label
          <select value={chosen} onChange={(e) => setLabel(e.target.value)}>
            {tags.map((t) => <option key={t.label} value={t.label}>{t.label}</option>)}
            {detail.static_columns.filter((c) => c === "outcome").map((c) => <option key={c} value={`${c} = 'deceased'`}>{c} = deceased</option>)}
          </select>
        </label>
        <label className="field">Cutoff (hours after admission)<input type="number" min={1} value={cutoff} onChange={(e) => setCutoff(e.target.value)} placeholder="whole stay" /></label>
        <label className="field">Split
          <select value={split} onChange={(e) => setSplit(e.target.value)}>
            <option value="encounter">random by encounter</option>
            {detail.config.site_column && <option value="site">hold out whole sites</option>}
            {detail.static_columns.includes("patient_id") && <option value="group:patient_id">by patient</option>}
          </select>
        </label>
      </div>
      {sum.error && <Alert kind="danger">{sum.error}</Alert>}
      {sum.loading && <Spinner />}
      {sum.data && (
        <div className="stack" style={{ gap: 6 }}>
          <div className="kpis">
            <Kpi label="encounters" value={sum.data.n} /><Kpi label="features" value={sum.data.n_features} /><Kpi label="positives" value={sum.data.positives} />
            <Kpi label="train / test" value={`${sum.data.train} / ${sum.data.test}`} />
          </div>
          {sum.data.dropped.length > 0 && <div className="small muted">Dropped to avoid leakage: {sum.data.dropped.join(", ")}</div>}
          <div className="row">
            <a className="btn primary" href={api.mlUrl(name, where, params)}>Download Parquet</a>
            <code className="small">dea ml data/{name} out.parquet --label "{chosen}"{cutoff ? ` --cutoff ${cutoff}` : ""}{split !== "encounter" ? ` --split ${split}` : ""}</code>
          </div>
        </div>
      )}
    </Card>
  );
}
