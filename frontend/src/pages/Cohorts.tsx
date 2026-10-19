import { useMemo, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { buildTree, CohortCard } from "../components/CohortCard";
import { DemoForm } from "../components/ImportForms";
import { fromDataTransfer, ImportWizard, type Picked } from "../components/ImportWizard";
import { Alert, Card, Spinner, Tabs, toast, useAsync } from "../components/ui";

const STEPS = [
  ["Drop your data in", "A MIMIC-IV download, FHIR resources, a CSV or Parquet table. DEA works out the layout, the time unit and the units of every variable."],
  ["Look at the findings, then import", "Mixed units, values out of range, duplicates and differences between hospitals are reported before anything is written. LOINC, UCUM and ICD codes come with it."],
  ["Explore, build, export", "Overview, distributions, per-hospital views and a page per patient are ready right away. Build sub-cohorts with a click, then take a Polars or pandas frame to your models."],
];

export function Cohorts() {
  const nav = useNavigate();
  const { data, error, loading, reload } = useAsync(() => api.cohorts(), []);
  const [tab, setTab] = useState("Import");
  const [query, setQuery] = useState("");
  const [dropped, setDropped] = useState<Picked[] | null>(null);
  const [over, setOver] = useState(false);
  const tree = useMemo(() => buildTree(data ?? [], query), [data, query]);
  const totals = useMemo(() => ({
    cohorts: data?.length ?? 0,
    encounters: (data ?? []).reduce((a, c) => a + c.n_encounters, 0),
    warnings: (data ?? []).reduce((a, c) => a + c.n_warnings, 0),
  }), [data]);
  // dropping anywhere on the page hands the files to the wizard
  const onDrop = async (e: DragEvent) => {
    e.preventDefault(); setOver(false);
    if (!e.dataTransfer.types.includes("Files")) return;
    setTab("Import");
    setDropped(await fromDataTransfer(e.dataTransfer));
  };
  const empty = data?.length === 0;
  return (
    <div
      className={`droppable${over ? " over" : ""}`}
      onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setOver(true); } }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setOver(false); }}
      onDrop={onDrop}
    >
      <section className="hero">
        <div>
          <div className="eyebrow">Diagnostic Expert Advisor</div>
          <h1 className="hero-title">Drop your data in. Checks and plots come with it.</h1>
          <p className="muted hero-sub">A lightweight toolkit for clinical time series. Drop a folder or a file anywhere on this page, look at what DEA found, import, and you have an overview, distributions, per-hospital views and a page per patient. Everything runs on this machine; the optional AI advisor only ever sees aggregates.</p>
        </div>
        <div className="hero-stats">
          <div><b>{totals.cohorts}</b><span>cohorts</span></div>
          <div><b>{totals.encounters.toLocaleString()}</b><span>encounters</span></div>
          <div><b>{totals.warnings}</b><span>open findings</span></div>
        </div>
      </section>
      <div className="grid" style={{ gridTemplateColumns: empty ? "minmax(320px, 3fr) minmax(0, 2fr)" : "minmax(0, 3fr) minmax(360px, 2fr)", alignItems: "start" }}>
        {empty ? (
          <>
            <Card title="Add data">
              <Tabs tabs={["Import", "Demo"]} value={tab} onChange={setTab} />
              {tab === "Demo" ? <DemoForm onDone={(n) => nav(`/c/${n}`)} /> : <ImportWizard dropped={dropped} onMore={reload} onImported={reload} onDone={(n, where) => nav(where === "profile" ? `/c/${n}/profile` : `/c/${n}`)} />}
            </Card>
            <div className="card empty" style={{ textAlign: "left", padding: "22px 24px" }}>
              <h2 style={{ marginTop: 0 }}>How it goes</h2>
              <div className="steps" style={{ gridTemplateColumns: "1fr" }}>{STEPS.map(([t, d], i) => <div key={t} className="step"><span className="n">{i + 1}</span><div><b>{t}</b><div className="small muted">{d}</div></div></div>)}</div>
              <p className="small muted" style={{ marginTop: 16 }}>No data at hand? The <b>Demo</b> tab generates a synthetic cohort over three hospitals, no real patients.</p>
            </div>
          </>
        ) : (
          <>
            <div className="stack">
              <div className="row spread">
                <h2 style={{ margin: 0 }}>Your cohorts</h2>
                {data && data.length > 3 && <input placeholder="filter by name…" value={query} onChange={(e) => setQuery(e.target.value)} style={{ width: 220 }} />}
              </div>
              {error && <Alert kind="danger">{error}</Alert>}
              {loading && <Spinner />}
              <div className="cohort-grid">
                {tree.map(({ c, depth }) => <CohortCard key={c.name} c={c} depth={depth} onOpen={() => nav(`/c/${c.name}`)} onDelete={async () => {
                  if (!confirm(`Delete cohort “${c.title}” and all its files?`)) return;
                  await api.deleteCohort(c.name); toast("Cohort deleted"); reload();
                }} />)}
              </div>
              <div className="steps compact">{STEPS.map(([t, d], i) => <div key={t} className="step"><span className="n">{i + 1}</span><div><b>{t}</b><div className="small muted">{d}</div></div></div>)}</div>
            </div>
            <Card title="Add data">
              <Tabs tabs={["Import", "Demo"]} value={tab} onChange={setTab} />
              {tab === "Demo" ? <DemoForm onDone={(n) => nav(`/c/${n}`)} /> : <ImportWizard dropped={dropped} onMore={reload} onImported={reload} onDone={(n, where) => nav(where === "profile" ? `/c/${n}/profile` : `/c/${n}`)} />}
            </Card>
          </>
        )}
        {loading && empty === undefined && <Spinner />}
      </div>
      {over && <div className="drop-veil">Drop to add the data</div>}
    </div>
  );
}
