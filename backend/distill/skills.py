"""Skill studio: turns what Distill learned about a company into Agent Skills (SKILL.md).

Three kinds:
  * sop           - "how this company actually runs process X", mined from the event log:
                    the happy path, real timings as SLAs, branches, known failure points.
  * data_context  - "how to answer questions about dataset Y": semantics of every column,
                    metric definitions, ready-made queries, caveats and current findings.
  * automation    - the playbook attached to each automation (see automations.py).

A deterministic template is always produced first; when Claude is available it rewrites the
body into a richer playbook (front matter preserved). Skills are versioned: when the data
changes, the content hash changes and the version is bumped, so skills evolve with the company.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .automations import slug
from .db import Automation, Dataset, Insight, Skill, utcnow


def _front(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n"


def _h(hours: float | None) -> str:
    if hours is None:
        return "n/a"
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.1f} h"


def sop_skill(ds: Dataset, model: dict[str, Any]) -> tuple[str, str, str]:
    name = slug(f"run-{ds.stream}-process")
    desc = (f"Standard operating procedure for a {ds.stream} case, mined from {model['cases']} real cases. "
            "Use when handling, reviewing or escalating a case.")
    happy = model["variants"][0]
    edges = {(e["source"], e["target"]): e for e in model["edges"]}
    acts = {a["name"]: a for a in model["activities"]}
    steps = []
    for i, step in enumerate(happy["path"]):
        line = f"{i + 1}. **{step}**"
        if i:
            e = edges.get((happy["path"][i - 1], step))
            if e:
                line += f": typically {_h(e['median_hours'])} after the previous step (mean {_h(e['mean_hours'])})"
        who = acts.get(step, {}).get("resources") or {}
        if who:
            line += f". Done by: {', '.join(list(who)[:3])}"
        steps.append(line)
    branches = [f"- After **{e['source']}**, {e['probability'] * 100:.0f}% of cases go to **{e['target']}**"
                for e in model["edges"] if 0.1 <= e["probability"] < 0.9 and e["frequency"] >= 0.05 * model["cases"]]
    risks = [f"- **{b['source']} → {b['target']}** waits {_h(b['mean_hours'])} on average ({b['slowdown']}x a typical step). "
             f"Flag any case waiting longer than {_h(b['median_hours'])}." for b in model["bottlenecks"][:3]
             if (b.get("slowdown") or 0) >= 2]
    rework = [f"- **{r['activity']}** repeats in {r['case_share'] * 100:.0f}% of cases: collect all required "
              "information up front to avoid the loop." for r in model["rework"]]
    auto = [f"- **{a['name']}** is a good bot candidate (automation potential {a['automation_potential']:.2f})."
            for a in model["activities"] if a["automation_potential"] >= 0.6]
    body = f"""# {ds.stream.replace('_', ' ').title()}: standard procedure

Mined from {model['cases']} cases. Median end-to-end time: {_h(model['case_duration_hours']['median'])}
(90% of cases finish within {_h(model['case_duration_hours']['p90'])}). The standard path below covers
{happy['share'] * 100:.0f}% of cases.

## Standard path
{chr(10).join(steps)}

## Decision points
{chr(10).join(branches) or '- None significant.'}

## Known delays (watch these)
{chr(10).join(risks) or '- No hand-off is unusually slow.'}

## Avoidable loops
{chr(10).join(rework) or '- None detected.'}

## Automation hooks
{chr(10).join(auto) or '- None above threshold.'}

