import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCohort } from "../App";
import { api, fmt, hours } from "../api";
import { FilterBar } from "../components/FilterBuilder";
import { Alert, Card, Spinner, useAsync } from "../components/ui";

export function Encounters() {
  const { name, detail, where, setWhere } = useCohort();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState("encounter_id");
  const [desc, setDesc] = useState(false);
  const size = 25;
  const { data, error, loading } = useAsync(() => api.encounters(name, where, { q, page, size, sort, desc }), [name, where.join("|"), q, page, sort, desc]);
  const keep = where.length ? `?${new URLSearchParams(where.map((w) => ["where", w])).toString()}` : "";
  if (!detail) return <Spinner />;
  const hidden = new Set(["encounter_id"]);
  const cols = (data?.columns ?? []).filter((c) => !hidden.has(c));
  const tags = new Set(data?.tags ?? []);
  const pages = data ? Math.max(1, Math.ceil(data.total / size)) : 1;
  const header = (c: string) => (
    <th key={c} onClick={() => { if (sort === c) setDesc(!desc); else { setSort(c); setDesc(false); } }}>
      {c === "los_hours" ? "Stay" : c === "n_observations" ? "Observations" : c.replace(/_/g, " ")}{sort === c ? (desc ? " ↓" : " ↑") : ""}
    </th>
  );
  return (
    <>
      <div className="topbar"><div><h1>Encounters</h1><p className="muted">{data ? `${data.total.toLocaleString()} encounters` : ""}</p></div>
        <input placeholder="Search id…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
      <Card flat><FilterBar where={where} configFilters={detail.config.filters} setWhere={setWhere} /></Card>
      {error && <Alert kind="danger">{error}</Alert>}
      <Card>
        {loading && !data ? <Spinner /> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr>{header("encounter_id")}{cols.map(header)}</tr></thead>
              <tbody>
                {data?.rows.map((r) => (
                  <tr key={String(r.encounter_id)} onClick={() => nav(`/c/${name}/encounters/${encodeURIComponent(String(r.encounter_id))}${keep}`)} style={{ cursor: "pointer" }}>
                    <td><a>{String(r.encounter_id)}</a></td>
                    {cols.map((c) => {
                      const v = r[c];
                      if (tags.has(c)) return <td key={c}>{v ? <span className="chip on">{c}</span> : ""}</td>;
                      if (c === "los_hours") return <td key={c} className="num">{hours(Number(v))}</td>;
                      if (typeof v === "number") return <td key={c} className="num">{fmt(v, Number.isInteger(v) ? 0 : 1)}</td>;
                      return <td key={c}>{v == null ? "" : String(v)}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pager">
          <button className="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
          <span>page {page} of {pages}</span>
          <button className="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      </Card>
    </>
  );
}
