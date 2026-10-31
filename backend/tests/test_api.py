"""End-to-end API tests: collect -> analyze -> learn -> automate."""


def test_demo_flow(demo):
    datasets = demo.get("/api/datasets").json()
    assert {d["stream"]: d["role"] for d in datasets} == {"orders": "TransactionTable", "support_events": "EventLog"}
    insights = demo.get("/api/insights").json()
    assert len(insights) >= 8
    assert insights == sorted(insights, key=lambda i: -i["score"])
    # idempotent: demo again does not duplicate
    assert demo.post("/api/demo").json()["existing"] is True


def test_resync_does_not_duplicate(demo):
    src = demo.get("/api/sources").json()[0]
    before = {d["stream"]: d["rows"] for d in demo.get("/api/datasets").json()}
    r = demo.post(f"/api/sources/{src['id']}/sync").json()
    assert r["run"]["records"] == 0  # cursors saved -> nothing new
    after = {d["stream"]: d["rows"] for d in demo.get("/api/datasets").json()}
    assert before == after
    assert len(demo.get("/api/insights").json()) >= 8


def test_feedback_learns(demo):
    anomalies = demo.get("/api/insights", params={"status": "new"}).json()
    anomaly = next(i for i in anomalies if i["kind"] == "anomaly")
    r = demo.post(f"/api/insights/{anomaly['id']}/feedback", json={"useful": False}).json()
    assert r["learning"]["beta"] == 2 and r["learning"]["new_threshold"] > 3.5
    # dismissed insight stays hidden, also after re-analysis
    ds = anomaly["dataset_id"]
    demo.post(f"/api/datasets/{ds}/analyze")
    assert anomaly["id"] not in {i["id"] for i in demo.get("/api/insights").json()}
    learning = demo.get("/api/learning").json()
    assert any(a["kind"] == "anomaly" and a["posterior_mean"] < 0.5 for a in learning["arms"])
    assert learning["thresholds"][0]["z"] > 3.5

    # a useful vote lifts the kind's ranking
    conc = next(i for i in demo.get("/api/insights").json() if i["kind"] == "concentration")
    demo.post(f"/api/insights/{conc['id']}/feedback", json={"useful": True})
    updated = next(i for i in demo.get("/api/insights").json() if i["id"] == conc["id"])
    assert updated["score"] > conc["score"]


def test_automate_and_export(demo):
    insights = demo.get("/api/insights").json()
    cand = next(i for i in insights if i["kind"] == "automation_candidate" and "CRM" in i["title"])
    a = demo.post(f"/api/insights/{cand['id']}/automate").json()
    assert a["kind"] == "event_bot" and a["rule"]["trigger_activity"] == "Triage"
    assert a["est_minutes_saved_per_week"] > 60
    assert a["n8n_workflow"]["nodes"][0]["type"] == "n8n-nodes-base.webhook"
    assert demo.patch(f"/api/automations/{a['id']}", json={"enabled": True}).json()["enabled"] is True
    assert demo.get("/api/learning").json()["minutes_saved_per_week"] == a["est_minutes_saved_per_week"]
    assert demo.get(f"/api/automations/{a['id']}/SKILL.md").text.startswith("---\nname: bot-automate")
    assert demo.get(f"/api/automations/{a['id']}/n8n.json").json()["name"] == a["name"]

    north = next(i for i in insights if i["kind"] == "segment_driver")
    w = demo.post(f"/api/insights/{north['id']}/automate").json()
    res = demo.post(f"/api/automations/{w['id']}/evaluate").json()
    assert res["triggered"] is True and "North" in res["message"]
    assert demo.get(f"/api/automations/{w['id']}").json()["runs"][-1]["triggered"] is True

    corr = next(i for i in insights if i["kind"] == "correlation")
    assert demo.post(f"/api/insights/{corr['id']}/automate").status_code == 422


def test_upload_csv_and_webhook(client):
    csv = "order,created_at,amount,segment\n" + "\n".join(
        f"{i},2026-0{1 + i % 6}-{10 + i % 15}T10:00:00,{100 + i},{'a' if i % 2 else 'b'}" for i in range(60))
    r = client.post("/api/sources/upload", data={"name": "Sales", "stream": "sales", "cursor_field": "created_at"},
                    files={"file": ("sales.csv", csv, "text/csv")})
    assert r.status_code == 201 and r.json()["run"]["records"] == 60
    ds = client.get("/api/datasets").json()[0]
    assert ds["role"] == "TransactionTable"
    assert client.get("/api/sources").json()[0]["config"]["content"] == f"<{len(csv):,} chars>"

    hook = client.post("/api/sources", json={"name": "Events", "kind": "webhook", "config": {"stream": "ev"}}).json()
    r = client.post(f"/api/sources/{hook['id']}/events",
                    json=[{"case_id": "1", "activity": "a", "timestamp": "2026-01-01T00:00:00"}] * 3)
    assert r.status_code == 202 and r.json()["rows"] == 3
    assert client.post(f"/api/sources/{ds['source_id']}/events", json={}).status_code == 409


def test_secrets_masked_and_validation(client):
    r = client.post("/api/sources", json={"name": "API", "kind": "rest",
                                          "config": {"stream": "x", "url": "http://example.invalid", "bearer_token": "s3cret"}})
    assert r.json()["config"]["bearer_token"] == "••••"
    assert "s3cret" not in client.get("/api/sources").text
    assert client.post("/api/sources", json={"name": "x", "kind": "nope"}).status_code == 422
    assert client.get("/api/datasets/999").status_code == 404


def test_digest_and_ask_without_llm(demo):
    d = demo.get("/api/digest").json()
    assert d["engine"] == "template" and "Top findings" in d["text"]
    a = demo.post("/api/ask", json={"question": "what about North region?"}).json()
    assert "North" in a["text"]


def test_delete_source_cascades(demo):
    src = demo.get("/api/sources").json()[0]
    assert demo.delete(f"/api/sources/{src['id']}").status_code == 204
    assert demo.get("/api/datasets").json() == [] and demo.get("/api/insights").json() == []


def test_postgres_urls_are_normalised():
    from distill.db import normalize_url
    assert normalize_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"


def test_demo_mode_seeds_on_boot_and_briefs_without_a_key(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from distill import agent
    from distill.api import app

    monkeypatch.setenv("DISTILL_DEMO", "1")
    monkeypatch.setenv("DISTILL_SCHEDULER", "0")
    monkeypatch.setenv("DISTILL_DATABASE_URL", f"sqlite:///{tmp_path}/boot.db")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(agent, "client_or_none", lambda: None)
    with TestClient(app) as c:
        assert c.get("/api/ai").json()["demo"] is True
        assert len(c.get("/api/insights").json()) >= 8       # seeded before any request
        b = c.post("/api/autopilot").json()
        assert b["engine"] == "demo" and b["text"]
