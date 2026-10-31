import { useMemo, useState } from "react";
import type { Json } from "./api";

interface Edge { source: string; target: string; frequency: number; probability: number; mean_hours: number }

const NODE_W = 150;
const NODE_H = 38;
const ROW = 86;

/** Layered layout of a directly-follows graph: BFS depth from the start activities, back-edges drawn as side arcs. */
export function ProcessMap({ model, minProbability = 0.04 }: { model: Json; minProbability?: number }) {
  const [hover, setHover] = useState<Edge | null>(null);
  const layout = useMemo(() => {
    const edges: Edge[] = model.edges.filter((e: Edge) => e.probability >= minProbability);
    const depth = new Map<string, number>();
    const queue = Object.keys(model.start_activities);
    queue.forEach((a) => depth.set(a, 0));
    while (queue.length) {
      const a = queue.shift()!;
      edges.filter((e) => e.source === a).sort((x, y) => y.frequency - x.frequency).forEach((e) => {
        if (!depth.has(e.target)) { depth.set(e.target, depth.get(a)! + 1); queue.push(e.target); }
      });
    }
    const rows = new Map<number, string[]>();
    for (const [a, d] of depth) rows.set(d, [...(rows.get(d) ?? []), a]);
    const width = Math.max(...[...rows.values()].map((r) => r.length)) * (NODE_W + 40) + 160;
    const pos = new Map<string, { x: number; y: number }>();
    for (const [d, names] of rows) {
      names.forEach((n, i) => pos.set(n, { x: width / 2 + (i - (names.length - 1) / 2) * (NODE_W + 40), y: 30 + d * ROW }));
    }
    const freq = new Map<string, number>(model.activities.map((a: Json) => [a.name, a.frequency]));
    const bottleneck = new Set<string>((model.bottlenecks ?? []).slice(0, 2).map((b: Json) => `${b.source}→${b.target}`));
    const auto = new Map<string, number>(model.activities.map((a: Json) => [a.name, a.automation_potential]));
    return { edges, pos, width, height: rows.size * ROW + 30, freq, bottleneck, auto, maxF: Math.max(...edges.map((e) => e.frequency)) };
  }, [model, minProbability]);

  const { edges, pos, width, height, freq, bottleneck, auto, maxF } = layout;

  return (
    <div className="process-wrap">
      <div className="legend">
        <span className="key s1" />Hand-off (width = cases)
        <span className="key warn" />Bottleneck
        <span className="key auto" />Automation candidate
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="process" role="img" aria-label="Discovered process map">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="9" markerHeight="9" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M0,0L10,5L0,10z" className="arrowhead" />
          </marker>
        </defs>
        {edges.map((e) => {
          const a = pos.get(e.source), b = pos.get(e.target);
          if (!a || !b) return null;
          const back = b.y <= a.y;
          const sw = 1.5 + 6 * (e.frequency / maxF);
          const cls = bottleneck.has(`${e.source}→${e.target}`) ? "edge warn" : "edge";
          const d = back
            ? `M${a.x + NODE_W / 2},${a.y}C${a.x + NODE_W / 2 + 70},${a.y} ${b.x + NODE_W / 2 + 70},${b.y} ${b.x + NODE_W / 2},${b.y}`
            : `M${a.x},${a.y + NODE_H / 2}C${a.x},${(a.y + b.y) / 2 + NODE_H / 2} ${b.x},${(a.y + b.y) / 2} ${b.x},${b.y - NODE_H / 2 - 2}`;
          return (
            <g key={`${e.source}-${e.target}`} onMouseEnter={() => setHover(e)} onMouseLeave={() => setHover(null)}>
              <path d={d} className="edge-hit" />
              <path d={d} className={cls} strokeWidth={sw} markerEnd="url(#arrow)" />
            </g>
          );
        })}
        {[...pos].map(([name, p]) => (
          <g key={name} transform={`translate(${p.x - NODE_W / 2},${p.y - NODE_H / 2})`}>
            <rect width={NODE_W} height={NODE_H} rx={8} className={(auto.get(name) ?? 0) >= 0.6 ? "node auto" : "node"} />
            <text x={NODE_W / 2} y={16} textAnchor="middle" className="node-label">{name}</text>
            <text x={NODE_W / 2} y={30} textAnchor="middle" className="node-sub">{freq.get(name)?.toLocaleString()} events</text>
          </g>
        ))}
      </svg>
      <div className="edge-info">
        {hover
          ? <><b>{hover.source} → {hover.target}</b> · {hover.frequency} cases · {(hover.probability * 100).toFixed(0)}% of exits · mean wait {hover.mean_hours.toFixed(1)}h</>
          : <span className="muted">Hover a hand-off to see its volume and waiting time.</span>}
      </div>
    </div>
  );
}
