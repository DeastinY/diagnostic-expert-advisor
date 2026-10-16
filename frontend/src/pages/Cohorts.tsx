import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { buildTree, CohortCard } from "../components/CohortCard";
import { DemoForm, ImportForm } from "../components/ImportForms";
import { Alert, Card, Spinner, Tabs, toast, useAsync } from "../components/ui";

const STEPS = [
  ["Throw data in", "CSV, Parquet, a MIMIC-IV directory or FHIR bundles. DEA detects the layout, the time unit and the units of every variable."],
  ["Check and harmonize", "Mixed units, implausible values, duplicates and site differences are caught before anything is written. LOINC, UCUM and ICD codes come for free."],
  ["Look, build, export", "Profiles, trajectories and per-site views out of the box. Build sub-cohorts with a click, then take a Polars or pandas frame to your models, or FHIR to your colleagues."],
];

export function Cohorts() {
  const nav = useNavigate();
  const { data, error, loading, reload } = useAsync(() => api.cohorts(), []);
  const [tab, setTab] = useState("Import");
  const [query, setQuery] = useState("");
  const tree = useMemo(() => buildTree(data ?? [], query), [data, query]);
  const totals = useMemo(() => ({
    cohorts: data?.length ?? 0,
    encounters: (data ?? []).reduce((a, c) => a + c.n_encounters, 0),
    warnings: (data ?? []).reduce((a, c) => a + c.n_warnings, 0),
  }), [data]);
  return (
    <>
      <section className="hero">
        <div>
          <div className="eyebrow">Diagnostic Expert Advisor</div>
          <h1 className="hero-title">Bring your cohort. Get structure, checks and views in minutes.</h1>
          <p className="muted hero-sub">A workbench for clinical time series: import anything, harmonize it to standards, see every patient and every site, and hand clean frames to your models. Everything runs locally; the AI advisor only ever sees aggregate statistics.</p>
        </div>
        <div className="hero-stats">
          <div><b>{totals.cohorts}</b><span>cohorts</span></div>
          <div><b>{totals.encounters.toLocaleString()}</b><span>encounters</span></div>
          <div><b>{totals.warnings}</b><span>open findings</span></div>
        </div>
      </section>
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 3fr) minmax(320px, 2fr)", alignItems: "start" }}>
        <div className="stack">
          <div className="row spread">
            <h2 style={{ margin: 0 }}>Your cohorts</h2>
            {data && data.length > 3 && <input placeholder="filter by name…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ width: 220 }} />}
          </div>
          {error && <Alert kind="danger">{error}</Alert>}
          {loading && <Spinner />}
          {data?.length === 0 && (
            <div className="card empty">
              <h2>No cohorts yet</h2>
              <p className="muted">Import a file or folder on the right, or generate a demo cohort to see what DEA does.</p>
              <div className="steps">{STEPS.map(([t, d], i) => <div key={t} className="step"><span className="n">{i + 1}</span><div><b>{t}</b><div className="small muted">{d}</div></div></div>)}</div>
            </div>
          )}
          <div className="cohort-grid">
            {tree.map(({ c, depth }) => <CohortCard key={c.name} c={c} depth={depth} onOpen={() => nav(`/c/${c.name}`)} onDelete={async () => {
              if (!confirm(`Delete cohort “${c.title}” and all its files?`)) return;
              await api.deleteCohort(c.name); toast("Cohort deleted"); reload();
            }} />)}
          </div>
          {data && data.length > 0 && (
            <div className="steps compact">{STEPS.map(([t, d], i) => <div key={t} className="step"><span className="n">{i + 1}</span><div><b>{t}</b><div className="small muted">{d}</div></div></div>)}</div>
          )}
        </div>
        <Card title="Add data">
          <Tabs tabs={["Import", "Demo"]} value={tab} onChange={setTab} />
          {tab === "Demo" ? <DemoForm onDone={(n) => nav(`/c/${n}`)} /> : <ImportForm onDone={(n) => nav(`/c/${n}/profile`)} />}
        </Card>
      </div>
    </>
  );
}
