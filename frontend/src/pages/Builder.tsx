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
    </>
  );
}
