"""Distill's analyst agent: Claude with tools over the company's data.

The statistical engine finds *what* changed; the agent investigates *why* and decides
*what to do*, by calling the same safe primitives the UI uses. Claude never receives
raw tables, only aggregates it explicitly asks for through `query`, capped in size,
and it cannot execute arbitrary code or SQL.

Loop: manual tool-use loop on the Messages API (claude-opus-5-5, adaptive thinking is
on by default for this model, effort tuned per task) with server-side refusal fallbacks.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Automation, Dataset, Insight
from .ingest import load_frame

MODEL = os.environ.get("DISTILL_MODEL", "claude-opus-5-5")
MAX_STEPS = 10
MAX_ROWS = 60

SYSTEM = """You are Distill's senior data analyst, embedded in a company's data platform.

You have tools to list datasets, read their profiles, run aggregate queries, read the
statistically-ranked findings, inspect mined business processes, and turn findings into
automations. Work like an analyst:
- Ground every claim in a number you obtained from a tool in this conversation. Never invent figures.
- When asked "why", form hypotheses and test them with `query` (break the metric down by the
  available dimensions and time) before concluding. Say which hypotheses the data rejected.
- Prefer few, well-chosen queries. Aggregates only; you cannot see raw rows.
- Only create an automation when the user asks for one or when you are investigating a finding
  and the fix is clearly a monitoring rule; automations are created disabled for human review.
