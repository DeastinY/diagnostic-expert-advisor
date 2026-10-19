import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { PanelChart } from "./Dashboard";
import { Card, Kpi, Spinner, useAsync } from "./ui";

/** What you got, right after an import: the numbers, who the patients are, and the
 *  distributions of everything that was read, before the overview is even opened. */
export function ImportSummary({ name, onClose }: { name: string; onClose: () => void }) {
  const nav = useNavigate();
  const detail = useAsync(() => api.cohort(name), [name]);
  const d = detail.data;
  if (detail.loading && !d) return <Card title={`“${name}” in numbers`}><Spinner /></Card>;
  if (!d) return null;
  const s = d.summary;
  const cols = d.static_columns;
  const find = (names: string[]) => cols.find((c) => names.includes(c.toLowerCase()));
  const age = find(["age", "anchor_age", "age_years"]);
  const sex = find(["sex", "gender"]);
  const outcome = find(["outcome", "mortality", "hospital_expire_flag", "died", "death"]);
  const site = d.config.site_column ?? find(["first_careunit", "site", "hospital", "unit", "ward"]);
  return (
    <Card title={`“${name}” in numbers`} actions={
      <div className="row">
        <button className="primary sm" onClick={() => nav(`/c/${name}`)}>Open the overview</button>
        <button className="sm" onClick={() => nav(`/c/${name}/profile`)}>Remaining findings</button>
        <button className="ghost sm" onClick={onClose}>hide</button>
      </div>}>
      <div className="kpis" style={{ marginBottom: 14 }}>
        <Kpi label="Encounters" value={s.n_encounters.toLocaleString()} />
        <Kpi label="Variables" value={s.n_variables} />
        <Kpi label="Observations" value={s.n_observations.toLocaleString()} />
        <Kpi label="Event types" value={d.event_variables.length} />
        <Kpi label="Interventions" value={d.interval_variables.length} />
        <Kpi label="Median stay" value={s.median_stay} />
        <Kpi label="Longest stay" value={s.longest_stay} />
      </div>
      <div className="dash">
        <section className="card panel s2"><div className="phead"><h2>Length of stay</h2></div><PanelChart name={name} kind="los" params={{}} height={200} /></section>
        <section className="card panel s2"><div className="phead"><h2>Most observed variables</h2></div><PanelChart name={name} kind="observations" params={{ n: 10 }} height={200} /></section>
        {age && <section className="card panel s1"><div className="phead"><h2>{d.config.variable_labels[age] ?? age}</h2></div><PanelChart name={name} kind="static" params={{ column: age }} height={180} /></section>}
        {sex && <section className="card panel s1"><div className="phead"><h2>{d.config.variable_labels[sex] ?? sex}</h2></div><PanelChart name={name} kind="static" params={{ column: sex }} height={180} /></section>}
        {outcome && <section className="card panel s1"><div className="phead"><h2>{d.config.variable_labels[outcome] ?? outcome}</h2></div><PanelChart name={name} kind="static" params={{ column: outcome }} height={180} /></section>}
        {site && <section className="card panel s1"><div className="phead"><h2>{d.config.variable_labels[site] ?? site}</h2></div><PanelChart name={name} kind="static" params={{ column: site }} height={180} /></section>}
        <section className="card panel s4"><div className="phead"><h2>Distributions</h2><span className="sub">the 24 most observed variables, each on its own scale</span></div><PanelChart name={name} kind="gallery" params={{ n: 24 }} /></section>
        <section className="card panel s4"><div className="phead"><h2>When is what measured</h2><span className="sub">share of encounters with data per variable and 4-hour window of the first week</span></div><PanelChart name={name} kind="coverage" params={{ max_hours: 168, variables: 30 }} /></section>
      </div>
    </Card>
  );
}
