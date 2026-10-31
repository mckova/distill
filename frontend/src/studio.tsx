import { useEffect, useState, type FormEvent } from "react";
import { Download, X } from "lucide-react";
import { api, type Learning, type Skill } from "./api";
import { HBar } from "./charts";
import { Empty, fmtHours, fmtMoney, Header, Ledger, Md, NA, Skeleton, Tag, timeAgo, useAsync } from "./ui";

export const KIND_LABEL: Record<string, string> = {
  trend: "Trend", segment_driver: "Driver", anomaly: "Anomaly", concentration: "Concentration",
  correlation: "Correlation", bottleneck: "Bottleneck", rework: "Rework", automation_candidate: "Automatable step",
  variability: "Variability", data_quality: "Data quality",
};

export const fmtHoursWk = (h: number) => `${Math.round(h).toLocaleString()} h/wk`;

// --------------------------------------------------------------------------- skills (library + reader)

const SKILL_KIND: Record<Skill["kind"], string> = {
  sop: "Operating procedures",
  data_context: "Data context",
  automation: "Automation playbooks",
};

export function Skills({ notify }: { notify: (m: string) => void }) {
  const skills = useAsync(api.skills);
  const [selId, setSelId] = useState<number | null>(null);
  const [doc, setDoc] = useState<Skill | null>(null);
  const [raw, setRaw] = useState(false);
  const [busy, setBusy] = useState(false);
  const list = skills.data ?? [];
  const current = selId ?? list[0]?.id ?? null;
  useEffect(() => { if (current) api.skill(current).then(setDoc); }, [current]);
  return (
    <>
      <Header title="Skills" sub="Playbooks written from your own data, in the Agent Skills format. Give them to any agent and it works the way this company actually works.">
        <button className="ghost" disabled={busy} onClick={async () => {
          setBusy(true);
          try { skills.setData(await api.generateSkills()); notify("Skills rewritten from the latest data"); }
          catch (e) { notify(String((e as Error).message)); } finally { setBusy(false); }
        }}>{busy ? "Rewriting…" : "Rewrite from data"}</button>
        <a className="btn primary" href={api.url("/api/skills/export.zip")} download><Download size={14} aria-hidden /> Download all</a>
      </Header>
      {skills.data?.length === 0 && <Empty title="No skills yet">Skills are written after the first sync. Connect a source or load the demo company.</Empty>}
      {!skills.data && <Skeleton lines={8} />}
      {list.length > 0 && (
        <div className="library">
          <nav className="lib-index" aria-label="Skills">
            {(["sop", "data_context", "automation"] as const).map((k) => {
              const items = list.filter((s) => s.kind === k);
              if (!items.length) return null;
              return (
                <div key={k} className="lib-group">
                  <span className="lib-label">{SKILL_KIND[k]}</span>
                  {items.map((s) => (
                    <button key={s.id} className="lib-item" aria-current={current === s.id ? "true" : undefined} onClick={() => { setSelId(s.id); setRaw(false); }}>
                      <span className="lib-slug">{s.slug}</span>
                      <span className="lib-ver num">v{s.version}</span>
                    </button>
                  ))}
                </div>
              );
            })}
          </nav>
          <article className="reader">
            {!doc && <Skeleton lines={10} />}
            {doc && <>
              <header className="reader-head">
                <div>
                  <p className="reader-meta num">
                    {doc.slug}/SKILL.md · version {doc.version} · {doc.author.startsWith("claude") ? `written by ${doc.author}` : "generated from template"} · updated {timeAgo(doc.updated_at)}
                  </p>
                  <p className="reader-desc">{doc.description}</p>
                </div>
                <div className="row end">
                  <div className="seg" role="tablist" aria-label="View">
                    <button role="tab" aria-selected={!raw} onClick={() => setRaw(false)}>Read</button>
                    <button role="tab" aria-selected={raw} onClick={() => setRaw(true)}>Source</button>
                  </div>
                  <button className="ghost" onClick={() => {
                    if (!doc.content) return;
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(new Blob([doc.content], { type: "text/markdown" }));
                    a.download = `${doc.slug}-SKILL.md`; a.click(); URL.revokeObjectURL(a.href);
                  }}>Download</button>
                </div>
              </header>
              {doc.content && (raw ? <pre className="code">{doc.content}</pre> : <div className="prose"><Md>{doc.content.replace(/^---[\s\S]*?---\n/, "")}</Md></div>)}
            </>}
          </article>
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- optimization

const OPP_LABEL: Record<string, string> = {
  automate_step: "Automate a step", remove_wait: "Remove a wait", remove_loop: "Remove a loop",
  recover_revenue: "Recover revenue", protect_key_accounts: "Protect key accounts",
};

export function Optimize({ notify }: { notify: (m: string) => void }) {
  const o = useAsync(api.optimization);
  const [busy, setBusy] = useState<number | null>(null);
  const d = o.data;
  return (
    <>
      <Header title="Optimize" sub="Each opportunity priced the same way: staff hours, hours cases spend waiting, or revenue at risk, per week. Staff time and waiting time are never added together." />
      <Ledger items={[
        { label: "Staff time automatable", value: d ? `${d.staff_hours_per_week} h/wk` : null, note: "manual work a bot can take over", tone: "good" },
        { label: "Case waiting removable", value: d ? fmtHoursWk(d.wait_hours_per_week) : null, note: "idle hours, summed over cases" },
        { label: "Revenue at risk", value: d ? `${fmtMoney(d.total_revenue_at_risk_per_week)}/wk` : null, note: "from declining segments and key accounts", tone: "warn" },
      ]} />
      {d?.simulations.map((s) => (
        <section key={s.dataset} className="sim" aria-labelledby={`sim-${s.dataset}`}>
          <h2 id={`sim-${s.dataset}`}>If every fix below ships, a <code>{s.dataset}</code> case takes {fmtHours(s.projected_case_hours)} instead of {fmtHours(s.current_case_hours)}.</h2>
          <div className="sim-bars" aria-hidden>
            <div className="sim-row"><span>Today</span><span className="sim-track"><span className="sim-fill now" /></span><span className="num">{fmtHours(s.current_case_hours)}</span></div>
            <div className="sim-row"><span>After fixes</span><span className="sim-track"><span className="sim-fill next" style={{ transform: `scaleX(${s.projected_case_hours / Math.max(s.current_case_hours, 0.01)})` }} /></span><span className="num">{fmtHours(s.projected_case_hours)}</span></div>
          </div>
          <p className="fine">A {s.reduction_pct}% shorter average case. Simple additive model: each removed wait or loop subtracts its mean hours, weighted by how many cases pass through it.</p>
        </section>
      ))}
      <section aria-labelledby="h-ranked">
        <h2 id="h-ranked">Ranked by impact</h2>
        {!d && <Skeleton lines={6} />}
        {d && d.opportunities.length === 0 && <Empty title="Nothing to optimize yet">Opportunities appear once there is an event log or a revenue table to analyse.</Empty>}
        {d && d.opportunities.length > 0 && (
          <table className="table">
            <thead><tr><th>Opportunity</th><th>Lever</th><th className="r">Time</th><th className="r">Revenue at risk</th><th /></tr></thead>
            <tbody>
              {d.opportunities.map((x) => (
                <tr key={`${x.kind}${x.insight_id}`}>
                  <td><span className="cell-title">{x.title}</span><span className="cell-sub">{x.detail}</span></td>
                  <td className="muted nowrap">{OPP_LABEL[x.kind] ?? x.kind}</td>
                  <td className="r num nowrap">{x.hours_per_week ? <>{fmtHoursWk(x.hours_per_week)} <span className="unit">{x.unit === "staff" ? "staff" : "waiting"}</span></> : NA}</td>
                  <td className="r num nowrap">{x.revenue_at_risk_per_week ? `${fmtMoney(x.revenue_at_risk_per_week)}/wk` : NA}</td>
                  <td className="r">{x.automated
                    ? <Tag tone="good">Automated</Tag>
                    : <button className="ghost" disabled={busy === x.insight_id} onClick={async () => {
                      setBusy(x.insight_id);
                      try { await api.automate(x.insight_id); notify("Automation drafted. Switch it on under Automations."); o.reload(); }
                      catch (e) { notify(String((e as Error).message)); } finally { setBusy(null); }
                    }}>Automate</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

// --------------------------------------------------------------------------- learning + memory

const MEM_SOURCE = { feedback: "from a dismissed finding", agent: "noted by the agent", user: "added by you" } as const;

export function LearningPage() {
  const l = useAsync<Learning>(api.learning);
  const mem = useAsync(api.memory);
  const [note, setNote] = useState("");
  const add = async (e: FormEvent) => {
    e.preventDefault();
    if (!note.trim()) return;
    await api.remember(note); setNote(""); mem.reload();
  };
  return (
    <>
      <Header title="Learning" sub="What Distill has learned about this company: the context the agent always reads, which findings matter here, and how sensitive each detector should be." />
      <section aria-labelledby="h-memory">
        <h2 id="h-memory">Company memory</h2>
        <p className="muted measure">The agent reads these notes before every question, review and playbook it writes. Dismissing a finding with a reason adds one; the agent can add its own.</p>
        <form className="inline-form" onSubmit={add}>
          <label htmlFor="mem-input" className="sr-only">New note</label>
          <input id="mem-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Our fiscal week starts on Sunday" maxLength={500} />
          <button className="primary" disabled={!note.trim()}>Add note</button>
        </form>
        {mem.data?.length === 0 && <p className="fine">No notes yet.</p>}
        <ul className="mem-list">
          {mem.data?.map((m) => (
            <li key={m.id}>
              <span className="mem-text">{m.text}</span>
              <span className="mem-meta">{MEM_SOURCE[m.source]}, {timeAgo(m.created_at)}</span>
              <button className="icon-btn" aria-label={`Forget note: ${m.text}`} onClick={async () => { await api.forget(m.id); mem.reload(); }}><X size={14} /></button>
            </li>
          ))}
        </ul>
      </section>
      <div className="cols">
        <section aria-labelledby="h-arms">
          <h2 id="h-arms">How useful each type of finding has been</h2>
          {l.data?.arms.length
            ? <HBar rows={l.data.arms.map((a) => ({ label: KIND_LABEL[a.kind] ?? a.kind, value: a.posterior_mean, note: `${a.ratings} ratings, Beta(${a.alpha}, ${a.beta})` }))} />
            : <p className="muted">Mark a few findings useful or not; this shows the learned belief per type.</p>}
          <p className="fine">Posterior mean of a Beta-Bernoulli model. Rank is severity times (mean plus an exploration bonus), so rarely rated types still surface.</p>
        </section>
        <section aria-labelledby="h-z">
          <h2 id="h-z">Anomaly detector sensitivity</h2>
          {l.data?.thresholds.length
            ? <table className="table"><thead><tr><th>Metric</th><th className="r">z threshold</th><th className="r">Default</th></tr></thead>
              <tbody>{l.data.thresholds.map((t) => <tr key={t.metric}><td>{t.metric}</td><td className="r num">{t.z.toFixed(2)}</td><td className="r num muted">3.50</td></tr>)}</tbody></table>
            : <p className="muted">Every detector is at its default (z = 3.5). Dismissing an anomaly raises its metric’s threshold; marking one useful lowers it.</p>}
          {l.data && <p className="fine num">{l.data.automations_enabled} automations on, saving about {l.data.minutes_saved_per_week.toFixed(0)} minutes a week.</p>}
        </section>
      </div>
    </>
  );
}
