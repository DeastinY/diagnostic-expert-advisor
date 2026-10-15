import { useEffect, useRef } from "react";
import embed, { type Result } from "vega-embed";

/** Renders a Vega-Lite spec produced by the backend; fills its container's width. */
export function VegaChart({ spec, height }: { spec: object | null | undefined; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let view: Result | undefined;
    if (ref.current && spec) {
      const s = { ...(spec as Record<string, unknown>), width: "container" } as object;
      embed(ref.current, s as never, { actions: false, renderer: "svg", config: { background: "transparent" } })
        .then((r) => { view = r; })
        .catch((e) => console.error(e));
    }
    return () => { view?.finalize(); };
  }, [spec]);
  if (!spec) return <div className="empty small">No data</div>;
  return <div ref={ref} className="vega" style={height ? { minHeight: height } : undefined} />;
}
