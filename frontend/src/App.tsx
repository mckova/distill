import { useEffect, useState, type ReactNode } from "react";
import { api, type Automation, type Dataset, type Insight, type Json, type Source } from "./api";
import { AgentBriefing, Ask, type AiInfo } from "./ai";
import { EvidenceChart, HBar } from "./charts";
import { ProcessMap } from "./ProcessMap";
import { fmtHoursWk, KIND_LABEL, LearningPage, Optimize, Skills } from "./studio";
import { ConfirmButton, Dialog, Empty, fmtMoney, Header, Ledger, Md, NA, plural, Skeleton, Steps, Tag, timeAgo, useAsync } from "./ui";

type Page = "brief" | "ask" | "findings" | "process" | "optimize" | "automations" | "skills" | "sources" | "learning";

const NAV: { group: string; items: { id: Page; label: string }[] }[] = [
  { group: "", items: [{ id: "brief", label: "Brief" }, { id: "ask", label: "Ask" }] },
  { group: "Understand", items: [{ id: "findings", label: "Findings" }, { id: "process", label: "Process" }, { id: "optimize", label: "Optimize" }] },
  { group: "Act", items: [{ id: "automations", label: "Automations" }, { id: "skills", label: "Skills" }] },
  { group: "Setup", items: [{ id: "sources", label: "Sources" }, { id: "learning", label: "Learning" }] },
];
const PAGES = NAV.flatMap((g) => g.items.map((i) => i.id));

const ACTIONABLE = new Set(["segment_driver", "trend", "anomaly", "concentration", "bottleneck", "automation_candidate", "rework", "data_quality"]);

type Ctx = { notify: (m: string) => void; go: (p: Page, prompt?: string) => void; bump: () => void; ai: AiInfo | null; counts: Record<string, number> };

export default function App() {
  const initial = location.hash.slice(1) as Page;
  const [page, setPage] = useState<Page>(PAGES.includes(initial) ? initial : "brief");
  const [prompt, setPrompt] = useState<string | undefined>();
  const [toast, setToast] = useState<{ id: number; text: string } | null>(null);
  const [version, setVersion] = useState(0);
  const ai = useAsync(api.ai).data;
  const insights = useAsync(() => api.insights(), [version, page]);
  const autos = useAsync(api.automations, [version, page]);
  const skills = useAsync(api.skills, [version, page]);
  const counts = {
    findings: insights.data?.filter((i) => i.status === "new").length ?? 0,
    automations: autos.data?.length ?? 0,
    skills: skills.data?.length ?? 0,
  };
  const ctx: Ctx = {
    ai, counts,
    bump: () => setVersion((v) => v + 1),
    notify: (text) => { const id = Date.now(); setToast({ id, text }); setTimeout(() => setToast((t) => (t?.id === id ? null : t)), 3600); },
    go: (p, q) => { setPrompt(q); location.hash = p; setPage(p); },
  };
  useEffect(() => { document.title = `${NAV.flatMap((g) => g.items).find((i) => i.id === page)?.label} · Distill`; }, [page]);

  return (
    <div className="shell">
      <nav className="side" aria-label="Main">
        <div className="brand">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden className="mark"><path d="M2 2h14L10.5 9.5V15l-3 1.5V9.5z" /></svg>
          Distill
        </div>
        {NAV.map((g) => (
          <div key={g.group || "top"} className="nav-group">
            {g.group && <span className="nav-label">{g.group}</span>}
            {g.items.map(({ id, label }) => (
              <button key={id} className="nav" aria-current={page === id ? "page" : undefined} onClick={() => ctx.go(id)}>
                <span>{label}</span>
                {counts[id as keyof typeof counts] ? <span className="nav-count num">{counts[id as keyof typeof counts]}</span> : null}
              </button>
            ))}
          </div>
        ))}
        <div className="side-foot">
          <span className={ai?.enabled ? "status on" : "status"} title={ai?.capabilities.join(", ")}>
            <span className="status-dot" aria-hidden />
            {ai ? (ai.enabled ? `Claude connected` : "Claude not connected") : "Checking…"}
          </span>
          {ai?.enabled && <span className="status-model">{ai.model}</span>}
        </div>
      </nav>
      <main key={version} className={page === "ask" ? "main bleed" : "main"}>
        {page === "brief" && <Brief {...ctx} />}
        {page === "ask" && <Ask ai={ai} notify={ctx.notify} initial={prompt} />}
        {page === "findings" && <Findings {...ctx} />}
        {page === "process" && <Process />}
        {page === "optimize" && <Optimize notify={ctx.notify} />}
        {page === "automations" && <Automations {...ctx} />}
        {page === "skills" && <Skills notify={ctx.notify} />}
        {page === "sources" && <Sources {...ctx} />}
        {page === "learning" && <LearningPage />}
      </main>
      <div className="toast-region" role="status" aria-live="polite">
        {toast && <div key={toast.id} className="toast">{toast.text}</div>}
      </div>
    </div>
  );
}

