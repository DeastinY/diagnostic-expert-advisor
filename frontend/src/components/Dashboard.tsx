import { useEffect, useMemo, useState, type DragEvent } from "react";
import { api, type CohortDetail, type Panel, type PanelDef, type PanelParamDef, type Where } from "../api";
import { Alert, Spinner, toast, useAsync } from "./ui";
import { VegaChart } from "./VegaChart";

/** The overview as a grid of panels: add from a catalogue, resize, reorder, remove.
 *  The layout is saved in the cohort's config; an empty list means DEA's default. */

const SIZES = [{ v: 1, label: "S" }, { v: 2, label: "M" }, { v: 3, label: "L" }, { v: 4, label: "XL" }];
const EVERY = ["15m", "30m", "1h", "4h", "12h", "1d"];
const uid = () => Math.random().toString(36).slice(2, 8);
type Params = Record<string, unknown>;

/** A readable title from the kind and its parameters, unless the user named the panel. */
export function panelTitle(p: Panel, def: PanelDef | undefined, detail: CohortDetail): string {
  if (p.title) return p.title;
  const lbl = (v: unknown) => (typeof v === "string" ? detail.config.variable_labels[v] ?? v : "");
  const q = p.params;
  switch (p.kind) {
    case "static": return q.column ? lbl(q.column) : "Static attribute";
    case "crosstab": return q.column && q.by ? `${lbl(q.column)} by ${lbl(q.by)}` : "One attribute by another";
    case "variable": return q.variable ? `${lbl(q.variable)} across encounters${q.by ? ` by ${lbl(q.by)}` : ""}` : "Variable across encounters";
    case "trajectory": return q.variable ? `${lbl(q.variable)} over time${q.by ? `, by ${lbl(q.by)}` : ""}` : "Trajectory";
    default: return def?.label ?? p.kind;
  }
}

/** One parameter picker, by the catalogue's type. */
function ParamControl({ def, value, detail, onChange }: { def: PanelParamDef; value: unknown; detail: CohortDetail; onChange: (v: unknown) => void }) {
  const lbl = (v: string) => detail.config.variable_labels[v] ?? v;
  const v = value ?? def.default ?? "";
  switch (def.type) {
    case "variable":
      return <select value={String(v)} onChange={(e) => onChange(e.target.value)}>{!def.required && <option value="">variable…</option>}{detail.variables.map((c) => <option key={c} value={c}>{lbl(c)}</option>)}</select>;
    case "static":
    case "static_categorical":
      return <select value={String(v)} onChange={(e) => onChange(e.target.value || undefined)}>{!def.required && <option value="">{def.name === "by" ? "no split" : "column…"}</option>}{detail.static_columns.map((c) => <option key={c} value={c}>{lbl(c)}</option>)}</select>;
    case "every":
      return <select value={String(v || "1h")} onChange={(e) => onChange(e.target.value)}>{EVERY.map((g) => <option key={g}>{g}</option>)}</select>;
    case "number":
      return <input type="number" value={v === "" ? "" : Number(v)} min={1} style={{ width: 80 }} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />;
    case "bool":
      return <label className="row" style={{ gap: 4 }}><input type="checkbox" checked={!!v} onChange={(e) => onChange(e.target.checked)} /> {def.name === "normalize" ? "as shares" : def.name}</label>;
    default:
      return null;
  }
}

const PARAM_LABELS: Record<string, string> = { column: "column", by: "by", variable: "variable", every: "grid", max_hours: "hours", n: "rows", variables: "rows", normalize: "" };

/** Just the chart of a panel kind, for places outside the grid (the post-import summary). */
export function PanelChart({ name, kind, params, where = [], height }: { name: string; kind: string; params: Params; where?: Where; height?: number }) {
  const chart = useAsync(() => api.panel(name, kind, params, where), [name, kind, JSON.stringify(params), where.join("|")]);
  if (chart.loading && !chart.data) return <Spinner />;
  if (chart.error) return <Alert kind="danger">{chart.error}</Alert>;
  return (
    <>
      {chart.data?.spec ? <VegaChart spec={chart.data.spec} height={height} /> : <div className="empty small">{chart.data?.note ?? "No data"}</div>}
      {chart.data?.spec && chart.data.note && <div className="note">{chart.data.note}</div>}
    </>
  );
}

