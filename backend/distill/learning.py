"""Self-learning loop.

Two feedback channels shape what the platform surfaces next time:

1. *Which kinds of insight this company cares about* - a Beta-Bernoulli
   bandit per insight kind. "Useful" is a success, "dismiss"
   a failure. Ranking uses the posterior mean plus an uncertainty bonus
   (UCB-style) so rarely-rated kinds still get shown and learned.
2. *How sensitive each detector should be* - every anomaly metric keeps its
   own z-threshold. Dismissed alerts raise it (less noise), useful ones lower
   it (catch more), within safe bounds.

Dismissed fingerprints are also suppressed on re-analysis, so the feed
never nags about something a human already rejected.
"""
from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Insight, LearningArm, Setting
from .insights import DEFAULT_Z

Z_MIN, Z_MAX = 2.5, 6.0


def arm(session: Session, key: str) -> LearningArm:
    a = session.scalar(select(LearningArm).where(LearningArm.key == key))
    if a is None:
        a = LearningArm(key=key, alpha=1.0, beta=1.0)
        session.add(a)
        session.flush()
    return a


def arm_key(kind: str) -> str:
    return f"kind:{kind}"


def rank_score(severity: float, a: LearningArm, exploration: float = 0.5) -> float:
    n = a.alpha + a.beta
    mean = a.alpha / n
    std = math.sqrt(a.alpha * a.beta / (n * n * (n + 1)))
    return round(severity * (mean + exploration * std), 4)


def thresholds(session: Session) -> dict[str, float]:
    rows = session.scalars(select(Setting).where(Setting.key.like("anomaly:%"))).all()
    return {r.key: float(r.value["z"]) for r in rows}


def _tune_threshold(session: Session, metric: str, useful: bool) -> float:
    key = f"anomaly:{metric}"
    s = session.get(Setting, key)
    z = float(s.value["z"]) if s else DEFAULT_Z
    z = max(Z_MIN, z - 0.1) if useful else min(Z_MAX, z + 0.25)
    if s:
        s.value = {"z": round(z, 2)}
    else:
        session.add(Setting(key=key, value={"z": round(z, 2)}))
    return z


def record_feedback(session: Session, insight: Insight, useful: bool, reason: str | None = None) -> dict[str, float]:
    a = arm(session, arm_key(insight.kind))
    if useful:
        a.alpha += 1
    else:
        a.beta += 1
    insight.status = "useful" if useful else "dismissed"
    if reason and reason.strip():  # free-text feedback becomes company memory the AI reads on every run
        from .db import Memory

        verdict = "useful" if useful else "not useful"
        session.add(Memory(source="feedback", text=f"User rated '{insight.title}' ({insight.kind}) {verdict}: {reason.strip()[:300]}"))
    out = {"alpha": a.alpha, "beta": a.beta, "posterior_mean": round(a.alpha / (a.alpha + a.beta), 3)}
    if insight.kind == "anomaly":
        out["new_threshold"] = _tune_threshold(session, insight.evidence.get("metric", "count"), useful)
    # re-rank everything of this kind with the updated posterior
    for other in session.scalars(select(Insight).where(Insight.kind == insight.kind)).all():
        other.score = rank_score(other.severity, a)
    session.commit()
    return out
