"""Agent tests. The Claude client is replaced by a scripted fake so the tool loop is tested offline."""
from types import SimpleNamespace as NS

import pytest

from distill import agent
from distill.db import Dataset, Insight, get_session


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


def fake_client(script):
    m = FakeMessages(script)
    return NS(beta=NS(messages=m)), m


def tool_use(id_, name, inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


@pytest.fixture()
def session(demo):
    s = get_session()
    yield s
    s.close()


def orders_id(s):
    return next(d.id for d in s.query(Dataset).all() if d.stream == "orders")


def test_query_tool_finds_north_drop(session):
    tb = agent.Toolbox(session)
    ds = orders_id(session)
    recent = tb.query(ds, None, "count", ["region"], [], None, 14)
    prior = tb.query(ds, None, "count", ["region"], [], None, 28)
    r = {x["region"]: x["value"] for x in recent["result"]}
    p = {x["region"]: x["value"] - r[x["region"]] for x in prior["result"]}
    assert r["North"] / p["North"] < 0.7 < r["Center"] / p["Center"]
    weekly = tb.query(ds, "amount", "sum", [], [{"column": "region", "op": "==", "value": "North"}], "week", None)
    assert weekly["groups"] >= 17 and "period" in weekly["result"][0]


def test_query_tool_validates(session):
    tb = agent.Toolbox(session)
    with pytest.raises(agent.ToolError, match="unknown column"):
        tb.query(orders_id(session), "nope", "sum", [], [], None, None)
    with pytest.raises(agent.ToolError):
        tb.call("drop_tables", {})


def test_agent_loop(session):
    ds = orders_id(session)
    client, msgs = fake_client([
        NS(stop_reason="tool_use", model="claude-opus-5-5", content=[
            text("Let me check."),
            tool_use("t1", "query", {"dataset_id": ds, "metric": None, "agg": "count", "group_by": ["region"],
                                     "filters": [], "time_grain": None, "last_days": 14}),
            tool_use("t2", "describe_dataset", {"dataset_id": 9999}),
        ]),
        NS(stop_reason="end_turn", model="claude-opus-5-5", content=[text("North orders fell ~50%.")]),
    ])
    out = agent.run_agent(session, "Why is North down?", client=client)
    assert out["text"] == "North orders fell ~50%."
    assert [st["tool"] for st in out["steps"]] == ["query", "describe_dataset"]
    assert out["steps"][0]["ok"] and not out["steps"][1]["ok"]
    first = msgs.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert all(t["strict"] for t in first["tools"])
    # both tool results returned together in ONE user message, error flagged
    results = msgs.calls[1]["messages"][2]  # (list is shared; later turns are appended after it)
    assert results["role"] == "user" and len(results["content"]) == 2
    assert results["content"][1]["is_error"] is True


def test_agent_refusal_and_no_client(session):
    client, _ = fake_client([NS(stop_reason="refusal", content=[], model="x")])
    assert "declined" in agent.run_agent(session, "hi", client=client)["text"]
    assert agent.run_agent(session, "hi", client=None)["engine"] == "unavailable"


def test_investigate_endpoint_requires_ai(demo):
    ins = demo.get("/api/insights").json()[0]
    assert demo.post(f"/api/insights/{ins['id']}/investigate").status_code == 503
    assert demo.get("/api/ai").json() == {**demo.get("/api/ai").json(), "enabled": False, "model": "claude-opus-5-5"}


def test_write_skill_keeps_front_matter():
    client, msgs = fake_client([NS(stop_reason="end_turn", content=[text("# Better playbook\n1. step")])])
    md = agent.write_skill("---\nname: x\ndescription: y\n---\n\n# X\nold", {"rule": {}}, client=client)
    assert md.startswith("---\nname: x") and "Better playbook" in md


def test_investigate_prompt_mentions_insight(session):
    ins = session.query(Insight).first()
    client, msgs = fake_client([NS(stop_reason="end_turn", content=[text("root cause")], model="claude-opus-5-5")])
    out = agent.investigate(session, ins, client=client)
    assert out["text"] == "root cause"
    assert f"insight_id={ins.id}" in msgs.calls[0]["messages"][0]["content"]
    assert msgs.calls[0]["output_config"]["effort"] == "high"