## Done when
The case reaches **{max(model['end_activities'], key=model['end_activities'].get)}** and every step above is logged
with a timestamp, so Distill can keep measuring the process.
"""
    return name, desc, _front(name, desc) + "\n" + body


def data_context_skill(ds: Dataset, insights: list[Insight]) -> tuple[str, str, str]:
    prof, roles = ds.profile, ds.profile.get("roles", {})
    name = slug(f"query-{ds.stream}-data")
    desc = (f"How to answer business questions with the {ds.stream} dataset ({ds.row_count:,} rows): column "
            "meanings, metric definitions, example queries and caveats.")
    meaning = {c["name"]: c["meaning"] for c in (prof.get("glossary") or {}).get("columns", [])}
    cols = "\n".join(
        f"| `{f['name']}` | {meaning.get(f['name'], f['semantic_type'])} | {f['kind']} | {f['distinct']:,} | "
        + (f"{f.get('min'):g} to {f.get('max'):g}" if f["kind"] == "number" else
           (f"{f.get('min', '')[:10]} to {f.get('max', '')[:10]}" if f["kind"] == "datetime" else
            ", ".join(list(f.get("top", {}))[:3])))
        + f" | {f['null_ratio']:.0%} |" for f in prof.get("fields", []))
    dims = [f["name"] for f in prof.get("fields", []) if f["semantic_type"] in ("Category", "Location")]
    metric = roles.get("money")
    metrics = []
    if metric:
        metrics += [f"- **Revenue** = `sum({metric})`", f"- **Average order value** = `mean({metric})`"]
    metrics.append("- **Volume** = count of rows")
    if roles.get("case"):
        metrics += [f"- **Cases** = distinct `{roles['case']}`",
                    f"- **Cycle time** = last minus first `{roles['time']}` per `{roles['case']}`"]
    queries = []
    if roles.get("time"):
        queries.append(f"- Weekly trend: metric by `time_grain=week` on `{roles['time']}`")
    for d in dims[:3]:
        queries.append(f"- Breakdown: metric grouped by `{d}`, compare `last_days=14` vs `last_days=28`")
    caveats = [f"- {i.title}" for i in insights if i.kind == "data_quality"]
    caveats += [f"- `{f['name']}` is empty in {f['null_ratio']:.0%} of rows" for f in prof.get("fields", [])
                if 0.05 <= f["null_ratio"]]
    findings = [f"- {i.title}" for i in insights if i.kind != "data_quality" and i.status != "dismissed"][:8]
    body = f"""# Working with `{ds.stream}`

{(prof.get('glossary') or {}).get('summary', '')}

Table role: **{prof.get('role')}**. Time column: `{roles.get('time')}`.

## Columns
| Column | Meaning | Type | Distinct | Range / top values | Empty |
|---|---|---|---|---|---|
{cols}

## Metric definitions
{chr(10).join(metrics)}

## Useful queries (Distill `query` tool)
{chr(10).join(queries) or '- Aggregate by any column.'}

## Caveats
{chr(10).join(caveats) or '- No data-quality issues detected.'}

## What is currently known
{chr(10).join(findings) or '- No findings yet.'}
"""
    return name, desc, _front(name, desc) + "\n" + body


def _upsert(session: Session, slug_: str, kind: str, dataset_id: int | None, desc: str, content: str,
            use_ai: bool) -> Skill:
    h = hashlib.sha256(content.encode()).hexdigest()
    row = session.scalar(select(Skill).where(Skill.slug == slug_))
    if row and row.source_hash == h:
        return row
    author = "template"
    if use_ai:
        from .agent import MODEL, write_skill

        try:
            improved = write_skill(content, {"kind": kind, "note": "Keep every number exactly as given."})
        except Exception:  # noqa: BLE001 - AI enrichment is best-effort; template stays valid
            improved = None
        if improved:
            content, author = improved, MODEL
    if row is None:
        row = Skill(slug=slug_, kind=kind, dataset_id=dataset_id, description=desc, content=content,
                    source_hash=h, author=author)
        session.add(row)
    else:
        row.content, row.description, row.source_hash, row.author = content, desc, h, author
        row.version += 1
        row.updated_at = utcnow()
    return row


def generate_skills(session: Session, use_ai: bool | None = None) -> list[Skill]:
    from .agent import client_or_none
    from .service import process_model

    if use_ai is None:
        use_ai = client_or_none() is not None
    for ds in session.scalars(select(Dataset)):
        if not ds.row_count:
            continue
        ins = list(session.scalars(select(Insight).where(Insight.dataset_id == ds.id)))
        n, d, c = data_context_skill(ds, ins)
        _upsert(session, n, "data_context", ds.id, d, c, use_ai)
        if ds.profile.get("role") == "EventLog":
            model = process_model(session, ds)
            if model.get("cases"):
                n, d, c = sop_skill(ds, model)
                _upsert(session, n, "sop", ds.id, d, c, use_ai)
    for a in session.scalars(select(Automation)):
        _upsert(session, slug(a.name), "automation", a.dataset_id, a.name, a.skill_md, False)
    session.commit()
    return list(session.scalars(select(Skill).order_by(Skill.kind, Skill.slug)))


def bundle(skills: list[Skill]) -> bytes:
    """A zip laid out as skills/<slug>/SKILL.md, ready to drop into an agent's skills folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for s in skills:
            z.writestr(f"skills/{s.slug}/SKILL.md", s.content)
    return buf.getvalue()
