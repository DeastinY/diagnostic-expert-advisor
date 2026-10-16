import { useMemo, useState } from "react";
import type { Check, Hist, UnitReport } from "../api";
import { SeverityChip, Spinner } from "./ui";

/** The findings of a cohort, fixes biggest first, with everything else folded away.
 *  Used by the import wizard (before anything is written) and by the profile page. */

export const num = (n: number) => n.toLocaleString();
const SEV_W = { error: 3, warning: 2, info: 1 } as const;
const TOP = 5;

/** Where a check comes from, by its id prefix: used to group the list. */
export function family(id: string): string {
  if (id.startsWith("units")) return "Units";
  if (id.startsWith("concept")) return "Concept mismatch";
  if (id.startsWith("range")) return "Values out of range";
  if (id.startsWith("cluster")) return "Two value clusters";
  if (id === "duplicates") return "Duplicates";
  if (id.startsWith("time")) return "Timing";
  if (id.startsWith("events-numeric")) return "Numbers stored as text";
  if (id.startsWith("coverage")) return "Rarely recorded variables";
  if (id.startsWith("encounters")) return "Encounters";
  if (id.startsWith("static")) return "Static table";
  if (id.startsWith("site")) return "Differences between sites";
  return "Other";
}

/** One line on what applying a check's fix does. */
export function effect(c: Check): string {
  const a = c.action ?? {};
  if ("dedupe" in a) return `removes ${num(c.n)} rows`;
  if ("drop_time" in a) return `drops ${num(c.n)} rows`;
  if ("parse" in a) return `parses ${num(c.n)} values`;
  if ("drop" in a) return `hides ${num((a.drop as string[]).length)} variables`;
  if ("filter" in a) return "adds a filter";
  if ("unmap" in a) return "forgets the concept";
  if ("fix" in a) return (a.fix as { expr: string | null }).expr ? `converts ${num(c.n)} values` : `drops ${num(c.n)} values`;
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

/** A small histogram with the concept's plausible range shaded. */
export function HistChart({ h }: { h: Hist }) {
  const W = 420, H = 90, pad = 4;
  const max = Math.max(1, ...h.bins.map((b) => b.count));
  const lo = h.bins.length ? h.bins[0].lo : 0, hi = h.bins.length ? h.bins[h.bins.length - 1].hi : 1;
  const x = (v: number) => pad + ((v - lo) / (hi - lo || 1)) * (W - 2 * pad);
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));
  const r = h.range;
  return (
    <div className="hist rise">
      <svg viewBox={`0 0 ${W} ${H + 18}`} width="100%" height={H + 18} role="img" aria-label={`distribution of ${h.label}`}>
        {r && <rect x={x(Math.max(lo, r[0]))} y={0} width={Math.max(0, x(Math.min(hi, r[1])) - x(Math.max(lo, r[0])))} height={H} className="band" />}
        {h.bins.map((b, i) => {
          const inside = !r || (b.lo >= r[0] && b.hi <= r[1]);
          return <rect key={i} x={x(b.lo)} y={H - (b.count / max) * (H - 8)} width={Math.max(1, x(b.hi) - x(b.lo) - 1)} height={(b.count / max) * (H - 8)} className={inside ? "bar" : "bar out"} />;
        })}
        <text x={pad} y={H + 14} className="lbl">{fmt(lo)}</text>
        <text x={W - pad} y={H + 14} className="lbl" textAnchor="end">{fmt(hi)}{h.unit ? ` ${h.unit}` : ""}</text>
        {r && <text x={W / 2} y={H + 14} className="lbl mid" textAnchor="middle">plausible {fmt(r[0])}–{fmt(r[1])}{h.concept ? ` for ${h.concept}` : ""}</text>}
      </svg>
      <div className="small muted">{num(h.n)} observations; red bars lie outside the plausible range.</div>
    </div>
  );
}

function FixRow({ c, total, on, busy, onToggle, loadHist }: { c: Check; total: number; on: boolean; busy: boolean; onToggle: (on: boolean) => void; loadHist?: (variable: string) => Promise<Hist> }) {
  const [hist, setHist] = useState<Hist | null | "loading">(null);
  const variable = c.variables[0];
  const canShow = !!loadHist && !!variable && !c.id.startsWith("coverage") && !c.id.startsWith("events");
  const toggleHist = async () => {
    if (hist) { setHist(null); return; }
    setHist("loading");
    try { setHist(await loadHist!(variable)); } catch { setHist(null); }
  };
  return (
    <div className={`fix${on ? " on" : ""}`}>
      <input type="checkbox" checked={on} disabled={busy} onChange={(e) => onToggle(e.target.checked)} />
      <div>
        <div className="t"><SeverityChip severity={c.severity} /> {c.title}</div>
        <div className="d">{c.detail}</div>
        {canShow && <button className="ghost sm link" onClick={toggleHist}>{hist ? "hide the data" : "show the data"}</button>}
        {hist === "loading" && <Spinner label="drawing…" />}
        {hist && hist !== "loading" && <HistChart h={hist} />}
      </div>
      <span className="eff"><span className="fam">{family(c.id)}</span>{effect(c)}{share(c, total) ? <span className="muted"> · {share(c, total)}</span> : null}</span>
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

export function Findings({ checks, total, selected, onToggle, busy, loadHist, unitReports, laterNote }: {
  checks: Check[]; total: number; selected: Set<string>; onToggle: (id: string, on: boolean) => void; busy: boolean;
  loadHist?: (variable: string) => Promise<Hist>; unitReports?: UnitReport[]; laterNote?: string;
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
  const hiddenSelected = fixable.slice(TOP).filter((c) => selected.has(c.id)).length;
  return (
    <>
      {fixable.length > 0 && (
        <div className="rise d3">
          <div className="row spread">
            <b>Fix now <span className="muted" style={{ fontWeight: 500 }}>({selected.size} of {fixable.length} ticked, biggest first)</span></b>
            <span className="small muted">Recommended ones are ticked. Everything is recorded in cohort.yaml.</span>
          </div>
          <div className="fixes" style={{ marginTop: 8 }}>
            {shown.map((c) => <FixRow key={c.id} c={c} total={total} on={selected.has(c.id)} busy={busy} onToggle={(on) => onToggle(c.id, on)} loadHist={loadHist} />)}
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
