import { useCohort } from "../App";
import { Dashboard, PanelChart } from "../components/Dashboard";
import { FilterBar } from "../components/FilterBuilder";
import { Alert, Card, Kpi, Spinner } from "../components/ui";

/** The cohort's front page: the numbers, the filter, and a grid of plots the user composes. */
export function Overview() {
  const { name, detail, where, setWhere, error } = useCohort();
  if (error) return <Alert kind="danger">{error}</Alert>;
  if (!detail) return <Spinner />;
  const cfg = detail.config;
  const s = detail.summary;
  const filtered = where.length > 0;
  return (
    <>
      <div className="topbar">
        <div><h1>{cfg.name}</h1><p className="muted">{cfg.description}</p></div>
      </div>
      <Card flat>
        <FilterBar where={where} configFilters={cfg.filters} setWhere={setWhere} count={{ n: s.n_encounters, n_total: detail.n_total }} />
      </Card>
      <div className="stack" style={{ marginTop: 16 }}>
        <Card>
          <div className="kpis">
            <Kpi label="Encounters" value={s.n_encounters.toLocaleString()} delta={filtered ? `of ${detail.n_total.toLocaleString()}` : undefined} />
            <Kpi label="Variables" value={s.n_variables} />
            <Kpi label="Observations" value={s.n_observations.toLocaleString()} />
            <Kpi label="Median stay" value={s.median_stay} />
            <Kpi label="Longest stay" value={s.longest_stay} />
            <Kpi label="Event types" value={detail.event_variables.length} />
          </div>
        </Card>
        {filtered && (
          <Card title="Selection versus the rest of the cohort">
            <p className="muted small">Share of observations per bin for the selected encounters and for everyone else, on shared bins. Nine most observed variables.</p>
            <PanelChart name={name} kind="selection" params={{}} where={where} />
          </Card>
        )}
        <Dashboard name={name} detail={detail} where={where} />
      </div>
    </>
  );
}