- Answer in the language of the user's question. Be concise: lead with the answer, then the
  evidence as short bullets, then one recommended next action."""


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required if required is not None else list(props),
            "additionalProperties": False}


TOOLS: list[dict[str, Any]] = [
    {"name": "list_datasets", "description": "List all datasets with their detected role and row count.",
     "input_schema": _schema({})},
    {"name": "describe_dataset",
     "description": "Columns of a dataset with semantic types, cardinality and value ranges.",
     "input_schema": _schema({"dataset_id": {"type": "integer"}})},
    {"name": "query",
     "description": ("Aggregate a dataset. metric is a numeric column, or null to count rows. Optional group_by "
                     "(max 2 columns), filters (equality / comparison) and time_grain on the dataset's time column. "
                     f"Returns at most {MAX_ROWS} rows sorted by the time bucket, else by value descending."),
     "input_schema": _schema({
         "dataset_id": {"type": "integer"},
         "metric": {"type": ["string", "null"]},
         "agg": {"type": "string", "enum": ["sum", "mean", "median", "count", "min", "max", "nunique"]},
         "group_by": {"type": "array", "items": {"type": "string"}},
         "filters": {"type": "array", "items": _schema({
             "column": {"type": "string"}, "op": {"type": "string", "enum": ["==", "!=", ">", ">=", "<", "<="]},
             "value": {"type": ["string", "number"]}})},
         "time_grain": {"type": ["string", "null"], "enum": ["day", "week", "month", None]},
         "last_days": {"type": ["integer", "null"], "description": "only rows from the final N days of the data"},
     })},
    {"name": "list_insights", "description": "Statistically detected findings, ranked. Optionally filter by kind.",
     "input_schema": _schema({"kind": {"type": ["string", "null"]}, "limit": {"type": "integer"}})},
    {"name": "process_model",
     "description": "Mined process of an event-log dataset: activities, hand-offs with waiting times, bottlenecks, variants.",
     "input_schema": _schema({"dataset_id": {"type": "integer"}})},
    {"name": "create_automation",
     "description": "Compile a finding into a monitoring rule + n8n workflow + agent skill. Created disabled.",
     "input_schema": _schema({"insight_id": {"type": "integer"}})},
    {"name": "run_automation", "description": "Evaluate an automation against current data now.",
     "input_schema": _schema({"automation_id": {"type": "integer"}})},
]
for _t in TOOLS:
    _t["strict"] = True


class ToolError(Exception):
    pass


class Toolbox:
    """Executes tool calls against the database. Every method returns JSON-serialisable data."""

    def __init__(self, session: Session):
        self.s = session

    def _ds(self, dataset_id: int) -> Dataset:
        ds = self.s.get(Dataset, dataset_id)
        if ds is None:
            raise ToolError(f"dataset {dataset_id} does not exist; call list_datasets")
        return ds

    def list_datasets(self) -> list[dict[str, Any]]:
        return [{"dataset_id": d.id, "stream": d.stream, "role": d.profile.get("role"), "rows": d.row_count,
                 "time_column": d.profile.get("roles", {}).get("time")} for d in self.s.scalars(select(Dataset))]

    def describe_dataset(self, dataset_id: int) -> dict[str, Any]:
        ds = self._ds(dataset_id)
        keep = ("name", "semantic_type", "kind", "distinct", "null_ratio", "min", "max", "mean", "top")
        return {"stream": ds.stream, "role": ds.profile.get("role"), "roles": ds.profile.get("roles"),
                "columns": [{k: f[k] for k in keep if k in f} for f in ds.profile.get("fields", [])]}

    def query(self, dataset_id: int, metric: str | None, agg: str, group_by: list[str], filters: list[dict[str, Any]],
              time_grain: str | None, last_days: int | None) -> dict[str, Any]:
        ds = self._ds(dataset_id)
        df = load_frame(self.s, ds.id)
        cols = set(df.columns)
        for c in [*group_by, *(f["column"] for f in filters), *([metric] if metric else [])]:
            if c not in cols:
                raise ToolError(f"unknown column '{c}'. Columns: {sorted(cols)}")
        if len(group_by) > 2:
            raise ToolError("group_by supports at most 2 columns")
        time_col = ds.profile.get("roles", {}).get("time")
        if (time_grain or last_days) and not time_col:
            raise ToolError("this dataset has no time column")
        if time_col:
            df = df.assign(_t=pd.to_datetime(df[time_col], utc=True, format="mixed", errors="coerce"))
        if last_days:
            df = df[df["_t"] > df["_t"].max() - pd.Timedelta(days=last_days)]
        for f in filters:
            col, op, val = df[f["column"]], f["op"], f["value"]
            if isinstance(val, (int, float)):
                col = pd.to_numeric(col, errors="coerce")
            else:
                col = col.astype(str)
            df = df[{"==": col == val, "!=": col != val, ">": col > val, ">=": col >= val, "<": col < val,
                     "<=": col <= val}[op]]
        keys = list(group_by)
        if time_grain:
            freq = {"day": "D", "week": "W-SUN", "month": "MS"}[time_grain]
            df = df.assign(period=df["_t"].dt.tz_localize(None).dt.to_period(freq[0]).dt.start_time.dt.strftime("%Y-%m-%d"))
            keys = ["period", *keys]
        values = pd.to_numeric(df[metric], errors="coerce") if metric else pd.Series(1, index=df.index)
        func = "sum" if (agg == "count" and not metric) else agg
        if agg == "count" and metric:
            func = "count"
        if keys:
            out = values.groupby([df[k] for k in keys]).agg(func).reset_index(name="value")
            out = out.sort_values("period" if time_grain else "value", ascending=bool(time_grain))
        else:
            out = pd.DataFrame([{"value": values.agg(func)}])
        total = len(out)
        out["value"] = out["value"].astype(float).round(3)
        return {"rows_matched": int(len(df)), "groups": total, "truncated": total > MAX_ROWS,
                "result": out.head(MAX_ROWS).to_dict(orient="records")}

    def list_insights(self, kind: str | None, limit: int) -> list[dict[str, Any]]:
        q = select(Insight).where(Insight.status != "dismissed").order_by(Insight.score.desc())
        if kind:
            q = q.where(Insight.kind == kind)
        drop = {"series", "points", "curve", "bars", "variants", "resources"}
        return [{"insight_id": i.id, "dataset_id": i.dataset_id, "kind": i.kind, "title": i.title, "status": i.status,
                 "evidence": {k: v for k, v in i.evidence.items() if k not in drop}}
                for i in self.s.scalars(q.limit(max(1, min(limit, 30))))]

    def process_model(self, dataset_id: int) -> dict[str, Any]:
        from .service import process_model

        m = process_model(self.s, self._ds(dataset_id))
        if not m.get("cases"):
            raise ToolError("not an event log (needs case id, activity and timestamp columns)")
        return {k: m[k] for k in ("cases", "case_duration_hours", "bottlenecks", "rework", "variant_count")} | {
            "activities": [{k: a[k] for k in ("name", "frequency", "case_coverage", "median_hours_since_previous",
                                              "human_share", "automation_potential")} for a in m["activities"]],
            "top_variants": m["variants"][:4]}

    def create_automation(self, insight_id: int) -> dict[str, Any]:
        from .service import create_automation

        ins = self.s.get(Insight, insight_id)
        if ins is None:
            raise ToolError(f"insight {insight_id} not found")
        a = create_automation(self.s, ins)
        if a is None:
            raise ToolError(f"'{ins.kind}' findings are informational and have no automation template")
        return {"automation_id": a.id, "name": a.name, "rule": a.rule, "enabled": a.enabled}

    def run_automation(self, automation_id: int) -> dict[str, Any]:
        from .service import run_automation

        a = self.s.get(Automation, automation_id)
        if a is None:
            raise ToolError(f"automation {automation_id} not found")
        return run_automation(self.s, a)

    def call(self, name: str, args: dict[str, Any]) -> Any:
        fn: Callable[..., Any] | None = getattr(self, name, None) if name in {t["name"] for t in TOOLS} else None
        if fn is None:
            raise ToolError(f"unknown tool {name}")
        return fn(**args)


def client_or_none() -> Any:
    try:
        import anthropic
    except ImportError:
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    return anthropic.Anthropic()


def _create(client: Any, **kwargs: Any) -> Any:
    """Messages call with server-side refusal fallbacks ("default" routing by refusal category)."""
    return client.beta.messages.create(model=MODEL, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                       **kwargs)


def run_agent(session: Session, prompt: str, client: Any | None = None, effort: str = "medium") -> dict[str, Any]:
    """Run the tool loop. Returns the final answer plus a trace of every tool call (shown in the UI)."""
    client = client or client_or_none()
    if client is None:
        return {"text": None, "engine": "unavailable", "steps": []}
    tools = Toolbox(session)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    steps: list[dict[str, Any]] = []
    for _ in range(MAX_STEPS):
        resp = _create(client, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                       output_config={"effort": effort})
        if resp.stop_reason == "refusal":
            return {"text": "The model declined this request.", "engine": MODEL, "steps": steps}
        messages.append({"role": "assistant", "content": resp.content})  # keep blocks verbatim (thinking included)
        calls = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason != "tool_use" or not calls:
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            return {"text": text, "engine": getattr(resp, "model", MODEL), "steps": steps}
        results = []
        for call in calls:
            try:
                out, is_error = tools.call(call.name, dict(call.input)), False
            except ToolError as exc:
                out, is_error = {"error": str(exc)}, True
            except (KeyError, ValueError, TypeError) as exc:
                out, is_error = {"error": f"{type(exc).__name__}: {exc}"}, True
            steps.append({"tool": call.name, "input": call.input, "ok": not is_error,
                          "summary": _summarize(out)})
            results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": is_error,
                            "content": json.dumps(out, default=str, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})  # all results in one message
    return {"text": "Stopped after the maximum number of steps; partial findings are in the trace.",
            "engine": MODEL, "steps": steps}


def _summarize(out: Any) -> str:
    if isinstance(out, dict) and "error" in out:
        return out["error"]
    if isinstance(out, dict) and "result" in out:
        return f"{out['groups']} groups from {out['rows_matched']} rows"
    if isinstance(out, list):
        return f"{len(out)} items"
    return ", ".join(f"{k}" for k in list(out)[:5]) if isinstance(out, dict) else str(out)[:80]


def investigate(session: Session, insight: Insight, client: Any | None = None) -> dict[str, Any]:
    """Root-cause analysis of one finding. The agent drills down with queries and proposes a fix."""
    prompt = (f"Investigate this finding (insight_id={insight.id}, dataset_id={insight.dataset_id}):\n"
              f"\"{insight.title}\"\n{insight.body}\n\n"
              "Find the most likely root cause by breaking the metric down along the other dimensions and time. "
              "Report: the root cause (or the best-supported hypothesis), the evidence, the hypotheses you ruled out, "
              "and the single most valuable action. Do not create automations.")
    return run_agent(session, prompt, client=client, effort="high")


def write_skill(skill_md: str, context: dict[str, Any], client: Any | None = None) -> str | None:
    """Let Claude turn the template SKILL.md into a domain-specific playbook. Front matter is preserved."""
    client = client or client_or_none()
    if client is None:
        return None
    head, _, _ = skill_md.partition("\n# ")
    resp = _create(client, max_tokens=8000, output_config={"effort": "medium"}, system=(
        "You write Agent Skills (SKILL.md) for operations teams. Keep the exact YAML front matter you are given. "
        "Write a precise, step-by-step procedure an AI agent can follow, with decision rules, escalation criteria "
        "and what to log. Use only facts from the context. Output only the markdown file."),
        messages=[{"role": "user", "content": f"Template:\n{skill_md}\n\nContext:\n{json.dumps(context, default=str)}"}])
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text.startswith("---"):
        text = head + "\n" + text
    return text
