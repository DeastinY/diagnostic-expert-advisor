import { useMemo, useState } from "react";
import type { Check, Hist, Series, UnitReport } from "../api";
import { SeverityChip, Spinner } from "./ui";

/** The findings of a cohort, fixes biggest first, with everything else folded away.
 *  Used by the import wizard (before anything is written) and by the profile page. */

export const num = (n: number) => n.toLocaleString();
const SEV_W = { error: 3, warning: 2, info: 1 } as const;
const TOP = 5;

type Fix = { where: string; expr: string | null };
export type FixLoader = (variable: string, fix?: Fix) => Promise<Hist>;
export type SeriesLoader = (variable: string, encounter: string) => Promise<Series>;
type Option = NonNullable<Check["options"]>[number];
type Row = { encounter: string; hours: number; value: number };
type Decision = "keep" | "drop" | "clamp";

/** The selection entry of a check: its id, or "id#option" / "id#rows=…" carrying the choice. */
export function entryFor(selected: Set<string>, id: string): string | null {
  for (const e of selected) if (e === id || e.startsWith(id + "#")) return e;
  return null;
}

/** The selection with one check's entry replaced; null removes the check. */
export function withChoice(selected: Set<string>, id: string, entry: string | null): Set<string> {
  const n = new Set([...selected].filter((e) => e !== id && !e.startsWith(id + "#")));
  if (entry) n.add(entry);
  return n;
}

/** Where a check comes from, by its id prefix: used to group the list. */
export function family(id: string): string {
  if (id.startsWith("units")) return "Units";
  if (id.startsWith("concept")) return "Concept mismatch";
  if (id.startsWith("range")) return "Values out of range";
  if (id.startsWith("cluster")) return "Two value clusters";
  if (id.startsWith("duplicates")) return "Duplicates";
  if (id.startsWith("time")) return "Timing";
  if (id.startsWith("events-numeric")) return "Numbers stored as text";
  if (id.startsWith("coverage")) return "Rarely recorded variables";
  if (id.startsWith("encounters")) return "Encounters";
  if (id.startsWith("static")) return "Static table";
  if (id.startsWith("site")) return "Differences between sites";
  return "Other";
}

/** One line on what applying a check's fix does. */
export function effect(c: Check, action?: Record<string, unknown> | null): string {
  const a = action ?? c.action ?? {};
  if ("dedupe" in a) return a.dedupe === "exact" ? `removes ${num(c.n)} rows` : a.dedupe === "drop" ? `drops ${num(c.n * 2)} rows` : `resolves ${num(c.n)} rows`;
  if ("drop_time" in a) return `drops ${num(c.n)} rows`;
  if ("parse" in a) return `parses ${num(c.n)} values`;
  if ("drop" in a) return `hides ${num((a.drop as string[]).length)} variables`;
  if ("filter" in a) return "adds a filter";
  if ("unmap" in a) return "forgets the concept";
  if ("fixes" in a) return "row by row";
  if ("fix" in a) return (a.fix as { expr: string | null }).expr ? `${c.options ? "clamps" : "converts"} ${num(c.n)} values` : `drops ${num(c.n)} values`;
  return "";
}

/** Bigger first: severity, then how much of the data a finding touches. */
export function priority(c: Check, total: number): number {
  const share = total ? Math.min(1, c.n / total) : 0;
  return SEV_W[c.severity] * 10 + (c.recommended ? 1 : 0) + share * 5 + Math.log10(c.n + 1) / 10;
}

function share(c: Check, total: number): string {
  if (!total || !c.n || c.id.startsWith("coverage") || c.action && "unmap" in c.action) return "";
  const p = (c.n / total) * 100;
  return p >= 10 ? `${p.toFixed(0)}% of rows` : p >= 0.1 ? `${p.toFixed(1)}% of rows` : "<0.1% of rows";
}

const fmtv = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : Number.isInteger(v) ? String(v) : v.toFixed(2));

