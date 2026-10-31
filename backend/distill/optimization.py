"""Optimization: ranks improvement opportunities by measurable impact and simulates the result.

Every opportunity is expressed in hours per week, labelled as either staff time (manual work a bot
can take over) or waiting time (hours cases sit idle), plus revenue at risk where the finding is
commercial. A simple what-if
model of the process re-computes the average case duration if the selected fixes are applied.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Automation, Dataset, Insight
from .ingest import load_frame


def _weeks(df: pd.DataFrame, time: str) -> float:
    t = pd.to_datetime(df[time], utc=True, format="mixed", errors="coerce")
    return max((t.max() - t.min()).days / 7, 1.0)


def opportunities(session: Session) -> dict[str, Any]:
    from .service import process_model

    automated = {a.insight_id for a in session.scalars(select(Automation)) if a.insight_id}
    out: list[dict[str, Any]] = []
    simulations: list[dict[str, Any]] = []
    for ds in session.scalars(select(Dataset)):
        roles = ds.profile.get("roles", {})
        insights = {(i.kind, i.title): i for i in session.scalars(select(Insight).where(Insight.dataset_id == ds.id))}
        by_kind = lambda k: [i for (kk, _), i in insights.items() if kk == k and i.status != "dismissed"]  # noqa: E731
        if ds.profile.get("role") == "EventLog":
            model = process_model(session, ds)
            if not model.get("cases"):
                continue
            weeks = _weeks(load_frame(session, ds.id), roles["time"])
            cases_pw = model["cases"] / weeks
            levers = []
            for i in by_kind("automation_candidate"):
                a = i.evidence
                minutes = (a.get("median_hours_since_previous") or 0) * 60
                hours = a["frequency"] / weeks * minutes / 60
                levers.append({"insight": i, "kind": "automate_step", "hours_per_week": hours,
                               "case_hours_saved": 0.0, "detail": f"{a['frequency'] / weeks:.0f} manual runs/week × {minutes:.0f} min"})
            med_edge = pd.Series([e["mean_hours"] for e in model["edges"]]).median()
            for i in by_kind("bottleneck"):
                b = i.evidence
                saved_per_pass = max(b["mean_hours"] - med_edge, 0)
                share = b["frequency"] / model["cases"]
                levers.append({"insight": i, "kind": "remove_wait", "hours_per_week": saved_per_pass * b["frequency"] / weeks,
                               "case_hours_saved": saved_per_pass * share,
                               "detail": f"cut wait from {b['mean_hours']:.1f}h to a typical {med_edge:.1f}h on {share:.0%} of cases"})
            for i in by_kind("rework"):
                loop = next((e for e in model["edges"] if e["source"] == i.evidence["activity"]), None)
                loop_h = loop["mean_hours"] if loop else 0
                levers.append({"insight": i, "kind": "remove_loop", "hours_per_week": loop_h * i.evidence["case_share"] * cases_pw * 0.5,
                               "case_hours_saved": loop_h * i.evidence["case_share"] * 0.5,
                               "detail": "halve repeat loops with a complete intake form"})
            for lv in levers:
                i = lv.pop("insight")
                out.append({"insight_id": i.id, "dataset": ds.stream, "title": i.title, **lv,
                            "hours_per_week": round(lv["hours_per_week"], 1), "case_hours_saved": round(lv["case_hours_saved"], 2),
                            "automated": i.id in automated})
            base = model["case_duration_hours"]["mean"]
            saved = sum(o["case_hours_saved"] for o in out if o["dataset"] == ds.stream)
            simulations.append({"dataset": ds.stream, "current_case_hours": round(base, 1),
                                "projected_case_hours": round(max(base - saved, 0), 1),
                                "reduction_pct": round(saved / base * 100, 1) if base else 0})
        elif roles.get("money"):
            for i in by_kind("segment_driver"):
                e = i.evidence
                if e["pct_change"] >= 0:
                    continue
                prior = next((b["prior"] for b in e["bars"] if b["label"] == e["segment"]), 0)
                recent = next((b["recent"] for b in e["bars"] if b["label"] == e["segment"]), 0)
                if e.get("metric") == "count":
                    df = load_frame(session, ds.id)
                    aov = pd.to_numeric(df[roles["money"]], errors="coerce").mean()
                    at_risk = (prior - recent) * aov / 2
                else:
                    at_risk = (prior - recent) / 2
                out.append({"insight_id": i.id, "dataset": ds.stream, "title": i.title, "kind": "recover_revenue",
                            "hours_per_week": 0.0, "case_hours_saved": 0.0, "revenue_at_risk_per_week": round(float(at_risk), 0),
                            "detail": f"{e['dimension']}={e['segment']} is {e['pct_change']:+.0f}% vs peers", "automated": i.id in automated})
            for i in by_kind("concentration"):
                df = load_frame(session, ds.id)
                weekly = pd.to_numeric(df[roles["money"]], errors="coerce").sum() / _weeks(df, roles["time"])
                exposure = weekly * i.evidence["top_share"] / 100
                out.append({"insight_id": i.id, "dataset": ds.stream, "title": i.title, "kind": "protect_key_accounts",
                            "hours_per_week": 0.0, "case_hours_saved": 0.0, "revenue_at_risk_per_week": round(float(exposure * 0.1), 0),
                            "detail": f"top accounts carry {exposure:,.0f}/week; a 10% churn among them costs this much",
                            "automated": i.id in automated})
    unit = {"automate_step": "staff", "remove_wait": "wait", "remove_loop": "wait"}
    for o in out:
        o["unit"] = unit.get(o["kind"], "revenue")
    out.sort(key=lambda o: (o.get("revenue_at_risk_per_week", 0) / 100 + o["hours_per_week"]), reverse=True)
    return {"opportunities": out, "simulations": simulations,
            "total_hours_per_week": round(sum(o["hours_per_week"] for o in out), 1),
            "staff_hours_per_week": round(sum(o["hours_per_week"] for o in out if o["unit"] == "staff"), 1),
            "wait_hours_per_week": round(sum(o["hours_per_week"] for o in out if o["unit"] == "wait"), 1),
            "total_revenue_at_risk_per_week": round(sum(o.get("revenue_at_risk_per_week", 0) for o in out), 0)}
