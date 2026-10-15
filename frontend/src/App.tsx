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
        {detail && (
          <div className="small muted" style={{ marginTop: "auto" }}>
            {detail.n_total.toLocaleString()} encounters · {detail.variables.length} variables
            {detail.config.parent && <div>derived from <code>{detail.config.parent.split("/").pop()}</code></div>}
          </div>
        )}
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
