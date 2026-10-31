export type Json = Record<string, any>;

export interface Dataset {
  id: number; source_id: number; source: string; stream: string; rows: number;
  role: string; roles: Record<string, string | null>; updated_at: string;
}
export interface Insight {
  id: number; dataset_id: number; kind: string; title: string; body: string;
  severity: number; score: number; status: "new" | "useful" | "dismissed"; evidence: Json; created_at: string;
}
export interface Automation {
  id: number; insight_id: number | null; dataset_id: number; name: string; kind: string; rule: Json;
  enabled: boolean; est_minutes_saved_per_week: number; runs: { at: string; triggered: boolean; message: string }[];
  n8n_workflow?: Json; skill_md?: string;
}
export interface Source { id: number; name: string; kind: string; config: Json; state: Json; datasets: { id: number; stream: string; rows: number }[] }
export interface Learning {
  arms: { kind: string; alpha: number; beta: number; posterior_mean: number; ratings: number }[];
  thresholds: { metric: string; z: number }[]; automations_enabled: number; minutes_saved_per_week: number;
}

export interface AgentStep { tool: string; input: Json; ok: boolean; summary: string }
export interface AgentAnswer { text: string; engine: string; steps?: AgentStep[] }
export interface Turn { role: "user" | "assistant"; text: string; steps?: AgentStep[]; engine?: string }
export interface Conversation { id: number; title: string; turns: Turn[]; created_at: string }
export interface Briefing { id: number; text: string; steps: AgentStep[]; engine: string; created_at: string }
export interface Skill { id: number; slug: string; kind: "sop" | "data_context" | "automation"; dataset_id: number | null;
  description: string; version: number; author: string; updated_at: string; content?: string }
export interface Opportunity { insight_id: number; dataset: string; title: string; kind: string; hours_per_week: number;
  case_hours_saved: number; revenue_at_risk_per_week?: number; detail: string; automated: boolean; unit: "staff" | "wait" | "revenue" }
export interface Optimization {
  opportunities: Opportunity[]; total_hours_per_week: number; staff_hours_per_week: number; wait_hours_per_week: number; total_revenue_at_risk_per_week: number;
  simulations: { dataset: string; current_case_hours: number; projected_case_hours: number; reduction_pct: number }[];
}
export interface MemoryNote { id: number; text: string; source: "feedback" | "agent" | "user"; created_at: string }

const BASE = import.meta.env.VITE_API_URL ?? "";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const post = <T>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) });

export const api = {
  datasets: () => req<Dataset[]>("/api/datasets"),
  dataset: (id: number) => req<Json>(`/api/datasets/${id}`),
  process: (id: number) => req<Json>(`/api/datasets/${id}/process`),
  analyze: (id: number) => post<Insight[]>(`/api/datasets/${id}/analyze`),
  insights: (includeDismissed = false) => req<Insight[]>(`/api/insights?include_dismissed=${includeDismissed}`),
  feedback: (id: number, useful: boolean, reason?: string) => post<Json>(`/api/insights/${id}/feedback`, { useful, reason: reason || null }),
  automate: (id: number) => post<Automation>(`/api/insights/${id}/automate`),
  automations: () => req<Automation[]>("/api/automations"),
  automation: (id: number) => req<Automation>(`/api/automations/${id}`),
  toggle: (id: number, enabled: boolean) =>
    req<Automation>(`/api/automations/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  evaluate: (id: number) => post<{ triggered: boolean; message: string; details: Json }>(`/api/automations/${id}/evaluate`),
  removeAutomation: (id: number) => req<void>(`/api/automations/${id}`, { method: "DELETE" }),
  sources: () => req<Source[]>("/api/sources"),
  connectors: () => req<{ kind: string; spec: Json }[]>("/api/connectors"),
  createSource: (body: { name: string; kind: string; config: Json }) => post<Source>("/api/sources", body),
  sync: (id: number) => post<Json>(`/api/sources/${id}/sync`),
  check: (id: number) => post<{ ok: boolean; message: string }>(`/api/sources/${id}/check`),
  removeSource: (id: number) => req<void>(`/api/sources/${id}`, { method: "DELETE" }),
  upload: (form: FormData) => post<Json>("/api/sources/upload", form),
  demo: () => post<Json>("/api/demo"),
  learning: () => req<Learning>("/api/learning"),
  digest: () => req<{ text: string; engine: string }>("/api/digest"),
  ask: (question: string) => post<AgentAnswer>("/api/ask", { question }),
  ai: () => req<{ enabled: boolean; demo?: boolean; model: string; capabilities: string[] }>("/api/ai"),
  investigate: (id: number) => post<Insight>(`/api/insights/${id}/investigate`),
  conversations: () => req<{ id: number; title: string; created_at: string }[]>("/api/conversations"),
  conversation: (id: number) => req<Conversation>(`/api/conversations/${id}`),
  chat: (message: string, conversation_id?: number) => post<Conversation>("/api/chat", { message, conversation_id: conversation_id ?? null }),
  autopilot: () => post<Briefing>("/api/autopilot"),
  briefings: () => req<Briefing[]>("/api/briefings"),
  skills: () => req<Skill[]>("/api/skills"),
  skill: (id: number) => req<Skill>(`/api/skills/${id}`),
  generateSkills: () => post<Skill[]>("/api/skills/generate"),
  optimization: () => req<Optimization>("/api/optimization"),
  memory: () => req<MemoryNote[]>("/api/memory"),
  remember: (text: string) => post<MemoryNote>("/api/memory", { text }),
  forget: (id: number) => req<void>(`/api/memory/${id}`, { method: "DELETE" }),
  url: (path: string) => BASE + path,
};
