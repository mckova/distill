"""Engine-level tests: the demo company plants known patterns; the engine must find each one."""
from datetime import timedelta

import pandas as pd
import pytest

from distill import automations as auto
from distill.connectors.base import Stream
from distill.connectors.sources import FileConnector
from distill.demo import END, generate_company
from distill.insights import analyze
from distill.process_mining import discover
from distill.profiler import profile_frame


@pytest.fixture(scope="module")
def company():
    data = generate_company(seed=7)
    out = {}
    for name, rows in data.items():
        df = pd.DataFrame(rows)
        out[name] = (df, profile_frame(df))
    return out


def by_kind(findings, kind):
    return [f for f in findings if f.kind == kind]


def test_profiler_assigns_roles(company):
    _, orders = company["orders"]
    _, events = company["support_events"]
    assert orders["role"] == "TransactionTable"
    assert orders["roles"]["time"] == "created_at" and orders["roles"]["money"] == "amount"
    assert events["role"] == "EventLog"
    assert events["roles"] == {**events["roles"], "case": "case_id", "activity": "activity", "resource": "resource"}
    sem = {f["name"]: f["semantic_type"] for f in orders["fields"]}
    assert sem["region"] == "Location" and sem["discount"] == "Discount" and sem["order_id"] == "ID"


def test_planted_transaction_patterns(company):
    df, profile = company["orders"]
    findings = analyze(df, profile)
    seg = by_kind(findings, "segment_driver")
    assert any(f.evidence["segment"] == "North" and f.evidence["pct_change"] < -30 for f in seg)
    spike_day = str((END - timedelta(days=37)).date())
    assert any(f.evidence["date"] == spike_day for f in by_kind(findings, "anomaly"))
    conc = by_kind(findings, "concentration")
    assert conc and conc[0].evidence["entity"] == "customer_id" and conc[0].evidence["top_share"] > 50
    corr = by_kind(findings, "correlation")
    assert any({f.evidence["x"], f.evidence["y"]} == {"amount", "discount"} and f.evidence["rho"] < -0.5 for f in corr)


def test_segment_driver_recall_and_false_positive_rate():
    """Statistical contract on 20 seeds: find the planted North drop >=85% of the time while
    reporting at most one spurious segment per 4 datasets (~1% of the ~440 tests run)."""
    recall = false_pos = 0
    for seed in range(20):
        df = pd.DataFrame(generate_company(seed=seed)["orders"])
        seg = by_kind(analyze(df, profile_frame(df)), "segment_driver")
        recall += any(f.evidence["segment"] == "North" for f in seg)
        false_pos += sum(f.evidence["segment"] != "North" for f in seg)
    assert recall >= 17 and false_pos <= 5, (recall, false_pos)


def test_process_discovery(company):
    df, profile = company["support_events"]
    r = profile["roles"]
    model = discover(df, r["case"], r["activity"], r["time"], r["resource"])
    assert model["cases"] == 720
    assert model["start_activities"] == {"Ticket Created": 720}
    edge_names = {(e["source"], e["target"]) for e in model["edges"]}
    assert ("Request Info", "Customer Reply") in edge_names and ("Customer Reply", "Request Info") in edge_names
    top = model["bottlenecks"][0]
    assert top["target"] == "Assign Agent" and 15 < top["mean_hours"] < 25
    best = max(model["activities"], key=lambda a: a["automation_potential"])
    assert best["name"] == "Copy to CRM"
    system = next(a for a in model["activities"] if a["name"] == "Close")
    assert system["automation_potential"] < 0.3  # already automated: must not be suggested


def test_process_insights(company):
    df, profile = company["support_events"]
    kinds = {(f.kind, f.key) for f in analyze(df, profile)}
    assert ("automation_candidate", "Copy to CRM") in kinds
    assert ("rework", "Request Info") in kinds
    assert any(k == "bottleneck" and key.endswith("->Assign Agent") for k, key in kinds)


def test_incremental_file_sync():
    csv1 = "id,ts,v\n1,2026-01-01,5\n2,2026-01-02,6\n"
    stream = Stream("s", cursor_field="ts")
    msgs = list(FileConnector({"stream": "s", "content": csv1}).read([stream], {}))
    assert [m.type for m in msgs] == ["RECORD", "RECORD", "STATE"] and msgs[-1].data["cursor"] == "2026-01-02"
    csv2 = csv1 + "3,2026-01-03,7\n"
    msgs = list(FileConnector({"stream": "s", "content": csv2}).read([stream], {"s": "2026-01-02"}))
    assert [m.data["id"] for m in msgs if m.type == "RECORD"] == [3]


def test_compile_every_actionable_kind(company):
    for name in ("orders", "support_events"):
        df, profile = company[name]
        for f in analyze(df, profile):
            stats = {"frequency": 600, "weeks": 17, "predecessor": "Triage"} if f.kind == "automation_candidate" else {}
            spec = auto.compile_insight({"kind": f.kind, "evidence": f.evidence}, profile, stats)
            if f.kind in ("correlation", "variability"):
                assert spec is None
                continue
            assert spec is not None, f.kind
            wf, md = auto.build_artifacts(spec, f.title, "http://x", "1")
            names = {n["name"] for n in wf["nodes"]}
            for src, conn in wf["connections"].items():
                assert src in names and conn["main"][0][0]["node"] in names
            assert md.startswith("---\nname: ") and "\ndescription: " in md
            if spec["rule"]["type"] != "event_bot":
                result = auto.evaluate(spec["rule"], df)
                assert set(result) == {"triggered", "message", "details"}


def test_north_watch_triggers(company):
    df, profile = company["orders"]
    rule = {"type": "metric_threshold", "time": "created_at", "value": None, "filter": {"region": "North"},
            "window_days": 7, "condition": {"pct_change_below": -20}}
    assert auto.evaluate(rule, df)["triggered"] is True
    rule["filter"] = {"region": "Center"}
    assert auto.evaluate(rule, df)["triggered"] is False
