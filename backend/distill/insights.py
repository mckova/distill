"""Insight engine: role-specific templates that turn a profiled dataset into ranked findings.

Statistics: seasonality-adjusted robust z-scores for anomalies, contribution
analysis for "what drove the change" (Welch-tested against the median peer),
Pareto concentration and Spearman correlation. Each finding carries
chart-ready evidence so the UI and the automation generator never re-query raw data.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

from .process_mining import discover

DEFAULT_Z = 3.5
WINDOW = 14
# Welch t needed before a segment change is reported. Value metrics are heavy-tailed (a few big
# orders swing a day), so they need stronger evidence than counts. Calibrated on 20 demo seeds.
SEGMENT_T_MIN = {"count": 2.5, "value": 3.5}


@dataclass
class Finding:
    kind: str
    key: str  # stable identity inside (dataset, kind), used for dedupe + learning
    title: str
    body: str
    severity: float
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.severity = float(max(0.0, min(1.0, self.severity)))

    def fingerprint(self, dataset_id: int) -> str:
        return hashlib.sha1(f"{dataset_id}|{self.kind}|{self.key}".encode()).hexdigest()[:32]


def _fmt_pct(x: float) -> str:
    return f"{x:+.0f}%"


def _daily(df: pd.DataFrame, time: str, value: str | None) -> pd.Series:
    t = pd.to_datetime(df[time], utc=True, format="mixed", errors="coerce")
    frame = pd.DataFrame({"t": t.dt.floor("D"), "v": pd.to_numeric(df[value], errors="coerce") if value else 1.0})
    s = frame.dropna().groupby("t")["v"].sum()
    if s.empty:
        return s
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full, fill_value=0.0)


def _series_payload(s: pd.Series) -> list[dict[str, Any]]:
    return [{"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 2)} for d, v in s.items()]


# --------------------------------------------------------------------------- transaction / time series

def trend(df: pd.DataFrame, roles: dict[str, Any], **_: Any) -> list[Finding]:
    time, money = roles.get("time"), roles.get("money")
    if not time:
        return []
    metric = money or "count"
    s = _daily(df, time, money)
    if len(s) < WINDOW * 2 + 1:
        return []
    recent, prior = s.iloc[-WINDOW:], s.iloc[-2 * WINDOW:-WINDOW]
    if prior.sum() == 0:
        return []
    pct = (recent.sum() / prior.sum() - 1) * 100
    pooled = np.sqrt((recent.var() + prior.var()) / WINDOW) or 1.0
    t_stat = abs(recent.mean() - prior.mean()) / pooled
    if abs(pct) < 5 or t_stat < 2:
        return []
    direction = "up" if pct > 0 else "down"
    return [Finding(
        "trend", f"{metric}:{direction}",
        f"Total {metric} is {direction} {_fmt_pct(pct)} over the last {WINDOW} days",
        f"The last {WINDOW} days sum to {recent.sum():,.0f} vs {prior.sum():,.0f} in the {WINDOW} days before "
        f"(t={t_stat:.1f}).",
        min(1.0, abs(pct) / 40 * min(t_stat / 3, 1)),
        {"metric": metric, "pct_change": round(pct, 2), "t_stat": round(float(t_stat), 2),
         "series": _series_payload(s.iloc[-60:]), "chart": "line"},
    )]


def _welch_t(df: pd.DataFrame, t: pd.Series, value: pd.Series, dim: str, seg: Any,
             recent_mask: pd.Series, prior_mask: pd.Series) -> float:
    """Welch t-statistic of the segment's daily totals, recent window vs prior window."""
    in_seg = df[dim] == seg
    day = t.dt.floor("D")
    a = value[in_seg & recent_mask].groupby(day[in_seg & recent_mask]).sum().reindex(
        pd.date_range(day[recent_mask].min(), day[recent_mask].max(), freq="D"), fill_value=0)
    b = value[in_seg & prior_mask].groupby(day[in_seg & prior_mask]).sum().reindex(
        pd.date_range(day[prior_mask].min(), day[prior_mask].max(), freq="D"), fill_value=0)
    se = np.sqrt(a.var() / max(len(a), 1) + b.var() / max(len(b), 1))
    return float(abs(a.mean() - b.mean()) / se) if se else 0.0