function PanelCard({ name, p, def, detail, where, index, count, onChange, onRemove, onMove, drag }: {
  name: string; p: Panel; def: PanelDef | undefined; detail: CohortDetail; where: Where; index: number; count: number;
  onChange: (next: Panel) => void; onRemove: () => void; onMove: (delta: number) => void;
  drag: { onDragStart: (e: DragEvent) => void; onDragOver: (e: DragEvent) => void; onDrop: (e: DragEvent) => void; onDragEnd: () => void; dragging: boolean; over: boolean };
}) {
  const [renaming, setRenaming] = useState(false);
  const params = def?.params ?? [];
  const setParam = (k: string, v: unknown) => onChange({ ...p, params: { ...p.params, [k]: v } });
  return (
    <section className={`card panel s${p.size}${drag.dragging ? " dragging" : ""}${drag.over ? " over" : ""}`} onDragOver={drag.onDragOver} onDrop={drag.onDrop}>
      <div className="phead">
        <div className="row" style={{ gap: 6, minWidth: 0 }}>
          <span className="grip" draggable onDragStart={drag.onDragStart} onDragEnd={drag.onDragEnd} title="Drag to reorder">⋮⋮</span>
          {renaming ? (
            <input autoFocus defaultValue={panelTitle(p, def, detail)} style={{ font: "inherit", fontWeight: 600 }}
              onBlur={(e) => { setRenaming(false); onChange({ ...p, title: e.target.value.trim() || null }); }}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setRenaming(false); }} />
          ) : (
            <h2 title="Double-click to rename" onDoubleClick={() => setRenaming(true)}>{panelTitle(p, def, detail)}</h2>
          )}
        </div>
        <div className="tools">
          <button type="button" onClick={() => onMove(-1)} disabled={index === 0} title="Move earlier">‹</button>
          <button type="button" onClick={() => onMove(1)} disabled={index === count - 1} title="Move later">›</button>
          <span className="seg">{SIZES.map((s) => <button key={s.v} type="button" className={p.size === s.v ? "on" : ""} onClick={() => onChange({ ...p, size: s.v })} title={`${s.v} of 4 columns`}>{s.label}</button>)}</span>
          <button type="button" onClick={onRemove} title="Remove this plot">×</button>
        </div>
      </div>
      {params.length > 0 && (
        <div className="pparams">
          {params.map((d) => (
            <span key={d.name} className="row" style={{ gap: 4 }}>
              {PARAM_LABELS[d.name] ?? d.name}{PARAM_LABELS[d.name] === "" ? "" : ":"}
              <ParamControl def={d} value={p.params[d.name]} detail={detail} onChange={(v) => setParam(d.name, v)} />
            </span>
          ))}
        </div>
      )}
      <PanelChart name={name} kind={p.kind} params={p.params} where={where} height={p.size >= 3 ? 240 : 200} />
    </section>
  );
}

