import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, type AgentAnswer, type AgentStep, type Automation, type Dataset, type Insight, type Json, type Learning, type Source } from "./api";
import { EvidenceChart, HBar } from "./charts";
import { ProcessMap } from "./ProcessMap";

type Page = "overview" | "insights" | "process" | "automations" | "sources" | "learning";

const PAGES: { id: Page; label: string; hint: string }[] = [
  { id: "overview", label: "Overview", hint: "Digest & ask your data" },
  { id: "insights", label: "Insights", hint: "Ranked findings" },
  { id: "process", label: "Process", hint: "Mined from event logs" },
  { id: "automations", label: "Automations", hint: "Rules, n8n, skills" },
  { id: "sources", label: "Sources", hint: "Connect & sync" },
  { id: "learning", label: "Learning", hint: "What the system learned" },
];

const KIND_LABEL: Record<string, string> = {
  trend: "Trend", segment_driver: "Driver", anomaly: "Anomaly", concentration: "Concentration",
  correlation: "Correlation", bottleneck: "Bottleneck", rework: "Rework", automation_candidate: "Automate",
  variability: "Variability",
};
const ACTIONABLE = new Set(["segment_driver", "trend", "anomaly", "concentration", "bottleneck", "automation_candidate", "rework"]);

function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    fn().then(setData).catch((e) => setError(String(e.message ?? e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(reload, [reload]);
  return { data, error, reload, setData };
}

function AiBadge() {
  const ai = useAsync(api.ai);
  if (!ai.data) return null;
  return (
    <div className={ai.data.enabled ? "ai-badge on" : "ai-badge"} title={ai.data.capabilities.join(" · ")}>
      <span className="dot-status" />{ai.data.enabled ? `AI agent: ${ai.data.model}` : "AI agent off (set ANTHROPIC_API_KEY)"}
    </div>
  );
}

function Steps({ steps }: { steps?: AgentStep[] }) {
  if (!steps?.length) return null;
  return (
    <details className="steps">
      <summary>{steps.length} tool calls by the agent</summary>
      <ol>{steps.map((st, i) => (
        <li key={i} className={st.ok ? "" : "bad"}><code>{st.tool}</code> {Object.entries(st.input).filter(([, v]) => v !== null && !(Array.isArray(v) && !v.length)).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(" ")} <span className="muted">→ {st.summary}</span></li>
      ))}</ol>
    </details>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>(() => (location.hash.slice(1) as Page) || "overview");
  const [toast, setToast] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const bump = () => setVersion((v) => v + 1);
  const notify = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3500); };
  const go = (p: Page) => { location.hash = p; setPage(p); };

  return (
    <div className="shell">
      <nav className="side">
        <div className="brand"><span className="logo">◆</span> Distill</div>
        <p className="tagline">Company data → insights → automations</p>
        <AiBadge />
        {PAGES.map((p) => (
          <button key={p.id} className={page === p.id ? "nav active" : "nav"} onClick={() => go(p.id)}>
            <span>{p.label}</span><small>{p.hint}</small>
          </button>
        ))}
      </nav>
      <main key={version}>
        {page === "overview" && <Overview go={go} notify={notify} bump={bump} />}
        {page === "insights" && <Insights notify={notify} go={go} />}
        {page === "process" && <Process />}
        {page === "automations" && <Automations notify={notify} />}
        {page === "sources" && <Sources notify={notify} bump={bump} />}
        {page === "learning" && <LearningPage />}
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

function Header({ title, sub, children }: { title: string; sub?: string; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div><h1>{title}</h1>{sub && <p className="muted">{sub}</p>}</div>
      <div className="actions">{children}</div>
    </header>
  );
}

function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return <div className="stat"><span className="stat-label">{label}</span><span className="stat-value">{value}</span>{note && <span className="muted small">{note}</span>}</div>;
}

function Empty({ go }: { go: (p: Page) => void }) {
  return (
    <div className="empty">
      <h2>No data yet</h2>
      <p>Load the demo company or connect your own CSV, database, API or webhook.</p>
      <button className="primary" onClick={() => go("sources")}>Connect a source</button>
    </div>
  );
}

// --------------------------------------------------------------------------- overview

function Overview({ go, notify, bump }: { go: (p: Page) => void; notify: (m: string) => void; bump: () => void }) {
  const datasets = useAsync(api.datasets);
  const insights = useAsync(() => api.insights());
  const learning = useAsync(api.learning);
  const digest = useAsync(api.digest);
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<AgentAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  if (datasets.data && datasets.data.length === 0) {
    return (
      <>
        <Header title="Welcome to Distill" sub="Collect company data, find what matters, and turn it into automations and agent skills." />
        <div className="empty">
          <h2>Start with a realistic demo company</h2>
          <p>120 days of orders and support-desk events with planted patterns for the engine to discover.</p>
          <button className="primary" disabled={busy} onClick={async () => {
            setBusy(true);
            try { const r = await api.demo(); notify(`Demo loaded: ${r.insights} insights found`); bump(); }
            catch (e) { notify(String(e)); } finally { setBusy(false); }
          }}>{busy ? "Loading…" : "Load demo company"}</button>
          <button className="ghost" onClick={() => go("sources")}>Connect my own data</button>
        </div>
      </>
    );
  }

  const top = (insights.data ?? []).slice(0, 4);
  const rows = (datasets.data ?? []).reduce((s, d) => s + d.rows, 0);
  return (
    <>
      <Header title="Overview" sub="What changed, what is slow, and what can be automated." />
      <section className="stats">
        <Stat label="Datasets" value={datasets.data?.length ?? "–"} note={`${rows.toLocaleString()} records`} />
        <Stat label="Open insights" value={insights.data?.filter((i) => i.status === "new").length ?? "–"} />
        <Stat label="Automations live" value={learning.data?.automations_enabled ?? "–"} />
        <Stat label="Time saved / week" value={learning.data ? `${Math.round(learning.data.minutes_saved_per_week / 60 * 10) / 10}h` : "–"} note="from enabled automations" />
      </section>
      <section className="grid2">
        <div className="card">
          <h3>Executive digest <span className="pill">{digest.data?.engine}</span></h3>
          <pre className="digest">{digest.data?.text ?? "…"}</pre>
        </div>
        <div className="card">
          <h3>Ask your data</h3>
          <form onSubmit={async (e: FormEvent) => {
            e.preventDefault(); if (!q) return; setAnswer(null); setAsking(true);
            try { setAnswer(await api.ask(q)); } finally { setAsking(false); }
          }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Why is the North region down? What should we automate first?" />
            <button className="primary" disabled={asking}>{asking ? "The agent is analysing…" : "Ask the analyst agent"}</button>
          </form>
          {answer && <><pre className="digest">{answer.text}<span className="pill">{answer.engine}</span></pre><Steps steps={answer.steps} /></>}
          <p className="muted small">A Claude agent with tools runs aggregate queries, reads findings and process models, and cites the numbers it got. Raw rows never leave the server.</p>
        </div>
      </section>
      <h2 className="section">Top insights</h2>
      <div className="insight-grid">
        {top.map((i) => <InsightCard key={i.id} insight={i} compact notify={notify} onChange={insights.reload} go={go} />)}
      </div>
      {top.length > 0 && <button className="ghost" onClick={() => go("insights")}>See all insights →</button>}
    </>
  );
}

// --------------------------------------------------------------------------- insights

function InsightCard({ insight, compact, notify, onChange, go }:
  { insight: Insight; compact?: boolean; notify: (m: string) => void; onChange: () => void; go: (p: Page) => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    setBusy(true);
    try { await fn(); notify(msg); onChange(); } catch (e) { notify(String(e)); } finally { setBusy(false); }
  };
  return (
    <article className={`card insight ${insight.status}`}>
      <div className="insight-head">
        <span className={`kind k-${insight.kind}`}>{KIND_LABEL[insight.kind] ?? insight.kind}</span>
        <span className="score" title="Rank = statistical strength × learned usefulness of this kind">rank {insight.score.toFixed(2)}</span>
      </div>
      <h3>{insight.title}</h3>
      <p className="muted">{insight.body}</p>
      {!compact && <EvidenceChart evidence={insight.evidence} />}
      {!compact && insight.evidence.ai_investigation && (
        <div className="ai-box">
          <b>AI root-cause analysis</b> <span className="pill">{insight.evidence.ai_investigation.engine}</span>
          <pre className="digest">{insight.evidence.ai_investigation.text}</pre>
          <Steps steps={insight.evidence.ai_investigation.steps} />
        </div>
      )}
      <div className="row">
        {ACTIONABLE.has(insight.kind) && (
          <button className="primary" disabled={busy} onClick={() => act(() => api.automate(insight.id), "Automation created").then(() => go("automations"))}>
            ⚡ Automate
          </button>
        )}
        {!compact && <button className="ghost" disabled={busy} onClick={() => act(() => api.investigate(insight.id), "Investigation complete")}>
          {busy ? "Investigating…" : "🔍 Investigate with AI"}</button>}
        {insight.status === "new" && <>
          <button className="ghost" disabled={busy} onClick={() => act(() => api.feedback(insight.id, true), "Thanks: similar insights will rank higher")}>👍 Useful</button>
          <button className="ghost" disabled={busy} onClick={() => act(() => api.feedback(insight.id, false), "Dismissed: the system will adapt")}>Dismiss</button>
        </>}
        {insight.status !== "new" && <span className="pill">{insight.status}</span>}
      </div>
    </article>
  );
}

function Insights({ notify, go }: { notify: (m: string) => void; go: (p: Page) => void }) {
  const datasets = useAsync(api.datasets);
  const [showDismissed, setShowDismissed] = useState(false);
  const insights = useAsync(() => api.insights(showDismissed), [showDismissed]);
  const [filter, setFilter] = useState<number | "all">("all");
  const [kind, setKind] = useState<string>("all");
  if (datasets.data?.length === 0) return <Empty go={go} />;
  const list = (insights.data ?? []).filter((i) => (filter === "all" || i.dataset_id === filter) && (kind === "all" || i.kind === kind));
  const kinds = [...new Set((insights.data ?? []).map((i) => i.kind))];
  return (
    <>
      <Header title="Insights" sub="Ranked by statistical strength × what you have told Distill is useful.">
        <select value={filter} onChange={(e) => setFilter(e.target.value === "all" ? "all" : +e.target.value)}>
          <option value="all">All datasets</option>
          {datasets.data?.map((d) => <option key={d.id} value={d.id}>{d.stream}</option>)}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="all">All kinds</option>
          {kinds.map((k) => <option key={k} value={k}>{KIND_LABEL[k] ?? k}</option>)}
        </select>
        <label className="check"><input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} /> show dismissed</label>
        <button className="ghost" onClick={async () => {
          for (const d of datasets.data ?? []) await api.analyze(d.id);
          insights.reload(); notify("Re-analysed with the latest learned settings");
        }}>↻ Re-analyse</button>
      </Header>
      <div className="insight-list">
        {list.map((i) => <InsightCard key={i.id} insight={i} notify={notify} onChange={insights.reload} go={go} />)}
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
    return <><Header title="Process mining" /><div className="empty"><p>No event log yet. Any dataset with a case id, an activity and a timestamp is mined automatically.</p></div></>;
  }
  const m = model.data;
  return (
    <>
      <Header title="Process mining" sub="Discovered from the event log: every hand-off, its volume and its waiting time.">
        <select value={current ?? ""} onChange={(e) => setId(+e.target.value)}>
          {logs.map((d) => <option key={d.id} value={d.id}>{d.stream}</option>)}
        </select>
        <label className="check">detail <input type="range" min={0} max={0.3} step={0.01} value={0.3 - minP} onChange={(e) => setMinP(0.3 - +e.target.value)} /></label>
      </Header>
      {m && m.cases > 0 && <>
        <section className="stats">
          <Stat label="Cases" value={m.cases.toLocaleString()} note={`${m.events.toLocaleString()} events`} />
          <Stat label="Median case time" value={`${m.case_duration_hours.median.toFixed(0)}h`} note={`p90 ${m.case_duration_hours.p90.toFixed(0)}h`} />
          <Stat label="Path variants" value={m.variant_count} />
          <Stat label="Top variant" value={`${(m.variants[0].share * 100).toFixed(0)}%`} note="of cases" />
        </section>
        <div className="card"><ProcessMap model={m} minProbability={minP} /></div>
        <section className="grid2">
          <div className="card">
            <h3>Automation potential by activity</h3>
            <HBar rows={[...m.activities].sort((a: Json, b: Json) => b.automation_potential - a.automation_potential)
              .map((a: Json) => ({ label: a.name, value: a.automation_potential, note: `${(a.case_coverage * 100).toFixed(0)}% of cases, ${(a.human_share * 100).toFixed(0)}% manual` }))}
              format={(v) => v.toFixed(2)} />
            <p className="muted small">Frequent + quick + manual + deterministically triggered = good bot candidate.</p>
          </div>
          <div className="card">
            <h3>Slowest hand-offs</h3>
            <table>
              <thead><tr><th>From → to</th><th>Cases</th><th>Mean wait</th><th>vs typical</th></tr></thead>
              <tbody>{m.bottlenecks.map((b: Json) => (
                <tr key={b.source + b.target}><td>{b.source} → {b.target}</td><td>{b.frequency}</td><td>{b.mean_hours.toFixed(1)}h</td><td>{b.slowdown}×</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      </>}
    </>
  );
}

// --------------------------------------------------------------------------- automations

function Automations({ notify }: { notify: (m: string) => void }) {
  const list = useAsync(api.automations);
  const [open, setOpen] = useState<Automation | null>(null);
  const [tab, setTab] = useState<"rule" | "n8n" | "skill">("rule");
  const [result, setResult] = useState<Record<number, { triggered: boolean; message: string }>>({});
  return (
    <>
      <Header title="Automations" sub="Each one ships as a Distill rule, an importable n8n workflow, and an Agent Skill (SKILL.md)." />
      {list.data?.length === 0 && <div className="empty"><p>No automations yet. Open an insight and press ⚡ Automate.</p></div>}
      <div className="auto-list">
        {list.data?.map((a) => (
          <article key={a.id} className="card auto">
            <div className="row spread">
              <div>
                <span className="kind">{a.kind.replace("_", " ")}</span>
                <h3>{a.name}</h3>
                <p className="muted small">≈ {a.est_minutes_saved_per_week.toFixed(0)} min saved / week{a.rule.every_hours ? ` · runs every ${a.rule.every_hours}h` : " · event-driven"}</p>
              </div>
              <label className="switch" title="Enable">
                <input type="checkbox" checked={a.enabled} onChange={async (e) => { await api.toggle(a.id, e.target.checked); list.reload(); }} />
                <span />
              </label>
            </div>
            {result[a.id] && <p className={result[a.id].triggered ? "alert on" : "alert"}>{result[a.id].triggered ? "🔔 " : "✓ "}{result[a.id].message}</p>}
            <div className="row">
              <button className="primary" onClick={async () => {
                const r = await api.evaluate(a.id); setResult((s) => ({ ...s, [a.id]: r })); list.reload();
              }}>Run now</button>
              <button className="ghost" onClick={async () => { setOpen(await api.automation(a.id)); setTab("rule"); }}>View artefacts</button>
              <a className="ghost btn" href={api.url(`/api/automations/${a.id}/n8n.json`)}>n8n.json</a>
              <a className="ghost btn" href={api.url(`/api/automations/${a.id}/SKILL.md`)}>SKILL.md</a>
              <button className="ghost danger" onClick={async () => { await api.removeAutomation(a.id); notify("Deleted"); list.reload(); }}>Delete</button>
            </div>
            {a.runs.length > 0 && <p className="muted small">Last run: {new Date(a.runs[a.runs.length - 1].at).toLocaleString()}</p>}
          </article>
        ))}
      </div>
      {open && (
        <div className="modal" onClick={() => setOpen(null)}>
          <div className="modal-body" onClick={(e) => e.stopPropagation()}>
            <div className="row spread"><h3>{open.name}</h3><button className="ghost" onClick={() => setOpen(null)}>✕</button></div>
            <div className="tabs">
              {(["rule", "n8n", "skill"] as const).map((t) => <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>{t === "skill" ? "SKILL.md" : t === "n8n" ? "n8n workflow" : "Rule"}</button>)}
            </div>
            <pre className="code">{tab === "rule" ? JSON.stringify(open.rule, null, 2) : tab === "n8n" ? JSON.stringify(open.n8n_workflow, null, 2) : open.skill_md}</pre>
          </div>
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- sources

function Sources({ notify, bump }: { notify: (m: string) => void; bump: () => void }) {
  const sources = useAsync(api.sources);
  const [busy, setBusy] = useState<string | null>(null);
  const [kind, setKind] = useState("rest");
  const [form, setForm] = useState<Json>({});
  const run = async (key: string, fn: () => Promise<unknown>, msg: (r: any) => string) => {
    setBusy(key);
    try { const r = await fn(); notify(msg(r)); sources.reload(); } catch (e) { notify(String((e as Error).message)); } finally { setBusy(null); }
  };
  return (
    <>
      <Header title="Sources" sub="Pull from files, REST APIs and SQL databases, or push events to a webhook. Incremental syncs resume from a saved cursor." />
      <section className="grid2">
        <div className="card">
          <h3>Upload a CSV / JSON file</h3>
          <form onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            run("upload", () => api.upload(fd), (r) => `Imported ${r.run.records} rows, ${r.insights} insights`).then(bump);
          }}>
            <input name="name" placeholder="Source name (e.g. Shopify export)" required />
            <input name="stream" placeholder="Stream name (e.g. orders)" required />
            <input name="cursor_field" placeholder="Cursor field for incremental sync (optional)" />
            <input name="file" type="file" accept=".csv,.json,.jsonl" required />
            <button className="primary" disabled={busy === "upload"}>{busy === "upload" ? "Importing…" : "Import & analyse"}</button>
          </form>
        </div>
        <div className="card">
          <h3>Connect a system</h3>
          <div className="tabs">{["rest", "sql", "webhook"].map((k) => <button key={k} className={kind === k ? "tab active" : "tab"} onClick={() => { setKind(k); setForm({}); }}>{k.toUpperCase()}</button>)}</div>
          <form onSubmit={(e) => {
            e.preventDefault();
            const { name, ...rest } = form;
            const config = kind === "sql"
              ? { url: rest.url, queries: { [rest.stream || "data"]: rest.query }, cursor_fields: rest.cursor ? { [rest.stream || "data"]: rest.cursor } : {} }
              : { ...rest };
            run("connect", () => api.createSource({ name: name || kind, kind, config }), () => "Source created. Press Sync to pull data.");
          }}>
            <input placeholder="Name" onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input placeholder="Stream name" required onChange={(e) => setForm({ ...form, stream: e.target.value })} />
            {kind === "rest" && <>
              <input placeholder="https://api.example.com/v1/orders" required onChange={(e) => setForm({ ...form, url: e.target.value })} />
              <input placeholder="Records path (e.g. data.items)" onChange={(e) => setForm({ ...form, records_path: e.target.value })} />
              <input placeholder="Bearer token (stored, never shown again)" type="password" onChange={(e) => setForm({ ...form, bearer_token: e.target.value })} />
            </>}
            {kind === "sql" && <>
              <input placeholder="postgresql://user:pass@host/db" required onChange={(e) => setForm({ ...form, url: e.target.value })} />
              <textarea placeholder="SELECT * FROM orders" required onChange={(e) => setForm({ ...form, query: e.target.value })} />
              <input placeholder="Cursor column (optional)" onChange={(e) => setForm({ ...form, cursor: e.target.value })} />
            </>}
            {kind === "webhook" && <p className="muted small">After creating, POST JSON events to <code>/api/sources/&lt;id&gt;/events</code>.</p>}
            <button className="primary" disabled={busy === "connect"}>Create source</button>
          </form>
        </div>
      </section>
      <h2 className="section">Connected</h2>
      <table className="card">
        <thead><tr><th>Name</th><th>Type</th><th>Streams</th><th>Cursor</th><th /></tr></thead>
        <tbody>
          {sources.data?.map((s: Source) => (
            <tr key={s.id}>
              <td>{s.name}</td><td><span className="pill">{s.kind}</span></td>
              <td>{s.datasets.map((d) => `${d.stream} (${d.rows.toLocaleString()})`).join(", ") || "–"}</td>
              <td className="small">{Object.entries(s.state).map(([k, v]) => `${k}: ${String(v).slice(0, 19)}`).join(" · ") || "–"}</td>
              <td className="row">
                {s.kind !== "webhook" && <button className="ghost" disabled={!!busy} onClick={() => run(`sync${s.id}`, () => api.sync(s.id), (r) => `Synced ${r.run.records} new records`).then(bump)}>{busy === `sync${s.id}` ? "Syncing…" : "Sync"}</button>}
                <button className="ghost" onClick={() => run(`check${s.id}`, () => api.check(s.id), (r) => (r.ok ? "✓ " : "✕ ") + r.message)}>Test</button>
                <button className="ghost danger" onClick={() => run(`del${s.id}`, () => api.removeSource(s.id), () => "Source removed").then(bump)}>Remove</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <DatasetProfiles />
    </>
  );
}

function DatasetProfiles() {
  const datasets = useAsync(api.datasets);
  const [open, setOpen] = useState<Json | null>(null);
  return (
    <>
      <h2 className="section">Datasets & detected semantics</h2>
      <div className="row wrap">
        {datasets.data?.map((d: Dataset) => (
          <button key={d.id} className="card ds" onClick={async () => setOpen(await api.dataset(d.id))}>
            <b>{d.stream}</b><span className="pill">{d.role}</span><span className="muted small">{d.rows.toLocaleString()} rows</span>
          </button>
        ))}
      </div>
      {open && (
        <div className="card">
          <h3>{open.stream} <span className="pill">{open.role}</span></h3>
          <table>
            <thead><tr><th>Column</th><th>Detected as</th><th>Kind</th><th>Distinct</th><th>Nulls</th></tr></thead>
            <tbody>{open.profile.fields.map((f: Json) => (
              <tr key={f.name}><td>{f.name}</td><td><span className="pill">{f.semantic_type}</span></td><td>{f.kind}</td><td>{f.distinct}</td><td>{(f.null_ratio * 100).toFixed(1)}%</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- learning

function LearningPage() {
  const l = useAsync<Learning>(api.learning);
  return (
    <>
      <Header title="Learning" sub="Distill adapts to your feedback: which kinds of insight matter here, and how sensitive each detector should be." />
      <section className="grid2">
        <div className="card">
          <h3>Learned usefulness by insight kind</h3>
          {l.data?.arms.length
            ? <HBar rows={l.data.arms.map((a) => ({ label: KIND_LABEL[a.kind] ?? a.kind, value: a.posterior_mean, note: `${a.ratings} ratings · Beta(${a.alpha}, ${a.beta})` }))} />
            : <p className="muted">Rate a few insights as useful or dismiss them; this chart shows the posterior belief per kind.</p>}
          <p className="muted small">Beta-Bernoulli posterior mean. Ranking = severity × (mean + exploration bonus), so rarely-rated kinds still surface.</p>
        </div>
        <div className="card">
          <h3>Detector sensitivity</h3>
          {l.data?.thresholds.length
            ? <table><thead><tr><th>Metric</th><th>Anomaly z-threshold</th></tr></thead>
              <tbody>{l.data.thresholds.map((t) => <tr key={t.metric}><td>{t.metric}</td><td>{t.z.toFixed(2)} <span className="muted small">(default 3.50)</span></td></tr>)}</tbody></table>
            : <p className="muted">All detectors at default sensitivity (z = 3.5). Dismissing an anomaly raises its threshold; marking one useful lowers it.</p>}
        </div>
      </section>
      <section className="stats">
        <Stat label="Automations enabled" value={l.data?.automations_enabled ?? "–"} />
        <Stat label="Estimated time saved" value={l.data ? `${l.data.minutes_saved_per_week.toFixed(0)} min/week` : "–"} />
      </section>
    </>
  );
}
