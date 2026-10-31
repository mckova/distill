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
  feedback: (id: number, useful: boolean) => post<Json>(`/api/insights/${id}/feedback`, { useful }),
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
  ai: () => req<{ enabled: boolean; model: string; capabilities: string[] }>("/api/ai"),
  investigate: (id: number) => post<Insight>(`/api/insights/${id}/investigate`),
  url: (path: string) => BASE + path,
};
