import { useState } from "react";
import { useCohort } from "../App";
import { api, fmt } from "../api";
import { FilterBar } from "../components/FilterBuilder";
import { Card, ChecksList, Kpi, Spinner, toast, useAsync, Alert } from "../components/ui";
import { VegaChart } from "../components/VegaChart";

export function Overview() {
  const { name, detail, where, setWhere, error, reload } = useCohort();
  const [staticCol, setStaticCol] = useState<string>("");
  const [variable, setVariable] = useState<string>("");
  const [by, setBy] = useState<string>("");
  const [tvar, setTvar] = useState<string>("");
  const [grid, setGrid] = useState("1h");
  const [maxHours, setMaxHours] = useState(168);
  const [tby, setTby] = useState("");
  const cfg = detail?.config;
  const sc = staticCol || detail?.static_columns[0] || "";
  const v = variable || cfg?.plots[0] || detail?.variables[0] || "";
  const tv = tvar || cfg?.plots[0] || detail?.variables[0] || "";
  const charts = useAsync(() => (detail ? api.overviewCharts(name, where, { static_column: sc, variable: v, by: by || undefined }) : Promise.resolve(null)), [name, where.join("|"), sc, v, by, !!detail]);
  const traj = useAsync(() => (detail && tv ? api.trajectory(name, where, { variable: tv, every: grid, max_hours: maxHours, by: tby || undefined }) : Promise.resolve(null)), [name, where.join("|"), tv, grid, maxHours, tby, !!detail]);
  if (error) return <Alert kind="danger">{error}</Alert>;
  if (!detail) return <Spinner />;
  const s = detail.summary;
  const filtered = where.length > 0;
  return (
    <>
      <div className="topbar">
        <div><h1>{cfg!.name}</h1><p className="muted">{cfg!.description}</p></div>
        <div className="row">
          <a className="btn sm" href={api.reportUrl(name, where)} target="_blank" rel="noreferrer">Report</a>
          <a className="btn sm" href={api.exportUrl(name, where, cfg!.processing.resample_every ?? "1h", cfg!.processing.fill)}>Export Parquet</a>
          <a className="btn sm" href={api.fhirExportUrl(name, where)}>Export FHIR</a>
        </div>
      </div>
      <Card flat>
        <FilterBar where={where} configFilters={cfg!.filters} setWhere={setWhere} count={{ n: s.n_encounters, n_total: detail.n_total }} />
      </Card>
      <div className="stack" style={{ marginTop: 16 }}>
        <Card>
          <div className="kpis">
            <Kpi label="Encounters" value={s.n_encounters.toLocaleString()} delta={filtered ? `of ${detail.n_total.toLocaleString()}` : undefined} />
            <Kpi label="Variables" value={s.n_variables} />
            <Kpi label="Observations" value={s.n_observations.toLocaleString()} />
            <Kpi label="Median stay" value={s.median_stay} />
            <Kpi label="Longest stay" value={s.longest_stay} />
            <Kpi label="Events" value={detail.event_variables.length} />
          </div>
        </Card>
        {charts.error && <Alert kind="danger">{charts.error}</Alert>}
        <div className="grid c2">
          <Card title="Length of stay">{charts.loading ? <Spinner /> : <VegaChart spec={charts.data?.los} height={220} />}</Card>
          {charts.data?.tags ? <Card title="Tags"><VegaChart spec={charts.data.tags} /></Card> : <Card title="Tags"><p className="muted small">No tags configured yet. Add them in Build cohort or apply the suggestions.</p></Card>}
        </div>
        {filtered && (
          <Card title="Selection versus the rest of the cohort">
            <p className="muted small">Share of observations per bin for the selected encounters and for everyone else, on shared bins. Nine most observed variables.</p>
            {charts.loading ? <Spinner /> : <VegaChart spec={charts.data?.selection} />}
          </Card>
        )}
        <Card title="When is what measured?">
          <p className="muted small">Share of encounters still in the unit that have data for each variable, per 4-hour window of the first week.</p>
          {charts.loading ? <Spinner /> : <VegaChart spec={charts.data?.coverage_heatmap} />}
        </Card>
        <div className="grid c2">
          <Card title="Static column" actions={
            <select value={sc} onChange={(e) => setStaticCol(e.target.value)}>{detail.static_columns.map((c) => <option key={c}>{c}</option>)}</select>}>
            <VegaChart spec={charts.data?.static} height={200} />
          </Card>
          <Card title="Variable across encounters" actions={
            <div className="row">
              <select value={v} onChange={(e) => setVariable(e.target.value)}>{detail.variables.map((c) => <option key={c} value={c}>{cfg!.variable_labels[c] ?? c}</option>)}</select>
              <select value={by} onChange={(e) => setBy(e.target.value)}><option value="">no split</option>{detail.static_columns.map((c) => <option key={c}>{c}</option>)}</select>
            </div>}>
            <VegaChart spec={charts.data?.variable} height={200} />
          </Card>
        </div>
        <SitesCard name={name} where={where} configured={cfg!.site_column} onChange={reload} />
        <Card title="Trajectories" actions={
          <div className="row">
            <select value={tv} onChange={(e) => setTvar(e.target.value)}>{detail.variables.map((c) => <option key={c} value={c}>{cfg!.variable_labels[c] ?? c}</option>)}</select>
            <select value={grid} onChange={(e) => setGrid(e.target.value)}>{["1h", "4h", "12h", "1d"].map((g) => <option key={g}>{g}</option>)}</select>
            <input type="number" value={maxHours} min={6} step={24} style={{ width: 90 }} onChange={(e) => setMaxHours(Number(e.target.value))} title="max hours" />
            <select value={tby} onChange={(e) => setTby(e.target.value)}><option value="">no split</option>{detail.static_columns.map((c) => <option key={c}>{c}</option>)}</select>
          </div>}>
          <p className="muted small">Median and interquartile band across encounters by time since admission{traj.data?.rows ? ` · ${fmt(traj.data.rows, 0)} cells` : ""}.</p>
          {traj.loading ? <Spinner /> : <VegaChart spec={traj.data?.spec} height={260} />}
        </Card>
      </div>
    </>
  );
}


