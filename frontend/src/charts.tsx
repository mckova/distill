import { useMemo, useState, type ReactNode } from "react";
import type { Json } from "./api";

const W = 560;
const H = 200;
const PAD = { l: 48, r: 12, t: 12, b: 26 };

const fmt = (v: number) =>
  Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : `${+v.toFixed(2)}`;

function niceTicks(max: number, n = 4): number[] {
  if (max <= 0) return [0];
  const step = Math.pow(10, Math.floor(Math.log10(max / n)));
  const mult = [1, 2, 2.5, 5, 10].find((m) => (max / (m * step)) <= n) ?? 10;
  const s = mult * step;
  return Array.from({ length: Math.ceil(max / s - 1e-9) + 1 }, (_, i) => i * s);
}

function Frame({ children, yMax, tip, label }: { children: ReactNode; yMax: number; tip: ReactNode; label: string }) {
  const ticks = niceTicks(yMax);
  const top = ticks[ticks.length - 1] || 1;
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - v / top);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} className="grid" />
            <text x={PAD.l - 6} y={y(t) + 4} className="axis" textAnchor="end">{fmt(t)}</text>
          </g>
        ))}
        {children}
      </svg>
      {tip}
    </div>
  );
}

function Tip({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  return <div className="tip" style={{ left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%` }}>{children}</div>;
}

export function LineChart({ series, highlight, xLabel = (d: string) => d.slice(5) }:
  { series: { date: string; value: number }[]; highlight?: string; xLabel?: (d: string) => string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...series.map((p) => p.value), 1);
  const top = niceTicks(max).slice(-1)[0] || 1;
  const x = (i: number) => PAD.l + ((W - PAD.l - PAD.r) * i) / Math.max(series.length - 1, 1);
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - v / top);
  const d = series.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const hi = highlight ? series.findIndex((p) => p.date === highlight) : -1;
  const labelEvery = Math.ceil(series.length / 6);
  return (
    <Frame yMax={max} label="Daily values" tip={hover !== null && (
      <Tip x={x(hover)} y={y(series[hover].value)}><b>{fmt(series[hover].value)}</b><span>{series[hover].date}</span></Tip>)}>
      <path d={d} className="line s1" />
      {hi >= 0 && <circle cx={x(hi)} cy={y(series[hi].value)} r={5} className="mark-hi" />}
      {series.map((p, i) => i % labelEvery === 0 && (
        <text key={p.date} x={x(i)} y={H - 6} className="axis" textAnchor="middle">{xLabel(p.date)}</text>))}
      {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b} className="crosshair" />}
      <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} fill="transparent"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = (e.currentTarget as SVGRectElement).getBoundingClientRect();
          const rel = (e.clientX - r.left) / r.width;
          setHover(Math.round(rel * (series.length - 1)));
        }} />
    </Frame>
  );
}

export function GroupedBars({ bars }: { bars: { label: string; recent: number; prior: number }[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...bars.flatMap((b) => [b.recent, b.prior]), 1);
  const top = niceTicks(max).slice(-1)[0] || 1;
  const band = (W - PAD.l - PAD.r) / bars.length;
  const bw = Math.min(26, band / 2 - 4);
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - v / top);
  const bar = (cx: number, v: number, cls: string) => {
    const h = H - PAD.b - y(v);
    const r = Math.min(4, h);
    return <path className={cls} d={`M${cx},${H - PAD.b}v${-(h - r)}q0,${-r} ${r},${-r}h${bw - 2 * r}q${r},0 ${r},${r}v${h - r}z`} />;
  };
  return (
    <>
      <div className="legend"><span className="key s1" />Last 14 days<span className="key s2" />Previous 14 days</div>
      <Frame yMax={max} label="Segment comparison" tip={hover !== null && (
        <Tip x={PAD.l + band * (hover + 0.5)} y={y(Math.max(bars[hover].recent, bars[hover].prior))}>
          <b>{bars[hover].label}</b><span>Last 14d: {fmt(bars[hover].recent)}</span><span>Previous: {fmt(bars[hover].prior)}</span>
        </Tip>)}>
        {bars.map((b, i) => {
          const cx = PAD.l + band * i + band / 2;
          return (
            <g key={b.label} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={PAD.l + band * i} y={PAD.t} width={band} height={H - PAD.t - PAD.b} fill="transparent" />
              {bar(cx - bw - 1, b.prior, "fill s2")}
              {bar(cx + 1, b.recent, "fill s1")}
              <text x={cx} y={H - 6} className="axis" textAnchor="middle">{b.label}</text>
            </g>
          );
        })}
      </Frame>
    </>
  );
}

export function Pareto({ curve, topShare }: { curve: { pct_entities: number; pct_value: number }[]; topShare: number }) {
  const series = curve.map((c) => ({ date: `${c.pct_entities}%`, value: c.pct_value }));
  return (
    <>
      <p className="muted small">Cumulative share of value (y) by share of entities, largest first. Top 10% hold {topShare.toFixed(0)}%.</p>
      <LineChart series={series} xLabel={(d) => d} />
    </>
  );
}

export function Scatter({ points, x, y }: { points: { x: number; y: number }[]; x: string; y: string }) {
  const [hover, setHover] = useState<number | null>(null);
  // clip the domain at the 95th percentile so a few outliers don't squash the cloud
  const q95 = (vs: number[]) => [...vs].sort((a, b) => a - b)[Math.floor(vs.length * 0.95)] ?? 1;
  const xMax = Math.max(q95(points.map((p) => p.x)), 1e-9);
  const yMax = Math.max(q95(points.map((p) => p.y)), 1e-9);
  points = points.filter((p) => p.x <= xMax && p.y <= yMax);
  const top = niceTicks(yMax).slice(-1)[0] || 1;
  const sx = (v: number) => PAD.l + ((W - PAD.l - PAD.r) * v) / xMax;
  const sy = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - v / top);
  return (
    <Frame yMax={yMax} label={`${y} by ${x}`} tip={hover !== null && (
      <Tip x={sx(points[hover].x)} y={sy(points[hover].y)}><span>{x}: {fmt(points[hover].x)}</span><span>{y}: {fmt(points[hover].y)}</span></Tip>)}>
      {points.map((p, i) => (
        <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={hover === i ? 6 : 4} className="dot s1"
          onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
      ))}
      <text x={W - PAD.r} y={H - 6} className="axis" textAnchor="end">{x} → (outliers above p95 hidden)</text>
      <text x={PAD.l + 8} y={PAD.t - 2} className="axis">↑ {y}</text>
    </Frame>
  );
}

export function HBar({ rows, max = 1, format = (v: number) => `${Math.round(v * 100)}%` }:
  { rows: { label: string; value: number; note?: string }[]; max?: number; format?: (v: number) => string }) {
  return (
    <div className="hbars">
      {rows.map((r) => (
        <div key={r.label} className="hbar" title={r.note}>
          <span className="hbar-label">{r.label}</span>
          <span className="hbar-track"><span className="hbar-fill" style={{ width: `${(r.value / max) * 100}%` }} /></span>
          <span className="hbar-value">{format(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

export function EvidenceChart({ evidence }: { evidence: Json }) {
  return useMemo(() => {
    switch (evidence.chart) {
      case "line": return <LineChart series={evidence.series} highlight={evidence.highlight} />;
      case "bars": return <GroupedBars bars={evidence.bars} />;
      case "pareto": return <Pareto curve={evidence.curve} topShare={evidence.top_share} />;
      case "scatter": return <Scatter points={evidence.points} x={evidence.x} y={evidence.y} />;
      case "variants":
        return <HBar rows={evidence.variants.map((v: Json) => ({ label: v.path.join(" → "), value: v.share }))} max={evidence.variants[0]?.share || 1} />;
      default: return null;
    }
  }, [evidence]);
}