def segment_drivers(df: pd.DataFrame, roles: dict[str, Any], fields: list[dict[str, Any]], **_: Any) -> list[Finding]:
    """Which segment diverges from the rest of the business (contribution analysis, count and value)."""
    time, money = roles.get("time"), roles.get("money")
    if not time:
        return []
    t = pd.to_datetime(df[time], utc=True, format="mixed", errors="coerce")
    end = t.max().floor("D") + pd.Timedelta(days=1)
    recent_mask = t >= end - pd.Timedelta(days=WINDOW)
    prior_mask = (t >= end - pd.Timedelta(days=2 * WINDOW)) & ~recent_mask
    dims = [f["name"] for f in fields if f["semantic_type"] in ("Category", "Location") and 2 <= f["distinct"] <= 25]
    best: dict[str, Finding] = {}
    for metric in ("count", money):
        if metric is None:
            continue
        value = pd.Series(1.0, index=df.index) if metric == "count" else pd.to_numeric(df[metric], errors="coerce")
        total_delta = value[recent_mask].sum() - value[prior_mask].sum()
        for dim in dims:
            table = pd.DataFrame({"recent": value[recent_mask].groupby(df.loc[recent_mask, dim]).sum(),
                                  "prior": value[prior_mask].groupby(df.loc[prior_mask, dim]).sum()}).fillna(0)
            if table.empty or table["prior"].sum() == 0:
                continue
            table["delta"] = table["recent"] - table["prior"]
            for seg, row in table.iterrows():
                if row["prior"] < 0.05 * table["prior"].sum() or row["prior"] == 0:
                    continue
                pct = (row["recent"] / row["prior"] - 1) * 100
                # Compare against the *median* peer, not the pooled rest: a pooled baseline would make
                # every healthy segment look divergent whenever one other segment collapses.
                peers = table.drop(index=seg)
                peers = peers[peers["prior"] > 0]
                others_pct = float(((peers["recent"] / peers["prior"] - 1) * 100).median()) if len(peers) else 0.0
                divergence = pct - others_pct
                if abs(pct) < 15 or abs(divergence) < 20:
                    continue
                if _welch_t(df, t, value, dim, seg, recent_mask, prior_mask) < SEGMENT_T_MIN["count" if metric == "count" else "value"]:
                    continue
                share = row["delta"] / total_delta * 100 if total_delta else 0
                label = "orders" if metric == "count" else metric
                f = Finding(
                    "segment_driver", f"{dim}={seg}",
                    f"{dim} '{seg}': {label} {_fmt_pct(pct)} while a typical {dim} moved {_fmt_pct(others_pct)}",
                    f"Over the last {WINDOW} days '{seg}' went from {row['prior']:,.0f} to {row['recent']:,.0f} {label}"
                    + (f"; it explains {share:.0f}% of the overall change." if abs(share) <= 300 else "."),
                    min(1.0, abs(divergence) / 80),
                    {"dimension": dim, "segment": str(seg), "metric": metric, "pct_change": round(float(pct), 2),
                     "others_pct_change": round(float(others_pct), 2),
                     "bars": [{"label": str(k), "recent": round(float(v.recent), 2), "prior": round(float(v.prior), 2)}
                              for k, v in table.sort_values("delta").iterrows()], "chart": "bars"},
                )
                if f.key not in best or f.severity > best[f.key].severity:
                    best[f.key] = f
    return sorted(best.values(), key=lambda f: -f.severity)[:4]