function SitesCard({ name, where, configured, onChange }: { name: string; where: string[]; configured: string | null; onChange: () => void }) {
  const [column, setColumn] = useState<string>("");
  const col = column || configured || "";
  const cmp = useAsync(() => api.sites(name, where, col || undefined), [name, where.join("|"), col]);
  const d = cmp.data;
  if (cmp.loading && !d) return <Card title="Across sites"><Spinner /></Card>;
  if (!d || (!d.column && d.candidates.length === 0)) return null;
  const pick = (
    <div className="row">
      <select value={d.column ?? ""} onChange={(e) => setColumn(e.target.value)}>
        <option value="">choose a site column</option>
        {d.candidates.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      {d.column && d.column !== d.configured && (
        <button className="sm" onClick={async () => { await api.setSite(name, d.column); toast(`Site column set to ${d.column}`); onChange(); }}>Use as site column</button>
      )}
    </div>
  );
  if (!d.column) return <Card title="Across sites" actions={pick}><p className="muted small">Pick the static column that names the hospital, site or export to compare distributions, coverage and sampling between them.</p></Card>;
  const warn = d.checks.filter((c) => c.severity !== "info").length;
  return (
    <Card title={`Across sites · ${d.column}`} actions={pick}>
      <div className="row" style={{ marginBottom: 8 }}>
        {(d.sizes ?? []).slice(0, 12).map((r) => <span key={String(r[d.column!])} className="chip">{String(r[d.column!])} <span className="k">{r.n}</span></span>)}
        {(d.sizes?.length ?? 0) > 12 && <span className="chip">+{d.sizes!.length - 12} more</span>}
        {warn > 0 && <span className="chip warn">{warn} finding(s)</span>}
      </div>
      {d.casemix && d.casemix.length > 0 && (
        <div className="table-wrap" style={{ maxHeight: 260, marginBottom: 10 }}>
          <table className="table"><thead><tr>{Object.keys(d.casemix[0]).map((k) => <th key={k}>{k.replace(/^dx_/, "").replace(/_/g, " ")}</th>)}</tr></thead>
            <tbody>{d.casemix.map((r, i) => <tr key={i}>{Object.entries(r).map(([k, v]) => <td key={k} className={typeof v === "number" ? "num" : undefined}>{typeof v === "number" ? (k.startsWith("dx_") || k.endsWith("_share") || k === "mortality" ? `${Math.round(v * 100)}%` : fmt(v)) : String(v ?? "–")}</td>)}</tr>)}</tbody></table>
        </div>
      )}
      <p className="muted small">Each curve is the share of that site's observations per bin, so sites of different size compare directly. Twelve most observed variables, from a sample of encounters per site{d.shown && d.shown.overlay_sites < d.shown.of ? `, ${d.shown.overlay_sites} largest sites` : ""}.</p>
      {cmp.loading ? <Spinner /> : <VegaChart spec={d.charts.distributions} />}
      <div className="grid c2" style={{ marginTop: 12 }}>
        <div><VegaChart spec={d.charts.coverage} /></div>
        <div>{d.charts.sampling ? <VegaChart spec={d.charts.sampling} /> : <p className="muted small" style={{ marginTop: 28 }}>No repeated measurements within encounters, so there is no sampling interval to compare.</p>}</div>
      </div>
      {d.charts.los && <div style={{ marginTop: 12 }}><VegaChart spec={d.charts.los} height={200} /></div>}
      <div style={{ marginTop: 12 }}>
        <b className="small">Findings</b>
        <ChecksList checks={d.checks} empty="No systematic differences between sites." />
      </div>
    </Card>
  );
}
