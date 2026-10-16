import { createContext, useContext, type ReactNode } from "react";
import { NavLink, Outlet, useParams, useSearchParams } from "react-router-dom";
import { api, type CohortDetail, type Where } from "./api";
import { ToastHost, useAsync } from "./components/ui";

interface Ctx { name: string; detail: CohortDetail | null; where: Where; setWhere: (w: Where) => void; reload: () => void; error: string | null }
const CohortCtx = createContext<Ctx | null>(null);
export function useCohort(): Ctx {
  const c = useContext(CohortCtx);
  if (!c) throw new Error("outside cohort layout");
  return c;
}

export function Shell({ children }: { children?: ReactNode }) {
  const params = useParams();
  const name = params.name;
  const [search, setSearch] = useSearchParams();
  const where = search.getAll("where");
  const setWhere = (w: Where) => {
    const next = new URLSearchParams(search);
    next.delete("where");
    w.forEach((x) => next.append("where", x));
    setSearch(next, { replace: true });
  };
  const { data: detail, error, reload } = useAsync(() => (name ? api.cohort(name, where) : Promise.resolve(null)), [name, where.join("|")]);
  const keep = where.length ? `?${new URLSearchParams(where.map((w) => ["where", w])).toString()}` : "";
  const health = useAsync(() => api.health(), []);
  const upd = health.data?.update;
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="dot" />DEA</div>
        <nav className="nav">
          <NavLink to="/" end>Cohorts</NavLink>
          {name && (
            <>
              <div className="group">{detail?.config.name ?? name}</div>
              <NavLink to={`/c/${name}${keep}`} end>Overview</NavLink>
              <NavLink to={`/c/${name}/encounters${keep}`}>Encounters</NavLink>
              <NavLink to={`/c/${name}/build${keep}`}>Build cohort</NavLink>
              <NavLink to={`/c/${name}/profile${keep}`}>Profile &amp; suggestions</NavLink>
            </>
          )}
        </nav>
        <div className="small muted" style={{ marginTop: "auto" }}>
          {detail && (
            <>
              {detail.n_total.toLocaleString()} encounters · {detail.variables.length} variables
              {detail.config.parent && <div>derived from <code>{detail.config.parent.split("/").pop()}</code></div>}
            </>
          )}
          {health.data && (
            <div style={{ marginTop: 10 }} title={upd?.commit ? `${upd.mode} install at ${upd.commit.slice(0, 9)}` : undefined}>
              v{health.data.version}{upd?.commit ? ` · ${upd.commit.slice(0, 7)}` : ""}
              {upd?.update_available && (
                <div className="chip warn" style={{ marginTop: 6 }} title={upd.command.map((c) => c.join(" ")).join(" && ")}>
                  update available · run <code>dea update</code>
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
      <main className="main">
        <CohortCtx.Provider value={{ name: name ?? "", detail, where, setWhere, reload, error }}>
          {children ?? <Outlet />}
        </CohortCtx.Provider>
      </main>
      <ToastHost />
    </div>
  );
}
