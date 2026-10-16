import { useState } from "react";
import { api } from "../api";
import { Spinner, toast } from "./ui";

export function DemoForm({ onDone }: { onDone: (name: string) => void }) {
  const [name, setName] = useState("demo");
  const [n, setN] = useState(60);
  const [mimic, setMimic] = useState(false);
  const [sites, setSites] = useState(1);
  const [busy, setBusy] = useState(false);
  return (
    <div className="stack">
      <p className="muted small">A synthetic ICU cohort with realistic sampling rates, events and notes. No real patients.</p>
      <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="field">Encounters<input type="number" min={5} max={5000} value={n} onChange={(e) => setN(Number(e.target.value))} /></label>
      <label className="row"><input type="checkbox" checked={mimic} onChange={(e) => setMimic(e.target.checked)} /> MIMIC-IV table layout (exercise the MIMIC importer)</label>
      {!mimic && <label className="field">Hospitals<select value={sites} onChange={(e) => setSites(Number(e.target.value))}>
        <option value={1}>one site</option><option value={2}>two sites (B charts °F, reads MAP high)</option><option value={3}>three sites (C exports 5-minute vitals, no lactate/CRP)</option>
      </select></label>}
      <button className="primary" disabled={busy || !name} onClick={async () => {
        setBusy(true);
        try { await api.demo({ name, n, mimic, sites: mimic ? 1 : sites }); toast("Demo cohort created"); onDone(name); }
        catch (e) { toast(String((e as Error).message)); } finally { setBusy(false); }
      }}>{busy ? <Spinner label="Generating…" /> : "Generate"}</button>
    </div>
  );
}
