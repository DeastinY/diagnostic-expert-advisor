import { useEffect, useState, type ReactNode } from "react";

export function Kpi({ label, value, delta }: { label: string; value: ReactNode; delta?: ReactNode }) {
  return (
    <div className="kpi">
      <div className="v">{value}</div>
      <div className="l">{label}</div>
      {delta && <div className="d">{delta}</div>}
    </div>
  );
}

export function Card({ title, children, actions, flat }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; flat?: boolean }) {
  return (
    <section className={`card${flat ? " flat" : ""}`}>
      {(title || actions) && (
        <div className="row spread" style={{ marginBottom: 10 }}>
          {title && <h2 style={{ margin: 0 }}>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Spinner({ label }: { label?: string }) {
  return <span className="muted small"><span className="spinner" /> {label ?? "Loading…"}</span>;
}

export function Alert({ children, kind }: { children: ReactNode; kind?: "danger" }) {
  return <div className={`alert${kind ? ` ${kind}` : ""}`}>{children}</div>;
}

export function SeverityChip({ severity }: { severity: "info" | "warning" | "error" | null | undefined }) {
  if (!severity) return null;
  const cls = severity === "error" ? "danger" : severity === "warning" ? "warn" : "info";
  return <span className={`chip ${cls}`}>{severity}</span>;
}

export function ChecksList({ checks, empty = "No findings." }: { checks: { id: string; severity: "info" | "warning" | "error"; title: string; detail: string; action?: unknown }[]; empty?: string }) {
  if (checks.length === 0) return <p className="muted small">{empty}</p>;
  return (
    <div className="checks">
      {checks.map((c) => (
        <div key={c.id} className="check">
          <SeverityChip severity={c.severity} />
          <div>
            <div><b>{c.title}</b>{c.action ? <span className="muted small"> · fix available after import</span> : null}</div>
            <div className="small muted">{c.detail}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: string[]; value: string; onChange: (t: string) => void }) {
  return (
    <div className="tabs">
      {tabs.map((t) => <button key={t} className={t === value ? "active" : ""} onClick={() => onChange(t)}>{t}</button>)}
    </div>
  );
}

let toastFn: ((m: string) => void) | null = null;
export function toast(message: string) { toastFn?.(message); }
export function ToastHost() {
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => {
    toastFn = (m) => { setMsg(m); setTimeout(() => setMsg(null), 3200); };
    return () => { toastFn = null; };
  }, []);
  return msg ? <div className="toast">{msg}</div> : null;
}

/** Fetch helper: re-runs when `deps` change, exposes data/error/loading. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn().then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e: Error) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