export function Verdict({ checks, fixable }: { checks: Check[]; fixable: Check[] }) {
  const errors = checks.filter((c) => c.severity === "error").length;
  const warnings = checks.filter((c) => c.severity === "warning").length;
  const rec = fixable.filter((c) => c.recommended).length;
  let text: string; let cls: string;
  if (errors) { text = `${errors} thing${errors === 1 ? "" : "s"} need${errors === 1 ? "s" : ""} a look before this is useful.`; cls = "danger"; }
  else if (rec) { text = `Looks usable. ${rec} quick fix${rec === 1 ? " is" : "es are"} recommended, the rest can wait.`; cls = "good"; }
  else if (fixable.length) { text = "Looks usable. Nothing needs fixing right away; a few optional fixes below."; cls = "good"; }
  else if (warnings) { text = "Looks usable. Nothing to fix now; a few things to look at later."; cls = "good"; }
  else { text = "Clean. Nothing stood out."; cls = "good"; }
  return <div className={`verdict ${cls} rise`}>{text}</div>;
}

/** One row of bars on a shared axis. */
function Bars({ bins, lo, hi, max, range, H, W, pad, cls }: { bins: Hist["bins"]; lo: number; hi: number; max: number; range: Hist["range"]; H: number; W: number; pad: number; cls?: string }) {
  const x = (v: number) => pad + ((v - lo) / (hi - lo || 1)) * (W - 2 * pad);
  return (
    <>
      {range && <rect x={x(Math.max(lo, range[0]))} y={0} width={Math.max(0, x(Math.min(hi, range[1])) - x(Math.max(lo, range[0])))} height={H} className="band" />}
      {bins.map((b, i) => {
        const inside = !range || (b.lo >= range[0] && b.hi <= range[1]);
        return <rect key={i} x={x(b.lo)} y={H - (b.count / max) * (H - 8)} width={Math.max(1, x(b.hi) - x(b.lo) - 1)} height={(b.count / max) * (H - 8)} className={`bar${inside ? "" : " out"}${cls ? ` ${cls}` : ""}`} />;
      })}
    </>
  );
}

/** A small histogram with the concept's plausible range shaded; with a fix, before and after,
 *  each on its own window (a unit conversion moves the whole picture). */
export function HistChart({ h }: { h: Hist }) {
  const W = 420, H = 90, pad = 4, head = 14, foot = 16;
  const after = h.after;
  const r = h.range;
  const panel = (bins: Hist["bins"], title: string | null, cls?: string) => {
    const max = Math.max(1, ...bins.map((b) => b.count));
    const lo = bins.length ? bins[0].lo : 0, hi = bins.length ? bins[bins.length - 1].hi : 1;
    const top = title ? head : 0;
    return (
      <svg viewBox={`0 0 ${W} ${H + top + foot}`} width="100%" height={H + top + foot} role="img" aria-label={`${title ?? "distribution"} of ${h.label}`}>
        {title && <text x={pad} y={10} className="sub">{title}</text>}
        <g transform={`translate(0 ${top})`}>
          <Bars bins={bins} lo={lo} hi={hi} max={max} range={r} H={H} W={W} pad={pad} cls={cls} />
          <text x={pad} y={H + 12} className="lbl">{fmtv(lo)}</text>
          <text x={W - pad} y={H + 12} className="lbl" textAnchor="end">{fmtv(hi)}{h.unit ? ` ${h.unit}` : ""}</text>
          {r && <text x={W / 2} y={H + 12} className="lbl mid" textAnchor="middle">plausible {fmtv(r[0])}–{fmtv(r[1])}{h.concept ? ` for ${h.concept}` : ""}</text>}
        </g>
      </svg>
    );
  };
  return (
    <div className="hist rise">
      {panel(h.bins, after ? `before: ${num(h.n)} observations` : null)}
      {after && panel(after.bins, `after the fix: ${num(after.n_changed)} values change, ${num(after.n_unchanged)} stay as they are${after.n < h.n ? `, ${num(h.n - after.n)} rows dropped` : ""}`, "after")}
      {!after && <div className="small muted">{num(h.n)} observations; red bars lie outside the plausible range.</div>}
    </div>
  );
}

