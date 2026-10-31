import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowUp } from "lucide-react";
import { api, type Briefing, type Conversation, type Turn } from "./api";
import { Header, Md, Skeleton, Steps, timeAgo, useAsync } from "./ui";

export type AiInfo = { enabled: boolean; model: string; capabilities: string[] };

const SUGGESTIONS = [
  "Why did orders in the North region drop, and since when?",
  "Which support step should we automate first, and what would it save?",
  "Can I trust these numbers? Check for duplicates and gaps.",
  "Draft a five-line update for the leadership meeting.",
];

export function Ask({ ai, notify, initial }: { ai: AiInfo | null; notify: (m: string) => void; initial?: string }) {
  const list = useAsync(api.conversations);
  const [conv, setConv] = useState<Conversation | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const sentInitial = useRef(false);
  const live = !!ai?.enabled;

  useEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [conv?.turns.length, pending]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || pending) return;
    setDraft(""); setPending(message);
    try {
      if (live) {
        const c = await api.chat(message, conv?.id);
        setConv(c); list.reload();
      } else {
        // Without an API key the platform still answers from its ranked findings (single turn, no tools).
        const a = await api.ask(message);
        const turns: Turn[] = [...(conv?.turns ?? []), { role: "user", text: message }, { role: "assistant", text: a.text, engine: a.engine, steps: a.steps }];
        setConv({ id: 0, title: message, turns, created_at: new Date().toISOString() });
      }
    } catch (e) {
      notify(String((e as Error).message)); setDraft(message);
    } finally { setPending(null); }
  };

  useEffect(() => {
    if (initial && ai && !sentInitial.current) { sentInitial.current = true; send(initial); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial, ai]);

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(draft); }
  };

  const turns = conv?.turns ?? [];
  return (
    <div className="ask">
      <aside className="conv-list" aria-label="Conversations">
        <button className="ghost new-chat" onClick={() => setConv(null)}>New conversation</button>
        {live && list.data?.length === 0 && <p className="fine">Past conversations appear here.</p>}
        {live && list.data?.map((c) => (
          <button key={c.id} className="conv" aria-current={conv?.id === c.id ? "true" : undefined} onClick={async () => setConv(await api.conversation(c.id))}>
            <span className="truncate">{c.title}</span><span className="conv-time">{timeAgo(c.created_at)}</span>
          </button>
        ))}
        {!live && <p className="fine">Conversations are kept once Claude is connected.</p>}
      </aside>
      <section className="chat">
        <div className="chat-col">
          <Header title="Ask" sub={live
            ? "Questions go to a Claude agent that runs aggregate queries on your data, reads the findings and process maps, and remembers what you tell it."
            : "Claude is not connected, so answers come from the ranked findings only. Set ANTHROPIC_API_KEY on the server for the full agent."} />
        </div>
        <div className="thread" aria-live="polite">
          <div className="chat-col">
            {turns.length === 0 && !pending && (
              <div className="starter">
                <p className="muted">Every answer is computed from your own data and shows the queries behind it. Raw rows never leave the server.</p>
                <ul className="suggestions">
                  {SUGGESTIONS.map((s) => <li key={s}><button className="suggestion" onClick={() => send(s)}>{s}</button></li>)}
                </ul>
              </div>
            )}
            {turns.map((t, i) => <Message key={i} turn={t} />)}
            {pending && <>
              <Message turn={{ role: "user", text: pending }} />
              <div className="msg assistant" aria-busy="true"><span className="who">Distill</span><span className="thinking">Querying your data…</span><Skeleton lines={3} /></div>
            </>}
            <div ref={endRef} />
          </div>
        </div>
        <div className="chat-col">
          <form className="composer" onSubmit={(e: FormEvent) => { e.preventDefault(); send(draft); }}>
            <label htmlFor="composer-input" className="sr-only">Message</label>
            <textarea id="composer-input" rows={1} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey}
              placeholder={conv ? "Ask a follow-up" : "Ask about revenue, a process, or data quality"} />
            <button className="send" aria-label="Send" disabled={!draft.trim() || !!pending}><ArrowUp size={16} /></button>
          </form>
        </div>
      </section>
    </div>
  );
}

function Message({ turn }: { turn: Turn }) {
  if (turn.role === "user") return <div className="msg user"><p>{turn.text}</p></div>;
  return (
    <div className="msg assistant">
      <span className="who">Distill <span className="engine">{turn.engine}</span></span>
      <Md>{turn.text}</Md>
      <Steps steps={turn.steps} />
    </div>
  );
}

/** The agent reviews everything, investigates what matters, drafts automations and writes the briefing. */
export function AgentBriefing({ ai, notify, onDone }: { ai: AiInfo | null; notify: (m: string) => void; onDone: () => void }) {
  const briefings = useAsync(api.briefings);
  const [running, setRunning] = useState(false);
  const latest: Briefing | undefined = briefings.data?.[0];
  return (
    <section className="briefing" aria-labelledby="h-briefing">
      <h2 id="h-briefing">Agent review</h2>
      {running && <><p className="muted small">The agent is reading every finding and checking the numbers…</p><Skeleton lines={5} /></>}
      {!running && latest && <>
        <p className="fine">Written {timeAgo(latest.created_at)} by {latest.engine}</p>
        <Md>{latest.text}</Md>
        <Steps steps={latest.steps} />
      </>}
      {!running && !latest && (
        <p className="muted small">{ai?.enabled
          ? "The agent reviews every finding, investigates the ones worth money, drafts automations for you to approve, and writes up what it found."
          : <>Connect Claude by setting <code>ANTHROPIC_API_KEY</code> on the server. Everything else works without it.</>}</p>
      )}
      <button className={latest ? "ghost" : "primary"} disabled={!ai?.enabled || running} onClick={async () => {
        setRunning(true);
        try { await api.autopilot(); briefings.reload(); onDone(); notify("Agent review finished"); }
        catch (e) { notify(String((e as Error).message)); } finally { setRunning(false); }
      }}>{running ? "Reviewing…" : latest ? "Review again" : "Run agent review"}</button>
    </section>
  );
}
