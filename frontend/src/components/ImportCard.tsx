import { api } from "../api";
import { Alert, Card, ChecksList, useAsync } from "./ui";

export function ImportCard({ name }: { name: string }) {
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
