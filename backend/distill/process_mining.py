"""Process discovery on event logs (case_id, activity, timestamp[, resource]).

Builds the directly-follows graph (DFG): sort events per case, count each
(a -> b) hand-off, and aggregate the time between them. On top of the DFG:
variant analysis, rework detection, bottleneck ranking and an *automation
potential* score per activity (frequent, short, rule-like, done by a person).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any

import pandas as pd

NON_HUMAN = {"system", "bot", "customer", "auto", "api"}


def _traces(df: pd.DataFrame, case: str, activity: str, time: str, resource: str | None):
    frame = df[[c for c in (case, activity, time, resource) if c]].copy()
    frame[time] = pd.to_datetime(frame[time], utc=True, format="mixed", errors="coerce")
    frame = frame.dropna(subset=[time]).sort_values([case, time], kind="stable")
    for cid, g in frame.groupby(case, sort=False):
        yield cid, list(g[activity]), list(g[time]), (list(g[resource]) if resource else [None] * len(g))


def discover(df: pd.DataFrame, case: str, activity: str, time: str, resource: str | None = None,
             top_variants: int = 8) -> dict[str, Any]:
    dfg_freq: Counter[tuple[str, str]] = Counter()
    dfg_wait: dict[tuple[str, str], list[float]] = defaultdict(list)
    act_freq: Counter[str] = Counter()
    act_cases: Counter[str] = Counter()
    act_service: dict[str, list[float]] = defaultdict(list)
    act_resources: dict[str, Counter[str]] = defaultdict(Counter)
    starts: Counter[str] = Counter()
    ends: Counter[str] = Counter()
    variants: Counter[tuple[str, ...]] = Counter()
    durations: list[float] = []
    rework_cases: Counter[str] = Counter()
    n_cases = 0

    for _cid, acts, times, res in _traces(df, case, activity, time, resource):
        n_cases += 1
        variants[tuple(acts)] += 1
        starts[acts[0]] += 1
        ends[acts[-1]] += 1
        durations.append((times[-1] - times[0]).total_seconds() / 3600)
        for a in set(acts):
            act_cases[a] += 1
        for a, c in Counter(acts).items():
            if c > 1:
                rework_cases[a] += 1
        for i, a in enumerate(acts):
            act_freq[a] += 1
            if res[i] is not None:
                act_resources[a][str(res[i])] += 1
            if i:
                gap = (times[i] - times[i - 1]).total_seconds() / 3600
                edge = (acts[i - 1], a)
                dfg_freq[edge] += 1
                dfg_wait[edge].append(gap)
                act_service[a].append(gap)

    if not n_cases:
        return {"cases": 0}

    edges = []
    out_totals: Counter[str] = Counter()
    for (a, _b), f in dfg_freq.items():
        out_totals[a] += f
    for (a, b), f in dfg_freq.most_common():
        waits = dfg_wait[(a, b)]
        edges.append({
            "source": a, "target": b, "frequency": f,
            "probability": round(f / out_totals[a], 4),
            "mean_hours": round(mean(waits), 3), "median_hours": round(median(waits), 3),
            "total_hours": round(sum(waits), 1),
        })

    activities = []
    for a, f in act_freq.most_common():
        svc = act_service.get(a, [])
        res = act_resources.get(a, Counter())
        human_share = (sum(c for r, c in res.items() if r.lower() not in NON_HUMAN) / sum(res.values())) if res else 0.0
        activities.append({
            "name": a, "frequency": f, "case_coverage": round(act_cases[a] / n_cases, 4),
            "median_hours_since_previous": round(median(svc), 3) if svc else None,
            "rework_case_share": round(rework_cases[a] / n_cases, 4),
            "human_share": round(human_share, 3), "resources": dict(res.most_common(6)),
        })

    for act in activities:
        act["automation_potential"] = automation_potential(act, edges)

    med_edge = median([e["mean_hours"] for e in edges]) if edges else 0
    bottlenecks = sorted(
        (dict(e, slowdown=round(e["mean_hours"] / med_edge, 2) if med_edge else None)
         for e in edges if e["frequency"] >= 0.05 * n_cases),
        key=lambda e: e["total_hours"], reverse=True)[:5]

    return {
        "cases": n_cases,
        "events": int(sum(act_freq.values())),
        "activities": activities,
        "edges": edges,
        "start_activities": dict(starts),
        "end_activities": dict(ends),
        "variants": [{"path": list(v), "cases": c, "share": round(c / n_cases, 4)}
                     for v, c in variants.most_common(top_variants)],
        "variant_count": len(variants),
        "case_duration_hours": {"mean": round(mean(durations), 2), "median": round(median(durations), 2),
                                "p90": round(float(pd.Series(durations).quantile(0.9)), 2)},
        "bottlenecks": bottlenecks,
        "rework": [{"activity": a, "case_share": round(c / n_cases, 4)}
                   for a, c in rework_cases.most_common() if c / n_cases >= 0.05],
    }


def automation_potential(act: dict[str, Any], edges: list[dict[str, Any]]) -> float:
    """0..1. High when the step is frequent, quick, person-performed and deterministically triggered."""
    incoming = [e for e in edges if e["target"] == act["name"]]
    determinism = max((e["probability"] for e in incoming), default=0.0)
    svc = act["median_hours_since_previous"]
    quickness = 0.0 if svc is None else max(0.0, 1 - svc / 1.0)  # under an hour scores > 0
    # quickness gates the score: a step that takes hours of human judgement is not bot work
    score = (0.45 * act["case_coverage"] + 0.35 * act["human_share"] + 0.2 * determinism) * (0.3 + 0.7 * quickness)
    if act["human_share"] < 0.5:  # already automated / done by the customer
        score *= 0.3
    return round(min(score, 1.0), 3)
