"""HTTP API (FastAPI). Run: uvicorn distill.api:app --reload"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import agent, narrator, service
from .connectors.sources import REGISTRY, build
from .db import Automation, Briefing, Conversation, Dataset, Insight, Memory, Skill, Source, SyncRun, get_session, init_db
from .ingest import append_records, get_or_create_dataset, load_frame, refresh_profile, sync_source
from .learning import record_feedback

app = FastAPI(title="Distill", version="1.0.0",
              description="Collect company data, mine insights and processes, compile them into automations and agent skills.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def demo_mode() -> bool:
    """DISTILL_DEMO=1: seed the demo company on boot and run the AI features in a labelled offline mode."""
    import os

    return os.environ.get("DISTILL_DEMO") == "1"


@app.on_event("startup")
def _startup() -> None:
    import os

    from . import scheduler

    init_db()
    if demo_mode():
        # A hosted demo starts with the demo company already loaded, so a visitor lands on real findings.
        with get_session() as s:
            if not s.scalar(select(Source)):
                demo(s)
    if os.environ.get("DISTILL_SCHEDULER", "1") != "0":
        scheduler.start()


def db() -> Iterator[Session]:
    s = get_session()
    try:
        yield s
    finally:
        s.close()


def _404(obj: Any, what: str) -> Any:
    if obj is None:
        raise HTTPException(404, f"{what} not found")
    return obj


def _mask(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    props = REGISTRY[kind].spec().get("properties", {})
    out = {}
    for k, v in config.items():
        if props.get(k, {}).get("secret") and v:
            out[k] = "••••"
        elif k == "content" and isinstance(v, str):
            out[k] = f"<{len(v):,} chars>"
        else:
            out[k] = v
    return out


def source_out(s: Source) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "kind": s.kind, "config": _mask(s.kind, s.config), "state": s.state,
            "created_at": s.created_at, "datasets": [{"id": d.id, "stream": d.stream, "rows": d.row_count} for d in s.datasets]}


def dataset_out(d: Dataset) -> dict[str, Any]:
    return {"id": d.id, "source_id": d.source_id, "source": d.source.name, "stream": d.stream, "rows": d.row_count,
            "role": d.profile.get("role"), "roles": d.profile.get("roles", {}), "updated_at": d.updated_at}


def insight_out(i: Insight) -> dict[str, Any]:
    return {"id": i.id, "dataset_id": i.dataset_id, "kind": i.kind, "title": i.title, "body": i.body,
            "severity": i.severity, "score": i.score, "status": i.status, "evidence": i.evidence,
            "created_at": i.created_at}


def automation_out(a: Automation, full: bool = False) -> dict[str, Any]:
    out = {"id": a.id, "insight_id": a.insight_id, "dataset_id": a.dataset_id, "name": a.name, "kind": a.kind,
           "rule": a.rule, "enabled": a.enabled, "est_minutes_saved_per_week": a.est_minutes_saved_per_week,
           "runs": a.runs[-10:], "created_at": a.created_at}
    if full:
        out.update(n8n_workflow=a.n8n_workflow, skill_md=a.skill_md)
    return out


# --------------------------------------------------------------------------- schemas

class SourceIn(BaseModel):
    name: str
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    useful: bool
    reason: str | None = None


class ChatIn(BaseModel):
    message: str
    conversation_id: int | None = None


class MemoryIn(BaseModel):
    text: str


class ToggleIn(BaseModel):
    enabled: bool


class AskIn(BaseModel):
    question: str


# --------------------------------------------------------------------------- sources

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/connectors")
def connectors() -> list[dict[str, Any]]:
    return [{"kind": k, "spec": c.spec()} for k, c in REGISTRY.items()]


@app.get("/api/sources")
def list_sources(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [source_out(x) for x in s.scalars(select(Source).order_by(Source.id))]


@app.post("/api/sources", status_code=201)
def create_source(body: SourceIn, s: Session = Depends(db)) -> dict[str, Any]:
    if body.kind not in REGISTRY:
        raise HTTPException(422, f"unknown connector kind '{body.kind}'")
    src = Source(name=body.name, kind=body.kind, config=body.config, state={})
    s.add(src)
    s.commit()
    return source_out(src)


@app.post("/api/sources/upload", status_code=201)
async def upload_source(name: str = Form(...), stream: str = Form(...), cursor_field: str = Form(""),
                        file: UploadFile = File(...), s: Session = Depends(db)) -> dict[str, Any]:
    content = (await file.read()).decode("utf-8-sig")
    fmt = "json" if (file.filename or "").lower().endswith((".json", ".jsonl")) else "csv"
    src = Source(name=name, kind="file", state={},
                 config={"stream": stream, "format": fmt, "content": content, "cursor_field": cursor_field or None})
    s.add(src)
    s.commit()
    return _sync_and_analyze(s, src)


@app.delete("/api/sources/{source_id}", status_code=204)
def delete_source(source_id: int, s: Session = Depends(db)) -> None:
    src = _404(s.get(Source, source_id), "source")
    for ds in src.datasets:
        for model in (Automation, Skill, Insight):
            for row in s.scalars(select(model).where(model.dataset_id == ds.id)):
                s.delete(row)
    for run in s.scalars(select(SyncRun).where(SyncRun.source_id == source_id)):
        s.delete(run)
    s.delete(src)
    s.commit()


@app.post("/api/sources/{source_id}/check")
def check_source(source_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    src = _404(s.get(Source, source_id), "source")
    ok, msg = build(src.kind, src.config).check()
    return {"ok": ok, "message": msg}


def _sync_and_analyze(s: Session, src: Source) -> dict[str, Any]:
    try:
        run = sync_source(s, src)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"sync failed: {exc}") from exc
    found = service.refresh_intelligence(s, list(src.datasets))
    return {"source": source_out(src), "run": {"id": run.id, "status": run.status, "records": run.records},
            "insights": found}


@app.post("/api/sources/{source_id}/sync")
def sync(source_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    return _sync_and_analyze(s, _404(s.get(Source, source_id), "source"))


@app.post("/api/sources/{source_id}/events", status_code=202)
def push_events(source_id: int, events: list[dict[str, Any]] | dict[str, Any], s: Session = Depends(db)) -> dict[str, Any]:
    """Webhook ingestion: push one event or a batch; the dataset profile refreshes immediately."""
    src = _404(s.get(Source, source_id), "source")
    if src.kind != "webhook":
        raise HTTPException(409, "events can only be pushed to webhook sources")
    batch = events if isinstance(events, list) else [events]
    ds = get_or_create_dataset(s, src, src.config["stream"])
    append_records(s, ds, batch)
    s.flush()
    refresh_profile(s, ds)
    s.commit()
    return {"accepted": len(batch), "dataset_id": ds.id, "rows": ds.row_count}


@app.get("/api/sources/{source_id}/runs")
def runs(source_id: int, s: Session = Depends(db)) -> list[dict[str, Any]]:
    q = select(SyncRun).where(SyncRun.source_id == source_id).order_by(SyncRun.id.desc()).limit(20)
    return [{"id": r.id, "status": r.status, "records": r.records, "log": r.log, "started_at": r.started_at,
             "finished_at": r.finished_at} for r in s.scalars(q)]


@app.post("/api/demo", status_code=201)
def demo(s: Session = Depends(db)) -> dict[str, Any]:
    existing = s.scalar(select(Source).where(Source.kind == "demo"))
    if existing:
        return {"source": source_out(existing), "insights": 0, "existing": True}
    src = Source(name="Orbit Supplies (demo)", kind="demo", config={"seed": 7, "days": 120}, state={})
    s.add(src)
    s.commit()
    return _sync_and_analyze(s, src)


# --------------------------------------------------------------------------- datasets

@app.get("/api/datasets")
def list_datasets(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [dataset_out(d) for d in s.scalars(select(Dataset).order_by(Dataset.id))]


@app.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    d = _404(s.get(Dataset, dataset_id), "dataset")
    sample = load_frame(s, d.id).head(25)
    return {**dataset_out(d), "profile": d.profile,
            "sample": sample.where(sample.notna(), None).to_dict(orient="records")}


@app.get("/api/datasets/{dataset_id}/process")
def get_process(dataset_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    return service.process_model(s, _404(s.get(Dataset, dataset_id), "dataset"))


@app.post("/api/datasets/{dataset_id}/analyze")
def reanalyze(dataset_id: int, s: Session = Depends(db)) -> list[dict[str, Any]]:
    d = _404(s.get(Dataset, dataset_id), "dataset")
    return [insight_out(i) for i in service.analyze_dataset(s, d)]


# --------------------------------------------------------------------------- insights

@app.get("/api/insights")
def list_insights(dataset_id: int | None = None, status: str | None = None, include_dismissed: bool = False,
                  s: Session = Depends(db)) -> list[dict[str, Any]]:
    q = select(Insight).order_by(Insight.score.desc())
    if dataset_id:
        q = q.where(Insight.dataset_id == dataset_id)
    if status:
        q = q.where(Insight.status == status)
    elif not include_dismissed:
        q = q.where(Insight.status != "dismissed")
    return [insight_out(i) for i in s.scalars(q)]


@app.post("/api/insights/{insight_id}/feedback")
def feedback(insight_id: int, body: FeedbackIn, s: Session = Depends(db)) -> dict[str, Any]:
    i = _404(s.get(Insight, insight_id), "insight")
    return {"insight": insight_out(i), "learning": record_feedback(s, i, body.useful, body.reason)}


@app.post("/api/insights/{insight_id}/automate", status_code=201)
def automate(insight_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    i = _404(s.get(Insight, insight_id), "insight")
    a = service.create_automation(s, i)
    if a is None:
        raise HTTPException(422, f"insights of kind '{i.kind}' are informational and have no automation template")
    return automation_out(a, full=True)


# --------------------------------------------------------------------------- automations

@app.get("/api/automations")
def list_automations(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [automation_out(a) for a in s.scalars(select(Automation).order_by(Automation.id.desc()))]


@app.get("/api/automations/{automation_id}")
def get_automation(automation_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    return automation_out(_404(s.get(Automation, automation_id), "automation"), full=True)


@app.patch("/api/automations/{automation_id}")
def toggle(automation_id: int, body: ToggleIn, s: Session = Depends(db)) -> dict[str, Any]:
    a = _404(s.get(Automation, automation_id), "automation")
    a.enabled = body.enabled
    s.commit()
    return automation_out(a)


@app.post("/api/automations/{automation_id}/evaluate")
def evaluate(automation_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    return service.run_automation(s, _404(s.get(Automation, automation_id), "automation"))


@app.get("/api/automations/{automation_id}/n8n.json")
def n8n_export(automation_id: int, s: Session = Depends(db)) -> JSONResponse:
    a = _404(s.get(Automation, automation_id), "automation")
    return JSONResponse(a.n8n_workflow, headers={"Content-Disposition": f'attachment; filename="distill-{a.id}.n8n.json"'})


@app.get("/api/automations/{automation_id}/SKILL.md")
def skill_export(automation_id: int, s: Session = Depends(db)) -> PlainTextResponse:
    a = _404(s.get(Automation, automation_id), "automation")
    return PlainTextResponse(a.skill_md, media_type="text/markdown",
                             headers={"Content-Disposition": 'attachment; filename="SKILL.md"'})


@app.delete("/api/automations/{automation_id}", status_code=204)
def delete_automation(automation_id: int, s: Session = Depends(db)) -> None:
    s.delete(_404(s.get(Automation, automation_id), "automation"))
    s.commit()


# --------------------------------------------------------------------------- learning & narrative

@app.get("/api/learning")
def learning(s: Session = Depends(db)) -> dict[str, Any]:
    return service.learning_summary(s)


@app.get("/api/digest")
def get_digest(s: Session = Depends(db)) -> dict[str, Any]:
    return narrator.digest(list_insights(s=s))


@app.post("/api/ask")
def ask(body: AskIn, s: Session = Depends(db)) -> dict[str, Any]:
    return narrator.answer(body.question, list_insights(s=s), session=s)


@app.get("/api/ai")
def ai_status() -> dict[str, Any]:
    return {"enabled": agent.client_or_none() is not None, "demo": demo_mode(), "model": agent.MODEL,
            "capabilities": ["copilot agent with 10 tools", "autopilot daily review", "root-cause investigation",
                             "AI data glossary", "AI-written skills", "company memory", "executive digest"]}


@app.post("/api/insights/{insight_id}/investigate")
def investigate(insight_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    """AI root-cause analysis: the agent drills into the data behind one finding."""
    import anthropic

    i = _404(s.get(Insight, insight_id), "insight")
    if agent.client_or_none() is None:
        raise HTTPException(503, "AI is not configured: set ANTHROPIC_API_KEY")
    try:
        result = agent.investigate(s, i)
    except anthropic.APIError as exc:
        raise HTTPException(502, f"Claude API error: {exc}") from exc
    i.evidence = {**i.evidence, "ai_investigation": {"text": result["text"], "steps": result["steps"],
                                                      "engine": result["engine"]}}
    s.commit()
    return insight_out(i)


# --------------------------------------------------------------------------- copilot (multi-turn agent)

def _ai_or_503() -> None:
    if agent.client_or_none() is None:
        raise HTTPException(503, "AI is not configured: set ANTHROPIC_API_KEY")


def conversation_out(c: Conversation) -> dict[str, Any]:
    return {"id": c.id, "title": c.title, "turns": c.turns, "created_at": c.created_at}


@app.get("/api/conversations")
def list_conversations(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [{"id": c.id, "title": c.title, "created_at": c.created_at}
            for c in s.scalars(select(Conversation).order_by(Conversation.id.desc()).limit(30))]


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    return conversation_out(_404(s.get(Conversation, conversation_id), "conversation"))


@app.post("/api/chat")
def chat(body: ChatIn, s: Session = Depends(db)) -> dict[str, Any]:
    """One copilot turn. The full API transcript is persisted so follow-up questions keep context."""
    import anthropic

    _ai_or_503()
    conv = s.get(Conversation, body.conversation_id) if body.conversation_id else None
    if conv is None:
        conv = Conversation(title=body.message[:120], messages=[], turns=[])
        s.add(conv)
        s.flush()
    try:
        result = agent.run_agent(s, body.message, history=list(conv.messages))
    except anthropic.APIError as exc:
        raise HTTPException(502, f"Claude API error: {exc}") from exc
    conv.messages = result["messages"]
    conv.turns = [*conv.turns, {"role": "user", "text": body.message},
                  {"role": "assistant", "text": result["text"], "steps": result["steps"], "engine": result["engine"]}]
    s.commit()
    return conversation_out(conv)


# --------------------------------------------------------------------------- autopilot

@app.post("/api/autopilot", status_code=201)
def run_autopilot(s: Session = Depends(db)) -> dict[str, Any]:
    """The AI reviews all findings, investigates the most valuable, sets up (disabled) automations, and briefs."""
    import anthropic

    if agent.client_or_none() is None and demo_mode():
        # No key on a hosted demo: a template briefing from the ranked findings, labelled as such.
        b = Briefing(text=narrator.fallback_digest(list_insights(s=s)), steps=[], engine="demo")
        s.add(b)
        s.commit()
        return {"id": b.id, "text": b.text, "steps": b.steps, "engine": b.engine, "created_at": b.created_at}
    _ai_or_503()
    try:
        result = agent.autopilot(s)
    except anthropic.APIError as exc:
        raise HTTPException(502, f"Claude API error: {exc}") from exc
    b = Briefing(text=result["text"], steps=result["steps"], engine=result["engine"])
    s.add(b)
    s.commit()
    return {"id": b.id, "text": b.text, "steps": b.steps, "engine": b.engine, "created_at": b.created_at}


@app.get("/api/briefings")
def briefings(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [{"id": b.id, "text": b.text, "steps": b.steps, "engine": b.engine, "created_at": b.created_at}
            for b in s.scalars(select(Briefing).order_by(Briefing.id.desc()).limit(10))]


# --------------------------------------------------------------------------- skills, optimization, memory

def skill_out(k: Skill, full: bool = False) -> dict[str, Any]:
    out = {"id": k.id, "slug": k.slug, "kind": k.kind, "dataset_id": k.dataset_id, "description": k.description,
           "version": k.version, "author": k.author, "updated_at": k.updated_at}
    if full:
        out["content"] = k.content
    return out


@app.get("/api/skills")
def list_skills(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [skill_out(k) for k in s.scalars(select(Skill).order_by(Skill.kind, Skill.slug))]


@app.post("/api/skills/generate")
def regenerate_skills(s: Session = Depends(db)) -> list[dict[str, Any]]:
    from .skills import generate_skills

    return [skill_out(k) for k in generate_skills(s)]


@app.get("/api/skills/export.zip")
def export_skills(s: Session = Depends(db)) -> Response:
    from .skills import bundle

    data = bundle(list(s.scalars(select(Skill))))
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="distill-skills.zip"'})


@app.get("/api/skills/{skill_id}")
def get_skill(skill_id: int, s: Session = Depends(db)) -> dict[str, Any]:
    return skill_out(_404(s.get(Skill, skill_id), "skill"), full=True)


@app.get("/api/optimization")
def optimization(s: Session = Depends(db)) -> dict[str, Any]:
    from .optimization import opportunities

    return opportunities(s)


@app.get("/api/memory")
def list_memory(s: Session = Depends(db)) -> list[dict[str, Any]]:
    return [{"id": m.id, "text": m.text, "source": m.source, "created_at": m.created_at}
            for m in s.scalars(select(Memory).order_by(Memory.id.desc()))]


@app.post("/api/memory", status_code=201)
def add_memory(body: MemoryIn, s: Session = Depends(db)) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(422, "empty note")
    m = Memory(text=body.text.strip()[:500], source="user")
    s.add(m)
    s.commit()
    return {"id": m.id, "text": m.text, "source": m.source, "created_at": m.created_at}


@app.delete("/api/memory/{memory_id}", status_code=204)
def delete_memory(memory_id: int, s: Session = Depends(db)) -> None:
    s.delete(_404(s.get(Memory, memory_id), "memory"))
    s.commit()


# --------------------------------------------------------------------------- static frontend

_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        target = (_DIST / path).resolve()
        inside = target.is_relative_to(_DIST)
        return FileResponse(target if path and inside and target.is_file() else _DIST / "index.html")