/** One encounter's series of a variable with the flagged value marked. */
function SeriesChart({ s, flagged, range }: { s: Series; flagged: number; range: [number, number] | null }) {
  const W = 420, H = 80, pad = 6;
  const pts = s.hours.map((h, i) => [h, s.values[i]] as const).filter((p): p is readonly [number, number] => p[1] != null);
  if (!pts.length) return <div className="small muted">no values</div>;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys, range ? range[0] : Infinity), y1 = Math.max(...ys, range ? range[1] : -Infinity);
  const x = (v: number) => pad + ((v - x0) / (x1 - x0 || 1)) * (W - 2 * pad);
  const y = (v: number) => H - pad - ((v - y0) / (y1 - y0 || 1)) * (H - 2 * pad);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  return (
    <div className="series rise">
      <svg viewBox={`0 0 ${W} ${H + 16}`} width="100%" height={H + 16} role="img" aria-label={`${s.variable} of ${s.encounter}`}>
        {range && <rect x={pad} y={y(Math.min(y1, range[1]))} width={W - 2 * pad} height={Math.max(0, y(Math.max(y0, range[0])) - y(Math.min(y1, range[1])))} className="band" />}
        <path d={d} className="line" />
        {pts.filter((p) => p[1] === flagged).map((p, i) => <circle key={i} cx={x(p[0])} cy={y(p[1])} r={4} className="pt" />)}
        <text x={pad} y={H + 12} className="lbl">{fmtv(x0)} h</text>
        <text x={W - pad} y={H + 12} className="lbl" textAnchor="end">{fmtv(x1)} h</text>
        <text x={W / 2} y={H + 12} className="lbl mid" textAnchor="middle">{s.variable}{s.unit ? ` (${s.unit})` : ""} of {s.encounter}: {pts.length} values, the flagged one in red</text>
      </svg>
    </div>
  );
}

/** The data behind a finding: samples, before/after values, listed rows. */
function Examples({ c }: { c: Check }) {
  const ex = (c.examples ?? {}) as Record<string, unknown>;
  const rows = ex.rows as (Row & { variable?: string; values?: number[] })[] | null | undefined;
  const parsed = ex.parsed as { value: string; n: number }[] | undefined;
  const kept = ex.kept as { value: string; n: number }[] | undefined;
  const before = ex.before as number[] | undefined, after = ex.after as number[] | undefined;
  const unchanged = ex.unchanged as { n: number; sample: number[]; median: number | null } | undefined;
  const bounds = ex.bounds as [number, number] | undefined;
  return (
    <div className="ex">
      {rows && rows.length > 0 && rows[0].values && (
        <table>
          <thead><tr><th>variable</th><th>encounter</th><th>hour</th><th>values recorded</th></tr></thead>
          <tbody>{rows.map((r, i) => <tr key={i}><td>{r.variable}</td><td>{r.encounter}</td><td className="v">{fmtv(r.hours)}</td><td className="v">{r.values!.map(fmtv).join(" · ")}</td></tr>)}</tbody>
        </table>
      )}
      {parsed && parsed.length > 0 && (
        <div>
          <span className="muted">parsed: </span>{parsed.map((p) => <span key={p.value} className="chip">{p.value} <span className="k">×{num(p.n)}</span></span>)}
          {kept && kept.length > 0 && <><span className="muted"> stays text: </span>{kept.map((p) => <span key={p.value} className="chip warn">{p.value} <span className="k">×{num(p.n)}</span></span>)}</>}
        </div>
      )}
      {before && after && (
        <div>
          <span className="muted">for example: </span>{before.map((b, i) => <span key={i} className="chip">{fmtv(b)} → {fmtv(after[i])}</span>)}
          {unchanged && unchanged.n > 0 && <span className="muted"> · {unchanged.n === 1 ? `the one value already in range (${fmtv(unchanged.sample[0])}) stays` : `${num(unchanged.n)} already in range stay${unchanged.median != null ? `, median ${fmtv(unchanged.median)}` : ""}${unchanged.sample.length ? ` (e.g. ${unchanged.sample.map(fmtv).join(", ")})` : ""}`}</span>}
        </div>
      )}
      {before && !after && (
        <div>
          <span className="muted">for example: </span>{before.map((b, i) => <span key={i} className="chip danger">{fmtv(b)}</span>)}
          {bounds && <span className="muted"> outside {fmtv(bounds[0])}–{fmtv(bounds[1])}</span>}
          {unchanged && unchanged.n > 0 && <span className="muted"> · {num(unchanged.n)} in range stay{unchanged.n === 1 ? "s" : ""}{unchanged.median != null && unchanged.n > 1 ? `, median ${fmtv(unchanged.median)}` : ""}</span>}
        </div>
      )}
    </div>
  );
}

