import { useState } from "react";
import { useCohort } from "../App";
import { api, type Recommendation } from "../api";
import { Alert, Kpi, Spinner, toast, useAsync } from "./ui";

export function AdvisorPanel() {
  const { name, where, reload } = useCohort();
  const status = useAsync(() => api.adviseStatus(name, where), [name, where.join("|")]);
  const [model, setModel] = useState(""); const [baseUrl, setBaseUrl] = useState(""); const [key, setKey] = useState("");
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [rename, setRename] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const st = status.data;
  const renames = rec?.variables.filter((v) => v.canonical_name && v.canonical_name !== v.variable) ?? [];
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="muted small">Optional. Any OpenAI-compatible endpoint (OpenAI, Azure, vLLM, Ollama, LiteLLM…). The model only ever receives the profile below: aggregate statistics, no encounter ids, timestamps or values.</p>
      <div className="grid c3">
        <label className="field">Model<input value={model} placeholder={st?.model ?? "model"} onChange={(e) => setModel(e.target.value)} /></label>
        <label className="field">Base URL<input value={baseUrl} placeholder={st?.base_url ?? "https://api.openai.com/v1"} onChange={(e) => setBaseUrl(e.target.value)} /></label>
        <label className="field">API key<input type="password" value={key} placeholder={st?.configured ? "from environment" : ""} onChange={(e) => setKey(e.target.value)} /></label>
      </div>
      <details><summary>What will be sent ({st ? `${JSON.stringify(st.payload).length.toLocaleString()} bytes` : "…"})</summary>
        <pre className="mono" style={{ maxHeight: 280, overflow: "auto", background: "var(--surface-2)", padding: 10, borderRadius: 8 }}>{st ? JSON.stringify(st.payload, null, 1) : ""}</pre></details>
      {st && !st.installed && <Alert>Install <code>dea[ai]</code> to enable the advisor.</Alert>}
      <div className="row">
        <button className="primary" disabled={busy || !st?.installed || (!st?.configured && !key && !baseUrl)} onClick={async () => {
          setBusy(true); setErr(null);
          try { setRec(await api.advise(name, where, { model: model || undefined, base_url: baseUrl || undefined, api_key: key || undefined })); }
          catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
        }}>{busy ? <Spinner label="Asking…" /> : "Get recommendations"}</button>
        {!st?.configured && !key && !baseUrl && st?.installed && <span className="small muted">set an API key or base URL</span>}
      </div>
      {err && <Alert kind="danger">{err}</Alert>}
      <MappingProposals name={name} where={where} creds={{ model: model || undefined, base_url: baseUrl || undefined, api_key: key || undefined }} enabled={!!st?.installed && (!!st?.configured || !!key || !!baseUrl)} onApplied={reload} />
      {rec && (
        <div className="stack" style={{ gap: 10 }}>
          <p>{rec.summary}</p>
          <div className="kpis"><Kpi label="recommended grid" value={rec.resample_every} /><Kpi label="aggregate" value={rec.aggregate} /><Kpi label="default fill" value={rec.default_fill} /></div>
          <div className="table-wrap" style={{ maxHeight: 300 }}><table className="table"><thead><tr><th>Variable</th><th>Label</th><th>Canonical</th><th>Role</th><th>Unit</th><th>Fill</th><th>Why</th></tr></thead>
            <tbody>{rec.variables.map((v) => <tr key={v.variable}><td>{v.variable}</td><td>{v.label}</td><td><code>{v.canonical_name}</code></td><td>{v.role}</td><td>{v.unit_guess}</td><td>{v.imputation}</td><td style={{ whiteSpace: "normal" }}>{v.rationale}</td></tr>)}</tbody></table></div>
          {rec.suggested_filters.map((f) => <div key={f.label} className="small"><b>{f.label}</b> <code>{f.where}</code> {f.description}</div>)}
          {rec.warnings.map((w) => <Alert key={w}>{w}</Alert>)}
          <div className="row">
            {renames.length > 0 && <label className="row small"><input type="checkbox" checked={rename} onChange={(e) => setRename(e.target.checked)} /> also rename {renames.length} variable(s) to canonical names (rewrites the cohort)</label>}
            <button className="primary" disabled={busy} onClick={async () => {
              setBusy(true);
              try { await api.adviseApply(name, rec, rename); toast("Recommendation applied"); setRec(null); reload(); }
              catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
            }}>Apply to cohort</button>
          </div>
        </div>
      )}
    </div>
  );
}

function MappingProposals({ name, where, creds, enabled, onApplied }: { name: string; where: string[]; creds: { model?: string; base_url?: string; api_key?: string }; enabled: boolean; onApplied: () => void }) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ proposals: { variable: string; concept: string; confidence: number; rationale: string }[]; mapping: Record<string, string>; n_unmatched: number } | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const selected = Object.entries(chosen).filter(([, on]) => on).map(([v]) => v);
  return (
    <div className="stack" style={{ gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      <div className="row spread">
        <div><b className="small">Bulk mapping</b><div className="small muted">Ask the model for a dictionary concept for every variable the offline matcher could not place. It sees names, units and ranges only.</div></div>
        <button className="sm" disabled={busy || !enabled} onClick={async () => {
          setBusy(true); setErr(null);
          try { const r = await api.adviseMap(name, where, creds); setRes(r); setChosen(Object.fromEntries(Object.keys(r.mapping).map((v) => [v, true]))); }
          catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
        }}>{busy ? <Spinner label="Mapping…" /> : "Propose mappings"}</button>
      </div>
      {err && <Alert kind="danger">{err}</Alert>}
      {res && res.n_unmatched === 0 && <p className="muted small">Every variable already matches the dictionary.</p>}
      {res && res.proposals.length > 0 && (
        <>
          <div className="table-wrap" style={{ maxHeight: 260 }}>
            <table className="table"><thead><tr><th></th><th>Variable</th><th>Concept</th><th>Confidence</th><th>Why</th></tr></thead>
              <tbody>{res.proposals.map((p) => (
                <tr key={p.variable}>
                  <td>{p.concept && <input type="checkbox" checked={!!chosen[p.variable]} onChange={(e) => setChosen({ ...chosen, [p.variable]: e.target.checked })} />}</td>
                  <td>{p.variable}</td><td>{p.concept ? <code>{p.concept}</code> : <span className="muted">no match</span>}</td>
                  <td className="num">{Math.round(p.confidence * 100)}%</td><td style={{ whiteSpace: "normal" }}>{p.rationale}</td>
                </tr>))}</tbody></table>
          </div>
          <div className="row">
            <button className="primary sm" disabled={busy || selected.length === 0} onClick={async () => {
              setBusy(true);
              try {
                const mapping = Object.fromEntries(res.proposals.filter((p) => chosen[p.variable] && p.concept).map((p) => [p.variable, p.concept]));
                const r = await api.applyMapping(name, { mapping }); toast(`Mapped ${r.applied.length} variable(s)`); setRes(null); onApplied();
              } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
            }}>Apply {selected.length} mapping(s)</button>
            <span className="small muted">rewrites the cohort; labels, units and LOINC codes follow the concept</span>
          </div>
        </>
      )}
    </div>
  );
}
