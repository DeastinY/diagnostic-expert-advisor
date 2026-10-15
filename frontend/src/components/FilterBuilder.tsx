import { useEffect, useMemo, useState } from "react";
import { api, type CatalogEntry, type Where } from "../api";
import { Spinner, toast } from "./ui";

const NUM_OPS = ["<", "<=", ">", ">=", "=", "!="];

function quote(name: string) { return /^[a-z_][a-z0-9_]*$/i.test(name) ? name : `"${name}"`; }

/** Builds one SQL condition from the feature catalog with a live encounter count. */
export function ConditionBuilder({ cohort, where, catalog, onAdd, onSave }: {
  cohort: string; where: Where; catalog: CatalogEntry[];
  onAdd: (cond: string) => void;
  onSave?: (cond: string, kind: "filter" | "tag", label: string) => void;
}) {
  const [feature, setFeature] = useState<string>(catalog[0]?.name ?? "");
  const [op, setOp] = useState("<");
  const [value, setValue] = useState("");
  const [label, setLabel] = useState("");
  const [count, setCount] = useState<{ n: number; n_total: number } | null>(null);
  const entry = useMemo(() => catalog.find((c) => c.name === feature), [catalog, feature]);

  useEffect(() => {
    if (entry?.kind === "categorical") { setOp("="); setValue(entry.categories?.[0] ?? ""); }
    else if (entry?.kind === "boolean") { setOp("="); setValue("true"); }
    else { setOp("<"); setValue(entry?.max != null ? String(Math.round(((entry.min ?? 0) + entry.max) / 2)) : ""); }
  }, [entry]);

  const cond = useMemo(() => {
    if (!entry || value === "") return "";
    const v = entry.kind === "categorical" ? `'${value.replace(/'/g, "''")}'` : value;
    return `${quote(entry.name)} ${op} ${v}`;
  }, [entry, op, value]);

  useEffect(() => {
    if (!cond) { setCount(null); return; }
    const t = setTimeout(() => {
      api.count(cohort, [...where, cond]).then(setCount).catch(() => setCount(null));
    }, 250);
    return () => clearTimeout(t);
  }, [cohort, where, cond]);

  const groups = { static: "Static", stay: "Stay", aggregate: "Measurements" } as const;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="builder">
        <label className="field">Feature
          <select value={feature} onChange={(e) => setFeature(e.target.value)}>
            {(Object.keys(groups) as (keyof typeof groups)[]).map((g) => (
              <optgroup key={g} label={groups[g]}>
                {catalog.filter((c) => c.source === g).map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
        <label className="field">Operator
          <select value={op} onChange={(e) => setOp(e.target.value)}>
            {(entry?.kind === "numeric" ? NUM_OPS : ["=", "!="]).map((o) => <option key={o}>{o}</option>)}
          </select>
        </label>
        <label className="field">Value
          {entry?.kind === "categorical" ? (
            <select value={value} onChange={(e) => setValue(e.target.value)}>
              {entry.categories?.map((c) => <option key={c}>{c}</option>)}
            </select>
          ) : entry?.kind === "boolean" ? (
            <select value={value} onChange={(e) => setValue(e.target.value)}><option>true</option><option>false</option></select>
          ) : (
            <input type="number" value={value} onChange={(e) => setValue(e.target.value)}
              placeholder={entry?.min != null ? `${entry.min} – ${entry.max}` : ""} />
          )}
        </label>
        <button className="primary" disabled={!cond} onClick={() => cond && onAdd(cond)}>Add</button>
      </div>
      <div className="row small muted">
        {cond && <code>{cond}</code>}
        {cond && (count ? <span>→ <b>{count.n}</b> of {count.n_total} encounters</span> : <Spinner label="counting…" />)}
      </div>
      {onSave && cond && (
        <div className="row">
          <input placeholder="Name this rule (e.g. Severe ARDS)" value={label} onChange={(e) => setLabel(e.target.value)} style={{ minWidth: 260 }} />
          <button className="sm" disabled={!label} onClick={() => { onSave(cond, "filter", label); setLabel(""); toast(`Saved filter “${label}”`); }}>Save as filter</button>
          <button className="sm" disabled={!label} onClick={() => { onSave(cond, "tag", label); setLabel(""); toast(`Saved tag “${label}”`); }}>Save as tag</button>
        </div>
      )}
    </div>
  );
}

/** Active filters as chips plus the configured filters as toggles. */
export function FilterBar({ where, configFilters, setWhere, count }: {
  where: Where; configFilters: { label: string; where: string }[];
  setWhere: (w: Where) => void; count?: { n: number; n_total: number } | null;
}) {
  const known = new Map(configFilters.map((f) => [f.where, f.label]));
  return (
    <div className="row" style={{ gap: 6 }}>
      {configFilters.map((f) => {
        const on = where.includes(f.where);
        return (
          <span key={f.label} className={`chip${on ? " on" : ""}`} style={{ cursor: "pointer" }} title={f.where}
            onClick={() => setWhere(on ? where.filter((w) => w !== f.where) : [...where, f.where])}>
            {f.label}
          </span>
        );
      })}
      {where.filter((w) => !known.has(w)).map((w) => (
        <span key={w} className="chip on"><code style={{ background: "transparent", padding: 0 }}>{w}</code>
          <button onClick={() => setWhere(where.filter((x) => x !== w))} aria-label="remove">×</button></span>
      ))}
      {where.length > 0 && <button className="ghost sm" onClick={() => setWhere([])}>Clear</button>}
      {count && <span className="small muted">{count.n} of {count.n_total} encounters</span>}
    </div>
  );
}
