import { type CohortInfo } from "../api";

export function buildTree(list: CohortInfo[], query: string): { c: CohortInfo; depth: number }[] {
  const q = query.trim().toLowerCase();
  const byName = new Map(list.map((c) => [c.name, c]));
  const children = new Map<string | null, CohortInfo[]>();
  for (const c of list) {
    const parent = c.parent ? c.parent.split("/").pop()! : null;
    const key = parent && byName.has(parent) ? parent : null;
    (children.get(key) ?? children.set(key, []).get(key)!).push(c);
  }
  const out: { c: CohortInfo; depth: number }[] = [];
  const walk = (key: string | null, depth: number) => {
    for (const c of (children.get(key) ?? []).sort((a, b) => b.created_at.localeCompare(a.created_at))) {
      if (!q || c.title.toLowerCase().includes(q) || c.description.toLowerCase().includes(q)) out.push({ c, depth });
      walk(c.name, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

function Spark({ bins }: { bins: number[] | null }) {
  if (!bins || bins.length === 0) return <div className="spark empty" title="Length-of-stay distribution appears after the first profile" />;
  const max = Math.max(...bins, 1);
  return (
    <div className="spark" title="Length of stay, 0–30 days">
      {bins.map((b, i) => <span key={i} style={{ height: `${Math.max(8, (b / max) * 100)}%` }} />)}
    </div>
  );
}

export function CohortCard({ c, depth, onOpen, onDelete }: { c: CohortInfo; depth: number; onOpen: () => void; onDelete: () => void }) {
  const when = new Date(c.created_at);
  return (
    <div className="card cohort-card" style={{ marginLeft: depth * 22 }} onClick={onOpen} role="link" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen()}>
      <div className="cohort-head">
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            {depth > 0 && <span className="muted">↳</span>}
            <b className="cohort-title">{c.title}</b>
            {c.format && <span className="chip">{c.format}</span>}
            {c.site_column && <span className="chip on" title={`site column ${c.site_column}`}>multi-site</span>}
            {c.n_warnings > 0 && <span className="chip warn">{c.n_warnings} finding{c.n_warnings > 1 ? "s" : ""}</span>}
          </div>
          <div className="small muted ellipsis">{c.derived_by ? <code>{c.derived_by}</code> : (c.description || c.source || "—")}</div>
        </div>
        <button className="ghost sm" title="Delete cohort" onClick={(e) => { e.stopPropagation(); onDelete(); }}>×</button>
      </div>
      <div className="cohort-body">
        <div className="cohort-kpis">
          <div><b>{c.n_encounters.toLocaleString()}</b><span>encounters</span></div>
          <div><b>{c.n_variables}</b><span>variables</span></div>
          <div><b>{c.n_tags}</b><span>tags</span></div>
        </div>
        <Spark bins={c.los_hist} />
      </div>
      <div className="small muted cohort-foot">{when.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}{c.n_filters ? ` · ${c.n_filters} saved filter${c.n_filters > 1 ? "s" : ""}` : ""}</div>
    </div>
  );
}