def anomalies(df: pd.DataFrame, roles: dict[str, Any], thresholds: dict[str, float], **_: Any) -> list[Finding]:
    """Robust z-score on day-of-week-normalised daily volume. Threshold is learned per metric.

    Count and value anomalies on the same day are merged into one finding (they share a root cause)."""
    time = roles.get("time")
    if not time:
        return []
    by_day: dict[Any, Finding] = {}
    for metric, col in (("count", None), (roles.get("money"), roles.get("money"))):
        if metric is None:
            continue
        s = _daily(df, time, col)
        if len(s) < 21:
            continue
        dow_med = s.groupby(s.index.dayofweek).transform("median").replace(0, np.nan)
        ratio = (s / dow_med).fillna(1.0)
        med = ratio.median()
        mad = (ratio - med).abs().median() * 1.4826 or 1e-9
        z = (ratio - med) / mad
        thr = thresholds.get(f"anomaly:{metric}", DEFAULT_Z)
        for day, zv in z[z.abs() >= thr].items():
            label = "orders" if metric == "count" else metric
            if day in by_day:  # same day already flagged by another metric: enrich, don't duplicate
                f = by_day[day]
                f.title += f"; {label} {s[day]:,.0f} ({ratio[day]:.1f}x)"
                f.evidence["also"] = {"metric": metric, "z": round(float(zv), 2)}
                continue
            by_day[day] = Finding(
                "anomaly", f"{metric}:{day.date()}",
                f"Unusual {day:%a %d %b %Y}: {label} {s[day]:,.0f} ({ratio[day]:.1f}x a normal {day:%A})",
                f"Robust z-score {zv:.1f} against the typical {day:%A} level (alert threshold {thr:.1f}). "
                f"Check promotions, outages or data-entry issues on that date.",
                float(0.5 + 0.5 * np.tanh((abs(zv) - thr) / (2 * thr))),
                {"metric": metric, "date": str(day.date()), "z": round(float(zv), 2), "threshold": thr,
                 "series": _series_payload(s), "highlight": str(day.date()), "chart": "line"},
            )
    return list(by_day.values())


