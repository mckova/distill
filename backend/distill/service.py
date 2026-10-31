"""Application services: orchestration between ingest, analysis, learning and automations."""
from __future__ import annotations

import os
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import automations as auto
from .db import Automation, Dataset, Insight, utcnow
from .ingest import load_frame
from .insights import analyze
from .learning import arm, arm_key, rank_score, thresholds
from .process_mining import discover

API_BASE = os.environ.get("DISTILL_PUBLIC_URL", "http://localhost:8000")


def analyze_dataset(session: Session, ds: Dataset) -> list[Insight]:
    df = load_frame(session, ds.id)
    findings = analyze(df, ds.profile, thresholds(session))
    existing = {i.fingerprint: i for i in session.scalars(select(Insight).where(Insight.dataset_id == ds.id))}
    seen: set[str] = set()
    for f in findings:
        fp = f.fingerprint(ds.id)
        seen.add(fp)
        score = rank_score(f.severity, arm(session, arm_key(f.kind)))
        row = existing.get(fp)
        if row is None:
            session.add(Insight(dataset_id=ds.id, fingerprint=fp, kind=f.kind, title=f.title, body=f.body,
                                severity=round(f.severity, 4), score=score, evidence=f.evidence))
        elif row.status != "dismissed":  # refresh numbers, keep human status
            row.title, row.body, row.evidence = f.title, f.body, f.evidence
            row.severity, row.score = round(f.severity, 4), score
    # findings that no longer hold and were never acted on are retired
    for fp, row in existing.items():
        if fp not in seen and row.status == "new":
            has_automation = session.scalar(select(Automation.id).where(Automation.insight_id == row.id))
            if not has_automation:
                session.delete(row)
    session.commit()
    return list(session.scalars(select(Insight).where(Insight.dataset_id == ds.id).order_by(Insight.score.desc())))


def process_model(session: Session, ds: Dataset) -> dict[str, Any]:
    roles = ds.profile.get("roles", {})
    if ds.profile.get("role") != "EventLog":
        return {"cases": 0}
    return discover(load_frame(session, ds.id), roles["case"], roles["activity"], roles["time"], roles.get("resource"))


def _activity_stats(df: pd.DataFrame, roles: dict[str, Any], activity: str) -> dict[str, Any]:
    model = discover(df, roles["case"], roles["activity"], roles["time"], roles.get("resource"))
    act = next((a for a in model["activities"] if a["name"] == activity), {"frequency": 0})
    incoming = sorted((e for e in model["edges"] if e["target"] == activity), key=lambda e: -e["frequency"])
    t = pd.to_datetime(df[roles["time"]], utc=True, format="mixed", errors="coerce")
    weeks = max((t.max() - t.min()).days / 7, 1)
    return {"frequency": act["frequency"], "weeks": weeks, "predecessor": incoming[0]["source"] if incoming else None}


def create_automation(session: Session, insight: Insight) -> Automation | None:
    ds = session.get(Dataset, insight.dataset_id)
    assert ds is not None
    stats: dict[str, Any] = {}
    if insight.kind == "automation_candidate":
        stats = _activity_stats(load_frame(session, ds.id), ds.profile["roles"], insight.evidence["name"])
    spec = auto.compile_insight({"kind": insight.kind, "evidence": insight.evidence}, ds.profile, stats)
    if spec is None:
        return None
    row = Automation(insight_id=insight.id, dataset_id=ds.id, name=spec["name"], kind=spec["kind"],
                     rule=spec["rule"], est_minutes_saved_per_week=spec["minutes"])
    session.add(row)
    session.flush()
    row.n8n_workflow, row.skill_md = auto.build_artifacts(spec, insight.title, API_BASE, str(row.id))
    row.skill_md = _ai_skill(row.skill_md, insight, spec) or row.skill_md
    if insight.status == "new":
        insight.status = "useful"
    session.commit()
    return row


def _ai_skill(template: str, insight: Insight, spec: dict[str, Any]) -> str | None:
    """AI-native skill authoring: Claude turns the template into a domain playbook (template if AI is off)."""
    import anthropic

    from .agent import client_or_none, write_skill

    if client_or_none() is None:
        return None
    context = {"insight": {"title": insight.title, "body": insight.body,
                           "investigation": insight.evidence.get("ai_investigation", {}).get("text")},
               "rule": spec["rule"], "description": spec["description"]}
    try:
        return write_skill(template, context)
    except anthropic.APIError:
        return None


def run_automation(session: Session, a: Automation) -> dict[str, Any]:
    df = load_frame(session, a.dataset_id)
    result = auto.evaluate(a.rule, df, thresholds(session))
    a.runs = [*a.runs[-49:], {"at": utcnow().isoformat(), **{k: result[k] for k in ("triggered", "message")}}]
    session.commit()
    return result


def learning_summary(session: Session) -> dict[str, Any]:
    from .db import LearningArm, Setting

    arms = [{"kind": a.key.split(":", 1)[1], "alpha": a.alpha, "beta": a.beta,
             "posterior_mean": round(a.alpha / (a.alpha + a.beta), 3), "ratings": int(a.alpha + a.beta - 2)}
            for a in session.scalars(select(LearningArm))]
    th = [{"metric": s.key.split(":", 1)[1], "z": s.value["z"]}
          for s in session.scalars(select(Setting).where(Setting.key.like("anomaly:%")))]
    autos = list(session.scalars(select(Automation)))
    return {"arms": sorted(arms, key=lambda a: -a["posterior_mean"]), "thresholds": th,
            "automations_enabled": sum(a.enabled for a in autos),
            "minutes_saved_per_week": round(sum(a.est_minutes_saved_per_week for a in autos if a.enabled), 1)}


def refresh_intelligence(session: Session, datasets: list[Dataset]) -> int:
    """After new data lands: AI glossary (if missing), statistical analysis, then skills regeneration."""
    import anthropic

    from .agent import client_or_none, glossary
    from .skills import generate_skills

    ai = client_or_none() is not None
    found = 0
    for ds in datasets:
        if ai and ds.row_count and not ds.profile.get("glossary"):
            try:
                g = glossary(ds.profile, ds.stream)
            except anthropic.APIError:
                g = None
            if g:
                ds.profile = {**ds.profile, "glossary": g}
                session.commit()
        found += len(analyze_dataset(session, ds))
    generate_skills(session, use_ai=ai)
    return found