/** Decide listed out-of-range rows one by one. */
function RowReview({ c, rows, decisions, onDecide, loadSeries }: { c: Check; rows: Row[]; decisions: Record<string, Decision>; onDecide: (d: Record<string, Decision>) => void; loadSeries?: SeriesLoader }) {
  const [plot, setPlot] = useState<{ key: string; s: Series } | "loading" | null>(null);
  const bounds = (c.examples as { bounds?: [number, number] } | null)?.bounds ?? null;
  const key = (r: Row) => `${r.encounter}:${r.value}`;
  const all = (d: Decision) => onDecide(Object.fromEntries(rows.map((r) => [key(r), d])));
  const show = async (r: Row) => {
    if (!loadSeries) return;
    const k = key(r);
    if (plot && plot !== "loading" && plot.key === k) { setPlot(null); return; }
    setPlot("loading");
    try { setPlot({ key: k, s: await loadSeries(c.variables[0], r.encounter) }); } catch { setPlot(null); }
  };
  return (
    <div className="ex review">
      <div className="row" style={{ gap: 8, marginBottom: 4 }}>
        <span className="muted">{rows.length} values, one decision each:</span>
        <button className="ghost sm link" onClick={() => all("drop")}>drop all</button>
        <button className="ghost sm link" onClick={() => all("clamp")}>clamp all</button>
        <button className="ghost sm link" onClick={() => all("keep")}>keep all</button>
      </div>
      <table>
        <thead><tr><th>encounter</th><th>hour</th><th>value</th><th>decision</th><th></th></tr></thead>
        <tbody>
          {rows.map((r) => {
            const k = key(r);
            const open = plot && plot !== "loading" && plot.key === k;
            return [
              <tr key={k} className={open ? "open" : undefined}>
                <td>{r.encounter}</td><td className="v">{fmtv(r.hours)}</td><td className="v">{fmtv(r.value)}</td>
                <td>
                  <select value={decisions[k] ?? "keep"} onChange={(e) => onDecide({ ...decisions, [k]: e.target.value as Decision })}>
                    <option value="keep">keep</option><option value="drop">drop</option><option value="clamp">clamp{bounds ? ` to ${fmtv(r.value > bounds[1] ? bounds[1] : bounds[0])}` : ""}</option>
                  </select>
                </td>
                <td>{loadSeries && <button className="ghost sm link" onClick={() => show(r)}>{open ? "hide plot" : "plot"}</button>}</td>
              </tr>,
              open ? <tr key={k + ":plot"}><td colSpan={5}><SeriesChart s={plot.s} flagged={r.value} range={bounds} /></td></tr> : null,
            ];
          })}
        </tbody>
      </table>
      {plot === "loading" && <Spinner label="drawing…" />}
    </div>
  );
}