def concentration(df: pd.DataFrame, roles: dict[str, Any], fields: list[dict[str, Any]], **_: Any) -> list[Finding]:
    money = roles.get("money")
    if not money:
        return []
    value = pd.to_numeric(df[money], errors="coerce")
    findings = []
    for f in fields:
        # an entity key: an ID-ish column that repeats (customer_id, sku, account...)
        if f["semantic_type"] not in ("ID", "Text") or not (0.001 < f["cardinality_ratio"] < 0.5) or f["distinct"] < 20:
            continue
        per = value.groupby(df[f["name"]]).sum().sort_values(ascending=False)
        top_n = max(1, int(np.ceil(len(per) * 0.1)))
        share = per.iloc[:top_n].sum() / per.sum() * 100
        if share < 40:
            continue
        cum = (per.cumsum() / per.sum() * 100).reset_index(drop=True)
        curve = [{"pct_entities": round((i + 1) / len(per) * 100, 1), "pct_value": round(float(v), 1)}
                 for i, v in cum.iloc[:: max(1, len(per) // 50)].items()]
        findings.append(Finding(
            "concentration", f["name"],
            f"Top 10% of {f['name']} generate {share:.0f}% of {money}",
            f"{top_n} of {len(per)} {f['name']} values account for {share:.0f}% of total {money}. "
            f"Losing a few of them is a material risk; they are also the best candidates for retention automation.",
            min(1.0, (share - 30) / 50),
            {"entity": f["name"], "top_share": round(float(share), 2), "top_n": top_n,
             "top": [{"label": str(k), "value": round(float(v), 2)} for k, v in per.head(10).items()],
             "curve": curve, "chart": "pareto"},
        ))
    return findings


def correlations(df: pd.DataFrame, fields: list[dict[str, Any]], **_: Any) -> list[Finding]:
    nums = [f["name"] for f in fields if f["kind"] == "number" and f["semantic_type"] not in ("ID",) and f["distinct"] > 5]
    if len(nums) < 2:
        return []
    corr = df[nums].apply(pd.to_numeric, errors="coerce").corr(method="spearman")
    findings = []
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            r = corr.loc[a, b]
            if pd.isna(r) or abs(r) < 0.3:
                continue
            sample = df[[a, b]].apply(pd.to_numeric, errors="coerce").dropna().sample(
                n=min(300, len(df)), random_state=0)
            findings.append(Finding(
                "correlation", f"{a}~{b}",
                f"{a} and {b} move {'together' if r > 0 else 'in opposite directions'} (ρ={r:.2f})",
                f"Spearman rank correlation of {r:.2f} across {len(df):,} rows. Correlation is not causation, "
                f"but it is a good hypothesis to test (e.g. an A/B on {a}).",
                min(1.0, abs(float(r))),
                {"x": a, "y": b, "rho": round(float(r), 3),
                 "points": [{"x": float(x), "y": float(y)} for x, y in sample.itertuples(index=False)],
                 "chart": "scatter"},
            ))
    return findings


# --------------------------------------------------------------------------- event logs

def process_insights(df: pd.DataFrame, roles: dict[str, Any], **_: Any) -> list[Finding]:
    model = discover(df, roles["case"], roles["activity"], roles["time"], roles.get("resource"))
    if not model.get("cases"):
        return []
    findings: list[Finding] = []
    total_case_hours = model["case_duration_hours"]["mean"] * model["cases"]
    for b in model["bottlenecks"][:2]:
        share = b["total_hours"] / total_case_hours * 100 if total_case_hours else 0
        if (b["slowdown"] or 0) < 3:
            continue
        findings.append(Finding(
            "bottleneck", f"{b['source']}->{b['target']}",
            f"Hand-off '{b['source']}' → '{b['target']}' waits {b['mean_hours']:.1f}h on average",
            f"{b['frequency']} cases pass through it; it is {b['slowdown']}x slower than a typical step and "
            f"accounts for {share:.0f}% of all case time. Auto-routing or an SLA alert here shortens every case.",
            min(1.0, share / 40 + 0.2),
            {**b, "share_of_case_time": round(share, 1), "chart": "process"},
        ))
    for r in model["rework"][:2]:
        findings.append(Finding(
            "rework", r["activity"],
            f"'{r['activity']}' repeats in {r['case_share'] * 100:.0f}% of cases",
            "Repeated steps signal missing information up-front. A smarter intake form or an automated "
            "follow-up template usually removes most loops.",
            min(1.0, r["case_share"] * 2),
            {**r, "chart": "process"},
        ))
    for a in sorted(model["activities"], key=lambda a: a["automation_potential"], reverse=True)[:2]:
        if a["automation_potential"] < 0.6:
            continue
        findings.append(Finding(
            "automation_candidate", a["name"],
            f"'{a['name']}' is a strong automation candidate (score {a['automation_potential']:.2f})",
            f"Done manually in {a['case_coverage'] * 100:.0f}% of cases, typically {a['median_hours_since_previous'] * 60:.0f} "
            f"minutes after the previous step, by {len(a['resources'])} different people. "
            "Frequent + quick + rule-triggered steps are what bots do best.",
            a["automation_potential"],
            {**a, "chart": "process"},
        ))
    if model["variant_count"] > 0.2 * model["cases"]:
        findings.append(Finding(
            "variability", "variants",
            f"{model['variant_count']} different paths for {model['cases']} cases",
            "High path variability usually means the process is not standardised. The top variant covers only "
            f"{model['variants'][0]['share'] * 100:.0f}% of cases.",
            min(1.0, model["variant_count"] / model["cases"]),
            {"variants": model["variants"], "chart": "variants"},
        ))
    return findings


TEMPLATES: dict[str, list[Callable[..., list[Finding]]]] = {
    "TransactionTable": [trend, segment_drivers, anomalies, concentration, correlations],
    "TimeSeriesTable": [trend, segment_drivers, anomalies, correlations],
    "EventLog": [process_insights],
    "GenericTable": [correlations],
}


def analyze(df: pd.DataFrame, profile: dict[str, Any], thresholds: dict[str, float] | None = None) -> list[Finding]:
    if df.empty:
        return []
    ctx = {"roles": profile.get("roles", {}), "fields": profile.get("fields", []), "thresholds": thresholds or {}}
    out: list[Finding] = []
    for template in TEMPLATES.get(profile.get("role", "GenericTable"), []):
        out.extend(template(df, **ctx))
    return out