function NoData({ go }: { go: Ctx["go"] }) {
  return <Empty title="Nothing connected yet" action={<button className="primary" onClick={() => go("sources")}>Connect a source</button>}>
    Load the demo company or connect a CSV, database, REST API or webhook.
  </Empty>;
}

const today = () => new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });

// --------------------------------------------------------------------------- brief

function Brief({ go, notify, bump, ai }: Ctx) {
  const datasets = useAsync(api.datasets);
  const insights = useAsync(() => api.insights());
  const learning = useAsync(api.learning);
  const opt = useAsync(api.optimization);
  const [busy, setBusy] = useState(false);

  if (datasets.data && datasets.data.length === 0) {
    return (
      <div className="welcome">
        <h1>Point Distill at your company’s data.</h1>
        <p className="lede">It profiles every table, finds what changed and what is slow, writes the playbooks your agents need, and turns each finding into an automation you can switch on.</p>
        <div className="row">
          <button className="primary" disabled={busy} onClick={async () => {
            setBusy(true);
            try { const r = await api.demo(); notify(`Demo company loaded with ${r.insights} findings`); bump(); }
            catch (e) { notify(String(e)); } finally { setBusy(false); }
          }}>{busy ? "Loading…" : "Load the demo company"}</button>
          <button className="ghost" onClick={() => go("sources")}>Connect my own data</button>
        </div>
        <p className="fine">The demo is 120 days of orders and support tickets from a small online retailer, with a regional slump, a slow hand-off and a manual copy-paste step hidden in it.</p>
      </div>
    );
  }

  const rows = (datasets.data ?? []).reduce((s, d) => s + d.rows, 0);
  const open = (insights.data ?? []).filter((i) => i.status === "new");
  const o = opt.data;
  return (
    <>
      <Header sub={`${today()}. Read from ${plural(rows, "record")} in ${plural(datasets.data?.length ?? 0, "dataset")}.`}
        title={insights.data
          ? (open[0] ? <>{plural(open.length, "finding")} to review. <span className="ink-2">Start here: {open[0].title}.</span></> : "Nothing needs attention right now.")
          : <Skeleton w="70%" h={30} />} />
      <div className="split">
        <section aria-labelledby="h-changed">
          <div className="section-bar"><h2 id="h-changed">What needs attention</h2><button className="link" onClick={() => go("findings")}>All findings</button></div>
          {!insights.data && <Skeleton lines={6} />}
          <ol className="findings-list">
            {open.slice(0, 6).map((i, n) => (
              <li key={i.id}>
                <span className="idx num" aria-label={`Rank ${n + 1}`}>{n + 1}</span>
                <div className="f-body">
                  <span className={`kind k-${i.kind}`}>{KIND_LABEL[i.kind] ?? i.kind}</span>
                  <button className="f-title" onClick={() => go("findings")}>{i.title}</button>
                  <p className="f-text">{i.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>
        <aside className="rail" aria-label="Agent">
          <AgentBriefing ai={ai} notify={notify} onDone={() => { insights.reload(); learning.reload(); }} />
          <form className="quick-ask" onSubmit={(e) => {
            e.preventDefault();
            const q = new FormData(e.currentTarget).get("q") as string;
            if (q.trim()) go("ask", q.trim());
          }}>
            <label htmlFor="quick-q">Ask a question about this data</label>
            <div className="row tight">
              <input id="quick-q" name="q" placeholder="Why did North drop in September?" />
              <button className="primary">Ask</button>
            </div>
          </form>
        </aside>
      </div>
      <section aria-labelledby="h-opps">
        <div className="section-bar"><h2 id="h-opps">Where the time and money go</h2><button className="link" onClick={() => go("optimize")}>Full breakdown</button></div>
        <Ledger items={[
        { label: "Staff time automatable", value: o ? `${o.staff_hours_per_week} h/wk` : null, tone: "good" },
        { label: "Case waiting removable", value: o ? fmtHoursWk(o.wait_hours_per_week) : null },
        { label: "Revenue at risk", value: o ? `${fmtMoney(o.total_revenue_at_risk_per_week)}/wk` : null, tone: "warn" },
      ]} />
        {!o && <Skeleton lines={4} />}
        {o && (
          <table className="table">
            <thead><tr><th>Opportunity</th><th className="r">Per week</th></tr></thead>
            <tbody>
              {o.opportunities.slice(0, 5).map((x) => (
                <tr key={`${x.kind}${x.insight_id}`}>
                  <td><span className="cell-title">{x.title}</span><span className="cell-sub">{x.detail}</span></td>
                  <td className="r num nowrap">{x.revenue_at_risk_per_week ? `${fmtMoney(x.revenue_at_risk_per_week)} at risk` : `${fmtHoursWk(x.hours_per_week)} ${x.unit === "staff" ? "staff" : "waiting"}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

// --------------------------------------------------------------------------- findings (master-detail)

function FindingDetail({ insight, notify, onChange, go }:
  { insight: Insight; notify: (m: string) => void; onChange: () => void; go: Ctx["go"] }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");
  const act = async (key: string, fn: () => Promise<unknown>, msg: string) => {
    setBusy(key);
    try { await fn(); notify(msg); onChange(); return true; } catch (e) { notify(String((e as Error).message)); return false; } finally { setBusy(null); }
  };
  const inv = insight.evidence.ai_investigation;
  return (
    <article className="detail" aria-live="polite">
      <div className="detail-meta">
        <span className={`kind k-${insight.kind}`}>{KIND_LABEL[insight.kind] ?? insight.kind}</span>
        <span className="muted small num" title="Rank = statistical strength × learned usefulness of this type">rank {insight.score.toFixed(2)}</span>
        {insight.status !== "new" && <Tag tone={insight.status === "useful" ? "good" : undefined}>{insight.status === "useful" ? "Marked useful" : "Dismissed"}</Tag>}
      </div>
      <h2 className="detail-title">{insight.title}</h2>
      <p className="detail-body">{insight.body}</p>
      <div className="figure">
        {["line", "bars", "pareto", "scatter", "variants"].includes(insight.evidence.chart)
          ? <EvidenceChart evidence={insight.evidence} />
          : <EvidenceFacts evidence={insight.evidence} />}
      </div>
      {inv && (
        <section className="investigation">
          <h3>Root cause, investigated by {inv.engine}</h3>
          <Md>{inv.text}</Md>
          <Steps steps={inv.steps} />
        </section>
      )}
      <div className="actions-bar">
        {ACTIONABLE.has(insight.kind) && (
          <button className="primary" disabled={!!busy} onClick={async () => { if (await act("auto", () => api.automate(insight.id), "Automation drafted. Review it, then switch it on.")) go("automations"); }}>
            Turn into automation
          </button>
        )}
        <button className="ghost" disabled={!!busy} onClick={() => act("inv", () => api.investigate(insight.id), "Investigation added")}>
          {busy === "inv" ? "Investigating…" : inv ? "Investigate again" : "Investigate the cause"}</button>
        {insight.status === "new" && <span className="push">
          <button className="ghost" disabled={!!busy} onClick={() => act("up", () => api.feedback(insight.id, true), "Noted. Findings like this will rank higher.")}>Useful</button>
          <button className="ghost" disabled={!!busy} onClick={() => setDismissing(true)}>Not useful</button>
        </span>}
      </div>
      <Dialog open={dismissing} onClose={() => setDismissing(false)} title="Why isn’t this useful?"
        footer={<>
          <button className="ghost" onClick={() => setDismissing(false)}>Cancel</button>
          <button className="primary" disabled={!!busy} onClick={async () => {
            if (await act("down", () => api.feedback(insight.id, false, reason), reason ? "Dismissed. The reason is now in company memory." : "Dismissed. Ranking will adapt.")) setDismissing(false);
          }}>Dismiss finding</button>
        </>}>
        <p className="muted">Optional. A reason is saved to company memory, so the agent and future analyses take it into account.</p>
        <label htmlFor={`why-${insight.id}`} className="field-label">Reason</label>
        <input id={`why-${insight.id}`} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="That spike was the planned September promotion" />
      </Dialog>
    </article>
  );
}

const HIDDEN = new Set(["chart", "ai_investigation", "check", "series", "bars", "points", "curve", "variants", "highlight"]);
const humanize = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/** For findings without a natural chart, the raw evidence the detector used, as a readable ledger. */
function EvidenceFacts({ evidence }: { evidence: Json }) {
  const rows = Object.entries(evidence).filter(([k, v]) => !HIDDEN.has(k) && (typeof v === "number" || typeof v === "string"));
  if (!rows.length) return <p className="fine">No additional evidence recorded.</p>;
  return (
    <dl className="facts">
      {rows.slice(0, 8).map(([k, v]) => (
        <div key={k}><dt>{humanize(k)}</dt><dd className="num">{typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString() : v.toFixed(2)) : v}</dd></div>
      ))}
    </dl>
  );
}

function Findings({ notify, go }: Ctx) {
  const datasets = useAsync(api.datasets);
  const [showDismissed, setShowDismissed] = useState(false);
  const insights = useAsync(() => api.insights(showDismissed), [showDismissed]);
  const [kind, setKind] = useState<string>("all");
  const [sel, setSel] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  if (datasets.data?.length === 0) return <NoData go={go} />;
  const all = insights.data ?? [];
  const list = all.filter((i) => kind === "all" || i.kind === kind);
  const kinds = [...new Set(all.map((i) => i.kind))];
  const current = list.find((i) => i.id === sel) ?? list[0];
  const dsName = (id: number) => datasets.data?.find((d) => d.id === id)?.stream ?? "";
  return (
    <>
      <Header title="Findings" sub="Ranked by statistical strength and by what you have told Distill matters here.">
        <label className="check"><input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} /> Include dismissed</label>
        <button className="ghost" disabled={busy} onClick={async () => {
          setBusy(true);
          try { for (const d of datasets.data ?? []) await api.analyze(d.id); insights.reload(); notify("Re-analysed with the latest learned settings"); }
          finally { setBusy(false); }
        }}>{busy ? "Analysing…" : "Re-analyse"}</button>
      </Header>
      <div className="filters" role="group" aria-label="Filter by type">
        <button aria-pressed={kind === "all"} onClick={() => setKind("all")}>All <span className="num">{all.length}</span></button>
        {kinds.map((k) => <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{KIND_LABEL[k] ?? k} <span className="num">{all.filter((i) => i.kind === k).length}</span></button>)}
      </div>
      <div className="master">
        <ul className="master-list" aria-label="Findings">
          {!insights.data && <li><Skeleton lines={8} /></li>}
          {list.map((i) => (
            <li key={i.id}>
              <button className="master-row" aria-current={current?.id === i.id ? "true" : undefined} onClick={() => setSel(i.id)}>
                <span className="row-top"><span className={`kind k-${i.kind}`}>{KIND_LABEL[i.kind] ?? i.kind}</span><span className="muted small">{dsName(i.dataset_id)}</span></span>
                <span className={i.status === "dismissed" ? "row-title gone" : "row-title"}>{i.title}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="master-detail">
          {current ? <FindingDetail key={current.id} insight={current} notify={notify} onChange={insights.reload} go={go} />
            : insights.data && <p className="muted">No findings of this type.</p>}
        </div>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- process

function Process() {
  const datasets = useAsync(api.datasets);
  const logs = (datasets.data ?? []).filter((d) => d.role === "EventLog");
  const [id, setId] = useState<number | null>(null);
  const current = id ?? logs[0]?.id ?? null;
  const model = useAsync(() => (current ? api.process(current) : Promise.resolve(null as Json | null)), [current]);
  const [minP, setMinP] = useState(0.04);
  if (datasets.data && logs.length === 0) {
    return <><Header title="Process" /><Empty title="No event log yet">Any dataset with a case id, an activity and a timestamp is mined automatically.</Empty></>;
  }
  const m = model.data;
  return (
    <>
      <Header title="Process" sub="Reconstructed from the event log: every hand-off, how often it happens and how long cases wait at it.">
        {logs.length > 1 && <>
          <label className="sr-only" htmlFor="p-ds">Event log</label>
          <select id="p-ds" value={current ?? ""} onChange={(e) => setId(+e.target.value)}>
            {logs.map((d) => <option key={d.id} value={d.id}>{d.stream}</option>)}
          </select>
        </>}
        <label className="check">Show rare paths <input type="range" min={0} max={0.3} step={0.01} value={0.3 - minP} onChange={(e) => setMinP(0.3 - +e.target.value)} /></label>
      </Header>
      {!m && <Skeleton h={320} />}
      {m && m.cases > 0 && <>
        <Ledger items={[
          { label: "Cases", value: m.cases.toLocaleString(), note: `${m.events.toLocaleString()} events` },
          { label: "Median case time", value: `${m.case_duration_hours.median.toFixed(0)} h`, note: `90% finish within ${m.case_duration_hours.p90.toFixed(0)} h` },
          { label: "Distinct paths", value: m.variant_count },
          { label: "Follow the standard path", value: `${(m.variants[0].share * 100).toFixed(0)}%` },
        ]} />
        <div className="frame"><ProcessMap model={m} minProbability={minP} /></div>
        <div className="cols">
          <section>
            <h2>Which steps a bot could take over</h2>
            <HBar rows={[...m.activities].sort((a: Json, b: Json) => b.automation_potential - a.automation_potential)
              .map((a: Json) => ({ label: a.name, value: a.automation_potential, note: `${(a.case_coverage * 100).toFixed(0)}% of cases, ${(a.human_share * 100).toFixed(0)}% done by people` }))}
              format={(v) => v.toFixed(2)} />
            <p className="fine">Score combines how often a step runs, how quick and manual it is, and how predictably it is triggered.</p>
          </section>
          <section>
            <h2>Slowest hand-offs</h2>
            <table className="table">
              <thead><tr><th>From → to</th><th className="r">Cases</th><th className="r">Mean wait</th><th className="r">vs typical</th></tr></thead>
              <tbody>{m.bottlenecks.map((b: Json) => (
                <tr key={b.source + b.target}><td>{b.source} → {b.target}</td><td className="r num">{b.frequency}</td><td className="r num">{b.mean_hours.toFixed(1)} h</td><td className="r num">{b.slowdown}×</td></tr>
              ))}</tbody>
            </table>
          </section>
        </div>
      </>}
    </>
  );
}

// --------------------------------------------------------------------------- automations

const ARTEFACTS = [["rule", "Rule"], ["n8n", "Workflow JSON"], ["skill", "Playbook"]] as const;

function Automations({ notify, go }: Ctx) {
  const list = useAsync(api.automations);
  const [open, setOpen] = useState<Automation | null>(null);
  const [tab, setTab] = useState<(typeof ARTEFACTS)[number][0]>("rule");
  const [result, setResult] = useState<Record<number, { triggered: boolean; message: string }>>({});
  const [running, setRunning] = useState<number | null>(null);
  return (
    <>
      <Header title="Automations" sub="Each automation is a rule Distill checks on a schedule, an importable n8n workflow and a playbook for agents. New ones stay off until you switch them on." />
      {list.data?.length === 0 && <Empty title="No automations yet" action={<button className="primary" onClick={() => go("findings")}>Open findings</button>}>
        Open a finding and choose “Turn into automation”, or let the agent draft them from the Brief.
      </Empty>}
      {!list.data && <Skeleton lines={6} />}
      {!!list.data?.length && (
        <ul className="auto-list">
          {list.data.map((a) => {
            const last = a.runs[a.runs.length - 1];
            const r = result[a.id];
            return (
              <li key={a.id} className="auto-row">
                <label className="switch">
                  <span className="sr-only">Run “{a.name}” automatically</span>
                  <input type="checkbox" role="switch" checked={a.enabled} onChange={async (e) => { await api.toggle(a.id, e.target.checked); list.reload(); }} />
                  <span className="track" aria-hidden />
                </label>
                <div className="auto-main">
                  <span className="auto-name">{a.name}</span>
                  <span className="auto-meta">
                    <span>{a.kind.replace(/_/g, " ")}</span>
                    <span>{a.rule.every_hours ? `checks every ${a.rule.every_hours} h` : "runs on each event"}</span>
                    <span className="num">saves about {a.est_minutes_saved_per_week.toFixed(0)} min a week</span>
                    {last && <span>last run {timeAgo(last.at)}</span>}
                  </span>
                  {r && <p className={r.triggered ? "result hit" : "result"}>{r.message}</p>}
                </div>
                <div className="auto-actions">
                  <button className="ghost" disabled={running === a.id} onClick={async () => {
                    setRunning(a.id);
                    try { const res = await api.evaluate(a.id); setResult((s) => ({ ...s, [a.id]: res })); list.reload(); } finally { setRunning(null); }
                  }}>{running === a.id ? "Running…" : "Run now"}</button>
                  <button className="ghost" onClick={async () => { setTab("rule"); setOpen(await api.automation(a.id)); }}>Details</button>
                  <ConfirmButton label="Delete" title="Delete this automation?" body={<>“{a.name}” and its run history will be removed. This cannot be undone.</>}
                    onConfirm={async () => { await api.removeAutomation(a.id); notify("Automation deleted"); list.reload(); }} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <Dialog open={!!open} onClose={() => setOpen(null)} wide title={open?.name}
        footer={open && <>
          <a className="btn ghost" href={api.url(`/api/automations/${open.id}/n8n.json`)} download>Download workflow</a>
          <a className="btn ghost" href={api.url(`/api/automations/${open.id}/SKILL.md`)} download>Download playbook</a>
        </>}>
        <div className="seg" role="tablist" aria-label="Artefact">
          {ARTEFACTS.map(([t, label]) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{label}</button>)}
        </div>
        {open && (tab === "skill" ? <Md>{(open.skill_md ?? "").replace(/^---[\s\S]*?---\n/, "")}</Md>
          : <pre className="code">{JSON.stringify(tab === "rule" ? open.rule : open.n8n_workflow, null, 2)}</pre>)}
      </Dialog>
    </>
  );
}

// --------------------------------------------------------------------------- sources

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="field"><span className="field-label">{label}</span>{children}{hint && <span className="fine">{hint}</span>}</label>;
}

function Sources({ notify, bump }: Ctx) {
  const sources = useAsync(api.sources);
  const [busy, setBusy] = useState<string | null>(null);
  const [kind, setKind] = useState("rest");
  const [form, setForm] = useState<Json>({});
  const set = (k: string) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const run = async (key: string, fn: () => Promise<unknown>, msg: (r: any) => string) => {
    setBusy(key);
    try { const r = await fn(); notify(msg(r)); sources.reload(); return true; } catch (e) { notify(String((e as Error).message)); return false; } finally { setBusy(null); }
  };
  return (
    <>
      <Header title="Sources" sub="Files, REST APIs and SQL databases are pulled; webhooks push. Syncs are incremental and can run on a schedule." />
      <section aria-labelledby="h-connected">
        <h2 id="h-connected">Connected</h2>
        {sources.data?.length === 0 && <p className="muted">Nothing connected yet.</p>}
        {!!sources.data?.length && (
          <table className="table">
            <thead><tr><th>Source</th><th>Type</th><th>Streams</th><th>Synced up to</th><th /></tr></thead>
            <tbody>
              {sources.data.map((s: Source) => (
                <tr key={s.id}>
                  <td className="cell-title">{s.name}</td>
                  <td className="muted">{s.kind}</td>
                  <td className="num">{s.datasets.map((d) => `${d.stream} (${d.rows.toLocaleString()})`).join(", ") || NA}</td>
                  <td className="muted small num">{Object.entries(s.state).map(([k, v]) => `${k}: ${String(v).slice(0, 16).replace("T", " ")}`).join(", ") || NA}</td>
                  <td><div className="row end">
                    {s.kind !== "webhook" && <button className="ghost" disabled={!!busy} onClick={() => run(`sync${s.id}`, () => api.sync(s.id), (r) => `Synced ${r.run.records} new records`).then((ok) => ok && bump())}>{busy === `sync${s.id}` ? "Syncing…" : "Sync"}</button>}
                    <button className="ghost" onClick={() => run(`check${s.id}`, () => api.check(s.id), (r) => (r.ok ? "Connection works: " : "Connection failed: ") + r.message)}>Test</button>
                    <ConfirmButton label="Remove" title={`Remove ${s.name}?`} confirmLabel="Remove source"
                      body="Its datasets, findings, automations and skills will be deleted. This cannot be undone."
                      onConfirm={() => run(`del${s.id}`, () => api.removeSource(s.id), () => "Source removed").then((ok) => ok && bump())} />
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <div className="cols">
        <section aria-labelledby="h-upload">
          <h2 id="h-upload">Upload a file</h2>
          <form onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            run("upload", () => api.upload(fd), (r) => `Imported ${r.run.records} rows and found ${r.insights} findings`).then((ok) => ok && bump());
          }}>
            <Field label="Source name"><input name="name" placeholder="Shopify export" required /></Field>
            <Field label="Stream"><input name="stream" placeholder="orders" required /></Field>
            <Field label="Cursor field" hint="Optional. Lets later uploads add only new rows."><input name="cursor_field" placeholder="updated_at" /></Field>
            <Field label="File (CSV, JSON or JSONL)"><input name="file" type="file" accept=".csv,.json,.jsonl" required /></Field>
            <button className="primary" disabled={busy === "upload"}>{busy === "upload" ? "Importing…" : "Import and analyse"}</button>
          </form>
        </section>
        <section aria-labelledby="h-connect">
          <h2 id="h-connect">Connect a system</h2>
          <div className="seg" role="tablist" aria-label="Connector type">
            {[["rest", "REST API"], ["sql", "SQL database"], ["webhook", "Webhook"]].map(([k, l]) => <button key={k} role="tab" aria-selected={kind === k} onClick={() => { setKind(k); setForm({}); }}>{l}</button>)}
          </div>
          <form onSubmit={(e) => {
            e.preventDefault();
            const { name, ...rest } = form;
            const config = kind === "sql"
              ? { url: rest.url, queries: { [rest.stream || "data"]: rest.query }, cursor_fields: rest.cursor ? { [rest.stream || "data"]: rest.cursor } : {} }
              : { ...rest };
            run("connect", () => api.createSource({ name: name || kind, kind, config }), () => "Source created. Press Sync to pull data.");
          }}>
            <Field label="Name"><input onChange={set("name")} placeholder="Pipedrive" /></Field>
            <Field label="Stream"><input required onChange={set("stream")} placeholder="deals" /></Field>
            {kind === "rest" && <>
              <Field label="URL"><input required type="url" onChange={set("url")} placeholder="https://api.example.com/v1/orders" /></Field>
              <Field label="Records path" hint="Where the list of records sits in the response."><input onChange={set("records_path")} placeholder="data.items" /></Field>
              <Field label="Bearer token" hint="Stored on the server and never shown again."><input type="password" autoComplete="off" onChange={set("bearer_token")} /></Field>
            </>}
            {kind === "sql" && <>
              <Field label="Connection URL"><input required onChange={set("url")} placeholder="postgresql://user:pass@host/db" /></Field>
              <Field label="Query"><textarea required onChange={set("query")} placeholder="SELECT * FROM orders" /></Field>
              <Field label="Cursor column" hint="Optional. Enables incremental syncs."><input onChange={set("cursor")} placeholder="updated_at" /></Field>
            </>}
            {kind === "webhook" && <p className="muted">After creating it, POST JSON events to <code>/api/sources/&lt;id&gt;/events</code>.</p>}
            <button className="primary" disabled={busy === "connect"}>Create source</button>
          </form>
        </section>
      </div>
      <DatasetProfiles />
    </>
  );
}

function DatasetProfiles() {
  const datasets = useAsync(api.datasets);
  const [open, setOpen] = useState<Json | null>(null);
  const glossary: Record<string, string> = Object.fromEntries((open?.profile.glossary?.columns ?? []).map((c: Json) => [c.name, c.meaning]));
  if (!datasets.data?.length) return null;
  return (
    <section aria-labelledby="h-ds">
      <h2 id="h-ds">What Distill detected in each dataset</h2>
      <div className="filters" role="group" aria-label="Dataset">
        {datasets.data.map((d: Dataset) => (
          <button key={d.id} aria-pressed={open?.id === d.id} onClick={async () => setOpen(await api.dataset(d.id))}>
            {d.stream} <span className="num">{d.rows.toLocaleString()}</span>
          </button>
        ))}
      </div>
      {open && <>
        <p className="muted">{open.profile.glossary?.summary ?? `Read as ${open.role === "EventLog" ? "an event log" : open.role === "TransactionTable" ? "a transaction table" : "a table"}.`}</p>
        <table className="table">
          <thead><tr><th>Column</th><th>Meaning</th><th>Detected as</th><th className="r">Distinct</th><th className="r">Empty</th></tr></thead>
          <tbody>{open.profile.fields.map((f: Json) => (
            <tr key={f.name}><td><code>{f.name}</code></td><td className="muted">{glossary[f.name] ?? NA}</td><td>{f.semantic_type}</td>
              <td className="r num">{f.distinct.toLocaleString()}</td><td className="r num">{(f.null_ratio * 100).toFixed(1)}%</td></tr>
          ))}</tbody>
        </table>
      </>}
    </section>
  );
}