/** Pick a plot from the catalogue, with search over kinds and over variable names. */
function AddPlot({ catalog, detail, onAdd, onClose }: { catalog: PanelDef[]; detail: CohortDetail; onAdd: (p: Panel) => void; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<string | null>(null);
  const [params, setParams] = useState<Params>({});
  const [size, setSize] = useState<number | null>(null);
  const lbl = (v: string) => detail.config.variable_labels[v] ?? v;
  const needle = q.trim().toLowerCase();
  const kinds = useMemo(() => catalog.filter((d) => !needle || `${d.label} ${d.description} ${d.kind}`.toLowerCase().includes(needle)), [catalog, needle]);
  const variables = useMemo(() => (needle.length >= 2 ? detail.variables.filter((v) => lbl(v).toLowerCase().includes(needle)).slice(0, 8) : []), [detail, needle]); // eslint-disable-line react-hooks/exhaustive-deps
  const statics = useMemo(() => (needle.length >= 2 ? detail.static_columns.filter((v) => lbl(v).toLowerCase().includes(needle)).slice(0, 6) : []), [detail, needle]); // eslint-disable-line react-hooks/exhaustive-deps
  const def = catalog.find((d) => d.kind === kind);
  const ready = !!def && def.params.filter((d) => d.required).every((d) => params[d.name] !== undefined && params[d.name] !== "");
  const pick = (k: string, preset: Params = {}) => {
    const d = catalog.find((x) => x.kind === k);
    const defaults: Params = {};
    for (const pd of d?.params ?? []) if (pd.default !== undefined) defaults[pd.name] = pd.default;
    setKind(k); setParams({ ...defaults, ...preset }); setSize(null);
  };
  const add = () => { if (!def || !ready) return; onAdd({ id: uid(), kind: def.kind, params, size: size ?? def.size, title: null }); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    addEventListener("keydown", onKey); return () => removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onClick={onClose}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Add a plot" onClick={(e) => e.stopPropagation()}>
        <div className="row spread"><h2 style={{ margin: 0 }}>Add a plot</h2><button type="button" className="ghost sm" onClick={onClose}>close</button></div>
        <input className="search" autoFocus placeholder="Search plots, variables or static columns: trajectory, heart rate, outcome…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="cols">
          <div>
            <div className="kinds">
              {kinds.map((d) => <button type="button" key={d.kind} className={`kind${kind === d.kind ? " on" : ""}`} onClick={() => pick(d.kind)}><b>{d.label}</b><span>{d.description}</span></button>)}
              {kinds.length === 0 && variables.length === 0 && statics.length === 0 && <div className="muted small">Nothing matches.</div>}
            </div>
            {(variables.length > 0 || statics.length > 0) && (
              <div className="quick">
                {variables.length > 0 && <div className="small muted" style={{ marginBottom: 4 }}>Variables</div>}
                {variables.map((v) => (
                  <span key={v}>
                    <span className="chip" onClick={() => pick("trajectory", { variable: v })}>{lbl(v)} <span className="k">over time</span></span>
                    <span className="chip" onClick={() => pick("variable", { variable: v })}>{lbl(v)} <span className="k">across encounters</span></span>
                  </span>
                ))}
                {statics.length > 0 && <div className="small muted" style={{ margin: "8px 0 4px" }}>Static columns</div>}
                {statics.map((c) => <span key={c} className="chip" onClick={() => pick("static", { column: c })}>{lbl(c)} <span className="k">distribution</span></span>)}
              </div>
            )}
          </div>
          <div className="side">
            {!def && <p className="muted small">Pick a kind of plot on the left, or search for a variable.</p>}
            {def && (
              <>
                <h2 style={{ marginTop: 0 }}>{def.label}</h2>
                <p className="muted small">{def.description}</p>
                {def.params.map((d) => (
                  <label key={d.name} className="field">{PARAM_LABELS[d.name] || d.name}{d.required ? "" : " (optional)"}
                    <ParamControl def={d} value={params[d.name]} detail={detail} onChange={(v) => setParams({ ...params, [d.name]: v })} />
                  </label>
                ))}
                <label className="field">width
                  <span className="row" style={{ gap: 4 }}>{SIZES.map((s) => <button type="button" key={s.v} className={`sm${(size ?? def.size) === s.v ? " primary" : ""}`} onClick={() => setSize(s.v)}>{s.label}</button>)}</span>
                </label>
                <button type="button" className="primary" disabled={!ready} onClick={add} style={{ marginTop: 8 }}>Add to the overview</button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function Dashboard({ name, detail, where }: { name: string; detail: CohortDetail; where: Where }) {
  const dash = useAsync(() => api.dashboard(name), [name]);
  const [panels, setPanels] = useState<Panel[] | null>(null);
  const [isDefault, setIsDefault] = useState(true);
  const [adding, setAdding] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [overIdx, setOverIdx] = useState<number | null>(null);
  useEffect(() => { if (dash.data) { setPanels(dash.data.panels); setIsDefault(dash.data.default); } }, [dash.data]);
  const catalog = dash.data?.catalog ?? [];
  const byKind = useMemo(() => new Map(catalog.map((d) => [d.kind, d])), [catalog]);
  const save = async (next: Panel[]) => {
    setPanels(next); setIsDefault(false);
    try { await api.setDashboard(name, next); } catch (e) { toast((e as Error).message); }
  };
  const reset = async () => {
    try { await api.setDashboard(name, []); dash.reload(); toast("Overview reset to DEA's default"); } catch (e) { toast((e as Error).message); }
  };
  const move = (from: number, to: number) => {
    if (!panels || to < 0 || to >= panels.length || from === to) return;
    const next = [...panels]; const [p] = next.splice(from, 1); next.splice(to, 0, p); save(next);
  };
  if (dash.error) return <Alert kind="danger">{dash.error}</Alert>;
  if (!panels) return <Spinner />;
  return (
    <>
      <div className="row spread dash-bar">
        <div><b>Plots</b> <span className="muted small">{panels.length} on the overview{isDefault ? " · DEA's default, change anything" : " · your layout"}</span></div>
        <div className="row">
          <button type="button" className="ghost sm" onClick={reset} disabled={isDefault}>Reset to default</button>
          <button type="button" className="primary" onClick={() => setAdding(true)}>+ Add plot</button>
        </div>
      </div>
      {adding && <AddPlot catalog={catalog} detail={detail} onClose={() => setAdding(false)} onAdd={(p) => { save([...panels, p]); setAdding(false); }} />}
      <div className="dash">
        {panels.map((p, i) => (
          <PanelCard key={p.id} name={name} p={p} def={byKind.get(p.kind)} detail={detail} where={where} index={i} count={panels.length}
            onChange={(next) => save(panels.map((x) => (x.id === p.id ? next : x)))}
            onRemove={() => save(panels.filter((x) => x.id !== p.id))}
            onMove={(d) => move(i, i + d)}
            drag={{
              dragging: dragIdx === i, over: overIdx === i && dragIdx !== null && dragIdx !== i,
              onDragStart: (e) => { setDragIdx(i); e.dataTransfer.effectAllowed = "move"; },
              onDragOver: (e) => { if (dragIdx !== null) { e.preventDefault(); setOverIdx(i); } },
              onDrop: (e) => { e.preventDefault(); if (dragIdx !== null) move(dragIdx, i); setDragIdx(null); setOverIdx(null); },
              onDragEnd: () => { setDragIdx(null); setOverIdx(null); },
            }} />
        ))}
        {panels.length === 0 && <section className="card panel s4 empty-panel"><div className="empty">No plots yet. Add one, or reset to DEA's default.</div></section>}
      </div>
    </>
  );
}
