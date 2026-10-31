"""Synthetic company "Orbit Supplies" with planted, discoverable patterns.

Planted ground truth (the test suite checks the engine finds each one):
  orders
    * revenue in region "North" drops ~45% over the final 14 days      -> trend/segment-driver insight
    * one day (END - 37d) has a 4x order spike                         -> anomaly insight
    * the top ~10% of customers produce the majority of revenue       -> concentration insight
    * discount is negatively correlated with order amount            -> correlation insight
    * 15 orders were exported twice                                  -> data-quality insight
  support_events (process log: case_id / activity / timestamp / resource)
    * "Triage" -> "Assign Agent" waits ~20 business hours              -> bottleneck insight
    * "Copy to CRM" is a short, manual, near-universal step           -> automation candidate
    * "Request Info" <-> "Customer Reply" loops                       -> rework insight
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

END = datetime(2026, 10, 30, tzinfo=timezone.utc)
REGIONS = ["North", "South", "Center", "Coast"]
CHANNELS = ["web", "phone", "partner"]
CATEGORIES = ["office", "electronics", "furniture", "cleaning"]
AGENTS = ["dana", "omer", "noa", "yossi", "lior"]


def _orders(rng: random.Random, days: int) -> list[dict[str, Any]]:
    start = END - timedelta(days=days)
    customers = [f"C{i:04d}" for i in range(400)]
    whales = set(customers[:40])
    rows: list[dict[str, Any]] = []
    oid = 1
    for d in range(days):
        day = start + timedelta(days=d)
        weekday_factor = 0.55 if day.weekday() in (4, 5) else 1.0  # Fri/Sat quieter (IL week)
        n = int(rng.gauss(38, 4) * weekday_factor)
        if d == days - 37:
            n *= 4
        for _ in range(max(n, 5)):
            region = rng.choice(REGIONS)
            if region == "North" and d >= days - 14 and rng.random() < 0.45:
                continue
            cust = rng.choice(customers[:40]) if rng.random() < 0.3 else rng.choice(customers)
            base = rng.lognormvariate(5.0, 0.5) * (3 if cust in whales else 1)
            # sales reps push small baskets with discounts -> planted negative correlation
            discount = round(min(max(0.42 - 0.06 * (base ** 0.5) / 2 + rng.gauss(0, 0.03), 0), 0.4), 3)
            amount = round(base * (1 - discount), 2)
            ts = day + timedelta(minutes=rng.randint(8 * 60, 20 * 60))
            rows.append({
                "order_id": f"O{oid:06d}", "created_at": ts.isoformat(), "customer_id": cust,
                "region": region, "channel": rng.choices(CHANNELS, [6, 3, 1])[0],
                "product_category": rng.choice(CATEGORIES), "quantity": rng.randint(1, 12),
                "amount": max(amount, 5.0), "discount": discount,
            })
            oid += 1
    # planted data-quality issue: a retrying integration exported 15 orders twice
    rows.extend(dict(r) for r in rng.sample(rows, 15))
    rows.sort(key=lambda r: r["created_at"])
    return rows


def _support(rng: random.Random, days: int) -> list[dict[str, Any]]:
    start = END - timedelta(days=days)
    rows: list[dict[str, Any]] = []
    for case in range(1, days * 6 + 1):
        t = start + timedelta(days=rng.uniform(0, days - 3), hours=rng.uniform(8, 17))
        channel = rng.choice(["email", "chat", "phone"])
        cid = f"T{case:05d}"

        def ev(activity: str, resource: str, gap_h: float) -> None:
            nonlocal t
            t = t + timedelta(hours=max(gap_h, 0.01))
            rows.append({"case_id": cid, "activity": activity, "timestamp": t.isoformat(),
                         "resource": resource, "channel": channel})

        ev("Ticket Created", "system", 0)
        ev("Triage", rng.choice(AGENTS), rng.uniform(0.2, 2))
        if rng.random() < 0.85:
            ev("Copy to CRM", rng.choice(AGENTS), rng.uniform(0.05, 0.25))
        ev("Assign Agent", "lead", rng.gauss(20, 5))  # planted bottleneck
        for _ in range(rng.choices([0, 1, 2, 3], [5, 3, 1.5, 0.5])[0]):  # planted rework loop
            ev("Request Info", rng.choice(AGENTS), rng.uniform(0.5, 4))
            ev("Customer Reply", "customer", rng.uniform(2, 30))
        if rng.random() < 0.12:
            ev("Escalate", "lead", rng.uniform(1, 6))
        ev("Resolve", rng.choice(AGENTS), rng.uniform(1, 8))
        ev("Close", "system", rng.uniform(1, 6))
    return rows


def generate_company(seed: int = 7, days: int = 120) -> dict[str, list[dict[str, Any]]]:
    rng = random.Random(seed)
    return {"orders": _orders(rng, days), "support_events": _support(rng, days)}
