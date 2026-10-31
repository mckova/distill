"""Skills, optimization, data quality, memory, copilot, autopilot and scheduler."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from distill import agent, scheduler


def text(t):
    return NS(type="text", text=t)


class Scripted:
    """Fake Anthropic client: returns scripted responses, records requests."""

    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=self)

    def create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        return self.script.pop(0)


@pytest.fixture()
def ai(monkeypatch):
    holder = {}

    def install(script):
        holder["c"] = Scripted(script)
        monkeypatch.setattr(agent, "client_or_none", lambda: holder["c"])
        return holder["c"]

    return install


def test_skills_generated_from_data(demo):
    skills = {k["slug"]: k for k in demo.get("/api/skills").json()}
    assert {"run-support-events-process", "query-orders-data", "query-support-events-data"} <= set(skills)
    sop = demo.get(f"/api/skills/{skills['run-support-events-process']['id']}").json()["content"]
    assert sop.startswith("---\nname: run-support-events-process\ndescription: ")
    assert "**Copy to CRM**" in sop and "Known delays" in sop and "Assign Agent" in sop
    ctx = demo.get(f"/api/skills/{skills['query-orders-data']['id']}").json()["content"]
    assert "| `amount` |" in ctx and "duplicate" in ctx.lower() and "sum(amount)" in ctx
    z = zipfile.ZipFile(io.BytesIO(demo.get("/api/skills/export.zip").content))
    assert "skills/run-support-events-process/SKILL.md" in z.namelist()
    # unchanged data -> same version; skills version up only when content changes
    again = {k["slug"]: k for k in demo.post("/api/skills/generate").json()}
    assert again["query-orders-data"]["version"] == 1


def test_data_quality_found_and_guardable(demo):
    dq = [i for i in demo.get("/api/insights").json() if i["kind"] == "data_quality"]
    assert any("duplicate" in i["title"] for i in dq)
    key = next(i for i in dq if i["evidence"]["check"] == "duplicate_key")
    a = demo.post(f"/api/insights/{key['id']}/automate").json()
    r = demo.post(f"/api/automations/{a['id']}/evaluate").json()
    assert r["triggered"] and "15 repeated" in r["message"]


def test_optimization_quantifies_impact(demo):
    o = demo.get("/api/optimization").json()
    kinds = {x["kind"] for x in o["opportunities"]}
    assert {"automate_step", "remove_wait", "recover_revenue"} <= kinds
    crm = next(x for x in o["opportunities"] if x["kind"] == "automate_step")
    assert 3 < crm["hours_per_week"] < 10  # ~36 runs/week x ~9 min
    sim = o["simulations"][0]
    assert sim["projected_case_hours"] < sim["current_case_hours"] and sim["reduction_pct"] > 10
    north = next(x for x in o["opportunities"] if x["kind"] == "recover_revenue")
    assert "North" in north["detail"] and north["revenue_at_risk_per_week"] > 0


def test_feedback_reason_becomes_memory_and_reaches_the_agent(demo, ai):
    ins = demo.get("/api/insights").json()[0]
    demo.post(f"/api/insights/{ins['id']}/feedback", json={"useful": False, "reason": "Sep 23 was a planned promo"})
    mem = demo.get("/api/memory").json()
    assert mem[0]["source"] == "feedback" and "planned promo" in mem[0]["text"]
    demo.post("/api/memory", json={"text": "Fiscal week starts on Sunday"})
    client = ai([NS(stop_reason="end_turn", model="claude-opus-5-5", content=[text("ok")])])
    demo.post("/api/chat", json={"message": "hi"})
    system = client.calls[0]["system"]
    assert "Company memory" in system and "planned promo" in system and "Sunday" in system


def test_copilot_is_multi_turn_and_can_remember(demo, ai):
    client = ai([
        NS(stop_reason="tool_use", model="claude-opus-5-5", content=[
            NS(type="tool_use", id="t1", name="remember", input={"note": "North drop is driven by fewer orders"})]),
        NS(stop_reason="end_turn", model="claude-opus-5-5", content=[text("Noted.")]),
        NS(stop_reason="end_turn", model="claude-opus-5-5", content=[text("Second answer")]),
    ])
    c = demo.post("/api/chat", json={"message": "Why is North down?"}).json()
    assert c["turns"][-1]["text"] == "Noted." and c["turns"][-1]["steps"][0]["tool"] == "remember"
    assert any(m["source"] == "agent" for m in demo.get("/api/memory").json())
    c2 = demo.post("/api/chat", json={"message": "And South?", "conversation_id": c["id"]}).json()
    assert [t["role"] for t in c2["turns"]] == ["user", "assistant", "user", "assistant"]
    history = client.calls[2]["messages"]
    assert history[0]["content"] == "Why is North down?" and history[-1]["content"] == "And South?"
    assert history[2]["content"][0]["type"] == "tool_result"  # earlier tool turn kept verbatim


def test_autopilot_briefing(demo, ai):
    ai([
        NS(stop_reason="tool_use", model="claude-opus-5-5", content=[
            NS(type="tool_use", id="a", name="list_opportunities", input={}),
            NS(type="tool_use", id="b", name="list_insights", input={"kind": None, "limit": 5})]),
        NS(stop_reason="end_turn", model="claude-opus-5-5", content=[text("Headline: automate Copy to CRM.")]),
    ])
    b = demo.post("/api/autopilot").json()
    assert b["text"].startswith("Headline") and [s["tool"] for s in b["steps"]] == ["list_opportunities", "list_insights"]
    assert all(s["ok"] for s in b["steps"])
    assert demo.get("/api/briefings").json()[0]["id"] == b["id"]


def test_ai_endpoints_need_key(demo):
    assert demo.post("/api/chat", json={"message": "x"}).status_code == 503
    assert demo.post("/api/autopilot").status_code == 503


def test_glossary_structured_output():
    c = Scripted([NS(stop_reason="end_turn", content=[text(json.dumps(
        {"summary": "Customer orders", "columns": [{"name": "amount", "meaning": "Order value in ILS"}]}))])])
    g = agent.glossary({"role": "TransactionTable", "fields": [{"name": "amount"}]}, "orders", client=c)
    assert g["columns"][0]["meaning"] == "Order value in ILS"
    assert c.calls[0]["output_config"]["format"]["type"] == "json_schema"


def test_scheduler_runs_due_automations(demo):
    ins = next(i for i in demo.get("/api/insights").json() if i["kind"] == "segment_driver")
    a = demo.post(f"/api/insights/{ins['id']}/automate").json()
    demo.patch(f"/api/automations/{a['id']}", json={"enabled": True})
    assert scheduler.tick()["automations"] == 1
    assert scheduler.tick()["automations"] == 0  # not due again for 24h
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    assert scheduler.tick(now=later)["automations"] == 1
