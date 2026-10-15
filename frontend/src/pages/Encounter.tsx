import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useCohort } from "../App";
import { api, hours } from "../api";
import { Alert, Card, Spinner, useAsync } from "../components/ui";
import { VegaChart } from "../components/VegaChart";

export function Encounter() {
  const { name, detail } = useCohort();
  const { eid = "" } = useParams();
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [grid, setGrid] = useState<string>("");
  const enc = useAsync(() => api.encounter(name, eid), [name, eid]);
  const vars = chosen ?? enc.data?.plots ?? [];
  const chart = useAsync(() => (enc.data ? api.encounterChart(name, eid, vars, grid || undefined) : Promise.resolve(null)), [name, eid, vars.join("|"), grid, !!enc.data]);
  if (enc.error) return <Alert kind="danger">{enc.error}</Alert>;
  if (!enc.data || !detail) return <Spinner />;
  const e = enc.data;
  const labels = detail.config.variable_labels;
  const toggle = (v: string) => setChosen(vars.includes(v) ? vars.filter((x) => x !== v) : [...vars, v]);
  return (
    <>
      <div className="topbar">
        <div><div className="small"><Link to={`/c/${name}/encounters`}>← Encounters</Link></div><h1>Encounter {e.id}</h1></div>
        <select value={grid} onChange={(ev) => setGrid(ev.target.value)}><option value="">raw observations</option>{["5m", "15m", "1h", "4h"].map((g) => <option key={g} value={g}>{g} grid</option>)}</select>
      </div>
      <div className="stack">
        <Card>
          <div className="kpis">
            <div className="kpi"><div className="v">{hours(e.los_hours)}</div><div className="l">stay</div></div>
            <div className="kpi"><div className="v">{e.n_observations.toLocaleString()}</div><div className="l">observations</div></div>
            {Object.entries(e.static).slice(0, 8).map(([k, v]) => <div key={k} className="kpi"><div className="v" style={{ fontSize: 18 }}>{v == null ? "–" : String(v)}</div><div className="l">{k.replace(/_/g, " ")}</div></div>)}
          </div>
        </Card>
        <Card title="Time series">
          <div className="row" style={{ marginBottom: 10 }}>
            {e.variables.map((v) => <span key={v} className={`chip${vars.includes(v) ? " on" : ""}`} style={{ cursor: "pointer" }} onClick={() => toggle(v)}>{labels[v] ?? v}</span>)}
          </div>
          {chart.loading ? <Spinner /> : <VegaChart spec={chart.data?.spec} />}
        </Card>
        {e.events.length > 0 && (
          <Card title={`Events (${e.events.length})`}>
            <div className="table-wrap" style={{ maxHeight: 320 }}>
              <table className="table"><thead><tr><th>Hour</th><th>Variable</th><th>Value</th></tr></thead>
                <tbody>{e.events.map((ev, i) => <tr key={i}><td className="num">{ev.hours.toFixed(1)}</td><td>{ev.variable}</td><td style={{ whiteSpace: "normal" }}>{ev.value}</td></tr>)}</tbody></table>
            </div>
          </Card>
        )}
      </div>
    </>
  );
}