function FixRow({ c, total, entry, busy, onChoose, loadHist, loadSeries }: { c: Check; total: number; entry: string | null; busy: boolean; onChoose: (entry: string | null) => void; loadHist?: FixLoader; loadSeries?: SeriesLoader }) {
  const [hist, setHist] = useState<Hist | null | "loading">(null);
  const [reviewing, setReviewing] = useState(false);
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const on = entry !== null;
  const options = c.options ?? null;
  const rowMode = !!entry && entry.includes("#rows=");
  const optionId = entry && entry.includes("#") && !rowMode ? entry.split("#")[1] : options?.[0]?.id ?? null;
  const option: Option | null = options?.find((o) => o.id === optionId) ?? null;
  const action = option ? option.action : c.action;
  const fix = (action as { fix?: Fix } | null)?.fix;
  const rows = ((c.examples as { rows?: Row[] | null } | null)?.rows ?? null) as Row[] | null;
  const canReview = !!rows && rows.length > 0 && !rows.some((r) => "values" in r) && !!fix;
  const variable = c.variables[0];
  const canShow = !!loadHist && !!variable && !c.id.startsWith("coverage") && !c.id.startsWith("events") && !c.id.startsWith("duplicates");
  const entryForOption = (id: string | null) => (id && options && id !== options[0].id ? `${c.id}#${id}` : c.id);
  const toggleHist = async () => {
    if (hist) { setHist(null); return; }
    setHist("loading");
    try { setHist(await loadHist!(variable, fix ? { where: fix.where, expr: fix.expr } : undefined)); } catch { setHist(null); }
  };
  const decide = (d: Record<string, Decision>) => {
    setDecisions(d);
    const spec = rows!.map((r) => `${r.encounter}:${r.value}:${d[`${r.encounter}:${r.value}`] ?? "keep"}`).join(",");
    onChoose(Object.values(d).some((x) => x !== "keep") ? `${c.id}#rows=${spec}` : null);
  };
  const startReview = () => { setReviewing(true); if (!rowMode) onChoose(null); };
  const stopReview = () => { setReviewing(false); setDecisions({}); if (rowMode) onChoose(null); };
  return (
    <div className={`fix${on ? " on" : ""}`}>
      <input type="checkbox" checked={on} disabled={busy || reviewing} onChange={(e) => onChoose(e.target.checked ? entryForOption(optionId) : null)} />
      <div>
        <div className="t"><SeverityChip severity={c.severity} /> {c.title}</div>
        <div className="d">{c.detail}</div>
        {c.examples && <Examples c={c} />}
        {(options || canReview) && !reviewing && (
          <div className="opts">
            {options && (
              <label className="small muted">how:
                <select value={optionId ?? ""} disabled={busy} onChange={(e) => onChoose(entryForOption(e.target.value))}>
                  {options.map((o) => <option key={o.id} value={o.id} title={o.detail}>{o.label}</option>)}
                </select>
              </label>
            )}
            {option && <span className="small muted">{option.detail}</span>}
            {canReview && <button className="ghost sm link" onClick={startReview}>decide one by one</button>}
          </div>
        )}
        {reviewing && rows && (
          <>
            <RowReview c={c} rows={rows} decisions={decisions} onDecide={decide} loadSeries={loadSeries} />
            <button className="ghost sm link" onClick={stopReview}>back to one decision for all</button>
          </>
        )}
        {canShow && <button className="ghost sm link" onClick={toggleHist}>{hist ? "hide the data" : fix ? "show before and after" : "show the data"}</button>}
        {hist === "loading" && <Spinner label="drawing…" />}
        {hist && hist !== "loading" && <HistChart h={hist} />}
      </div>
      <span className="eff"><span className="fam">{family(c.id)}</span>{rowMode ? `${Object.values(decisions).filter((d) => d !== "keep").length} of ${rows?.length ?? 0} rows` : effect(c, action)}{share(c, total) ? <span className="muted"> · {share(c, total)}</span> : null}</span>
    </div>
  );
}

export function UnitRows({ reports }: { reports: UnitReport[] }) {
  if (!reports.length) return <p className="small muted">Every variable arrived in one unit DEA knows.</p>;
  return (
    <table className="unitrows"><tbody>
      {reports.map((u) => (
        <tr key={u.variable}>
          <td><b>{u.variable}</b></td>
          <td>
            {u.skipped ? <span className="muted">left as is, {Object.keys(u.unconvertible).join(", ")} does not fit {u.label || u.canonical}</span> : <>→ {u.label || u.canonical}</>}
            {Object.entries(u.converted).map(([k, n]) => <span key={k} className="muted"> · {num(n)} rows from {k}</span>)}
            {!u.skipped && Object.entries(u.unconvertible).map(([k, n]) => <span key={k} className="muted"> · {num(n)} rows in {k} split off as “{u.variable} ({k})”</span>)}
          </td>
        </tr>
      ))}
    </tbody></table>
  );
}

