import { useState } from "react";
import { useCohort } from "../App";
import { api, fmt } from "../api";
import { FilterBar } from "../components/FilterBuilder";
import { Card, Kpi, Spinner, useAsync, Alert } from "../components/ui";
import { VegaChart } from "../components/VegaChart";

export function Overview() {
  const { name, detail, where, setWhere, error } = useCohort();
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
