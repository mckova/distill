import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronRight, X } from "lucide-react";
import type { AgentStep } from "./api";

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    fn().then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e.message ?? e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, reload, setData };
}

export function Header({ title, sub, children }: { title: ReactNode; sub?: ReactNode; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="lede">{sub}</p>}
      </div>
      {children && <div className="actions">{children}</div>}
    </header>
  );
}

/** A row of key figures separated by hairlines: read left to right like a ledger, not a wall of tiles. */
export function Ledger({ items }: { items: { label: string; value: ReactNode; note?: ReactNode; tone?: "good" | "warn" }[] }) {
  return (
    <dl className="ledger">
      {items.map((it) => (
        <div key={it.label} className="ledger-item">
          <dt>{it.label}</dt>
          <dd className={`num ${it.tone ?? ""}`}>{it.value ?? <Skeleton w={72} h={26} />}</dd>
          {it.note && <dd className="ledger-note">{it.note}</dd>}
        </div>
      ))}
    </dl>
  );
}

export function Skeleton({ w, h = 14, lines }: { w?: number | string; h?: number; lines?: number }) {
  if (lines) {
    return <div className="sk-stack" aria-hidden>{Array.from({ length: lines }, (_, i) => <span key={i} className="sk" style={{ height: h, width: i === lines - 1 ? "62%" : "100%" }} />)}</div>;
  }
  return <span className="sk" aria-hidden style={{ width: w ?? "100%", height: h }} />;
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Dialog({ open, onClose, title, children, wide, footer }:
  { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; wide?: boolean; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className={wide ? "dialog wide" : "dialog"} onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) onClose(); }}>
      {open && <>
        <div className="dialog-head">
          <h3>{title}</h3>
          <button className="icon-btn" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </>}
    </dialog>
  );
}

/** Destructive actions always go through an explicit confirmation. */
export function ConfirmButton({ label, title, body, onConfirm, className = "ghost danger", confirmLabel }:
  { label: ReactNode; title: string; body: ReactNode; onConfirm: () => Promise<unknown> | void; className?: string; confirmLabel?: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <>
      <button className={className} onClick={() => setOpen(true)}>{label}</button>
      <Dialog open={open} onClose={() => setOpen(false)} title={title}
        footer={<>
          <button className="ghost" onClick={() => setOpen(false)}>Cancel</button>
          <button className="destructive" disabled={busy} onClick={async () => {
            setBusy(true);
            try { await onConfirm(); setOpen(false); } finally { setBusy(false); }
          }}>{busy ? "Working…" : confirmLabel ?? "Delete"}</button>
        </>}>
        <p className="pretty">{body}</p>
      </Dialog>
    </>
  );
}

export function Md({ children }: { children: string }) {
  return <div className="md"><Markdown remarkPlugins={[remarkGfm]}>{children}</Markdown></div>;
}

export function Steps({ steps, open }: { steps?: AgentStep[]; open?: boolean }) {
  if (!steps?.length) return null;
  return (
    <details className="trace" open={open}>
      <summary><ChevronRight size={13} className="chev" aria-hidden />{steps.length} {steps.length === 1 ? "query" : "queries"} behind this answer</summary>
      <ol>
        {steps.map((st, i) => (
          <li key={i} className={st.ok ? "" : "bad"}>
            <code>{st.tool}</code>
            <span className="args">{Object.entries(st.input).filter(([, v]) => v !== null && !(Array.isArray(v) && !v.length)).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join("  ")}</span>
            <span className="result">{st.summary}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export function Tag({ children, tone }: { children: ReactNode; tone?: "good" | "warn" }) {
  return <span className={`tag ${tone ?? ""}`}>{children}</span>;
}

export const fmtHours = (h: number) => (h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(1)} h`);
export const NA = <span className="na">n/a</span>;
export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
export const fmtMoney = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 0 });

export function timeAgo(iso: string) {
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} d ago`;
  return d.toLocaleDateString();
}