export function Findings({ checks, total, selected, onChoose, busy, loadHist, loadSeries, unitReports, laterNote }: {
  checks: Check[]; total: number; selected: Set<string>; onChoose: (id: string, entry: string | null) => void; busy: boolean;
  loadHist?: FixLoader; loadSeries?: SeriesLoader; unitReports?: UnitReport[]; laterNote?: string;
}) {
  const [showAll, setShowAll] = useState(false);
  const fixable = useMemo(() => checks.filter((c) => c.action).sort((a, b) => priority(b, total) - priority(a, total)), [checks, total]);
  const later = useMemo(() => checks.filter((c) => !c.action && !c.id.startsWith("units:")), [checks]);
  const laterGroups = useMemo(() => {
    const m = new Map<string, Check[]>();
    for (const c of later) m.set(family(c.id), [...(m.get(family(c.id)) ?? []), c]);
    const sev = (list: Check[]) => Math.max(...list.map((c) => SEV_W[c.severity]));
    return [...m.entries()].sort((a, b) => sev(b[1]) - sev(a[1]) || b[1].length - a[1].length);
  }, [later]);
  const shown = showAll ? fixable : fixable.slice(0, TOP);
  const hidden = fixable.length - shown.length;
  const hiddenSelected = fixable.slice(TOP).filter((c) => entryFor(selected, c.id)).length;
  return (
    <>
      {fixable.length > 0 && (
        <div className="rise d3">
          <div className="row spread">
            <b>Fix now <span className="muted" style={{ fontWeight: 500 }}>({selected.size} of {fixable.length} ticked, biggest first)</span></b>
            <span className="small muted">Recommended ones are ticked. Everything is recorded in cohort.yaml.</span>
          </div>
          <div className="fixes" style={{ marginTop: 8 }}>
            {shown.map((c) => <FixRow key={c.id} c={c} total={total} entry={entryFor(selected, c.id)} busy={busy} onChoose={(entry) => onChoose(c.id, entry)} loadHist={loadHist} loadSeries={loadSeries} />)}
          </div>
          {hidden > 0 && (
            <button className="ghost sm" style={{ marginTop: 6 }} onClick={() => setShowAll(true)}>
              Show {hidden} smaller one{hidden === 1 ? "" : "s"}{hiddenSelected ? ` (${hiddenSelected} ticked)` : ""} ↓
            </button>
          )}
          {showAll && fixable.length > TOP && <button className="ghost sm" style={{ marginTop: 6 }} onClick={() => setShowAll(false)}>Show fewer ↑</button>}
        </div>
      )}
      <div className="later rise d4">
        {(fixable.length === 0 || later.length > 0) && <div className="small muted" style={{ padding: "4px 0 8px" }}>{fixable.length === 0 ? "Nothing to fix right away. " : ""}{laterNote ?? "For later:"}</div>}
        {laterGroups.map(([g, list]) => (
          <details key={g}>
            <summary>
              <SeverityChip severity={list.some((c) => c.severity === "error") ? "error" : list.some((c) => c.severity === "warning") ? "warning" : "info"} />
              {g}
              <span className="preview muted">{list.slice(0, 3).map((c) => c.variables[0] ?? c.title).join(", ")}{list.length > 3 ? ", …" : ""}</span>
              <span className="count">{list.length}</span>
            </summary>
            {list.slice(0, 8).map((c) => <div key={c.id} className="item"><b>{c.title}</b><div className="small muted">{c.detail}</div></div>)}
            {list.length > 8 && <div className="item small muted">and {list.length - 8} more</div>}
          </details>
        ))}
        {unitReports && (
          <details>
            <summary>Units: what was converted<span className="count">{unitReports.length}</span></summary>
            <div style={{ marginTop: 8 }}><UnitRows reports={unitReports} /></div>
          </details>
        )}
      </div>
    </>
  );
}
