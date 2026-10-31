"""Executive digest + "ask your data". Uses Claude when credentials exist, deterministic text otherwise.

The model never sees raw rows: only the already-computed insights (titles, numbers, evidence
summaries) plus the dataset profiles. That keeps prompts small, cheap and free of PII.
"""
from __future__ import annotations

import json
import os
from typing import Any

MODEL = os.environ.get("DISTILL_MODEL", "claude-opus-5-5")

SYSTEM = (
    "You are Distill's analyst. You receive pre-computed findings about a company's data. "
    "Write for a busy operations manager: lead with what matters, cite the numbers you were given, "
    "never invent figures, and end with the concrete next action. Answer in the language of the question."
)


def _compact(insights: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    keep = ("pct_change", "segment", "dimension", "top_share", "z", "date", "mean_hours", "case_share",
            "automation_potential", "rho", "share_of_case_time")
    return [{"kind": i["kind"], "title": i["title"], "status": i["status"], "score": i["score"],
             "numbers": {k: v for k, v in i["evidence"].items() if k in keep}} for i in insights[:limit]]


def _client():
    from .agent import client_or_none

    return client_or_none()


def _ask(client: Any, prompt: str) -> str | None:
    import anthropic

    from .agent import _create

    try:
        resp = _create(client, max_tokens=4000, system=SYSTEM, output_config={"effort": "low"},
                       messages=[{"role": "user", "content": prompt}])
    except anthropic.APIError:
        return None
    if resp.stop_reason == "refusal":
        return None
    return "".join(b.text for b in resp.content if b.type == "text").strip() or None


def fallback_digest(insights: list[dict[str, Any]]) -> str:
    if not insights:
        return "No findings yet. Connect a source and run an analysis."
    lines = ["**Top findings this run**", ""]
    for i in insights[:5]:
        lines.append(f"- {i['title']}")
    autos = [i for i in insights if i["kind"] in ("automation_candidate", "bottleneck")]
    if autos:
        lines += ["", f"**Suggested next action:** automate the fix for \"{autos[0]['title']}\"."]
    return "\n".join(lines)


def digest(insights: list[dict[str, Any]]) -> dict[str, Any]:
    client = _client()
    if client:
        text = _ask(client, "Write a 6-line executive digest of these findings:\n"
                    + json.dumps(_compact(insights), ensure_ascii=False))
        if text:
            return {"text": text, "engine": MODEL}
    return {"text": fallback_digest(insights), "engine": "template"}


def answer(question: str, insights: list[dict[str, Any]], session: Any = None) -> dict[str, Any]:
    """Free-form questions go to the tool-using analyst agent; without credentials, keyword fallback."""
    client = _client()
    if client and session is not None:
        import anthropic

        from .agent import run_agent

        try:
            result = run_agent(session, question, client=client)
        except anthropic.APIError as exc:
            result = {"text": None, "error": str(exc)}
        if result.get("text"):
            return result
    words = {w.lower().strip("?.,!") for w in question.split() if len(w) > 2}
    hits = [i for i in insights if words & set(i["title"].lower().replace("'", " ").split())]
    if hits:
        return {"text": "**Related findings**\n\n" + "\n".join(f"- {h['title']}" for h in hits[:5]), "engine": "keyword",
                "steps": []}
    return {"text": "Set ANTHROPIC_API_KEY to let the analyst agent answer free-form questions. Meanwhile:\n\n"
            + fallback_digest(insights), "engine": "template", "steps": []}
