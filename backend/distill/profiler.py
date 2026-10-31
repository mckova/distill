"""Column fingerprinting + semantic typing + table-role detection.

Three stages: (1) fingerprint every field (cardinality, nulls, ranges),
(2) assign a *semantic type* from name heuristics plus the fingerprint,
(3) classify the whole table into a role ("TransactionTable", "EventLog",
...) which decides which analysis templates apply (see insights.py).
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

NAME_RULES: list[tuple[str, str]] = [
    (r"(^|_)(case|ticket|process|trace)_?id$", "CaseID"),
    (r"^(activity|event|action|step|status_change|event_name)$", "Activity"),
    (r"(^|_)(resource|agent|assignee|user|employee|owner)$", "Resource"),
    (r"(amount|revenue|price|income|total|cost|value|sales)", "Money"),
    (r"discount", "Discount"),
    (r"(qty|quantity|units|count)$", "Quantity"),
    (r"(created|timestamp|date|time|_at$|^ts$)", "DateTime"),
    (r"(region|country|state|city|area|branch)", "Location"),
    (r"(category|type|segment|channel|source|tier|plan|department)", "Category"),
    (r"email", "Email"),
    (r"(^id$|_id$|^uuid$)", "ID"),
]


def _parse_dates(s: pd.Series) -> pd.Series | None:
    if s.dtype.kind in "biuf":
        return None
    sample = s.dropna().astype(str).head(50)
    if sample.empty or sample.str.contains(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}", regex=True).mean() < 0.8:
        return None
    try:
        parsed = pd.to_datetime(s, errors="coerce", utc=True, format="mixed")
    except (ValueError, TypeError):
        return None
    return parsed if parsed.notna().mean() > 0.9 else None


def fingerprint(name: str, s: pd.Series) -> dict[str, Any]:
    n = len(s)
    non_null = s.dropna()
    fp: dict[str, Any] = {
        "name": name,
        "null_ratio": round(1 - len(non_null) / n, 4) if n else 0.0,
        "distinct": int(non_null.astype(str).nunique()),
    }
    fp["cardinality_ratio"] = round(fp["distinct"] / max(len(non_null), 1), 4)
    numeric = pd.to_numeric(non_null, errors="coerce")
    is_numeric = len(non_null) > 0 and numeric.notna().mean() > 0.95 and non_null.dtype != bool
    dates = None if is_numeric else _parse_dates(s)
    if is_numeric:
        fp.update(kind="number", min=float(numeric.min()), max=float(numeric.max()),
                  mean=round(float(numeric.mean()), 4), std=round(float(numeric.std() or 0), 4))
    elif dates is not None:
        fp.update(kind="datetime", min=str(dates.min()), max=str(dates.max()))
    else:
        fp.update(kind="text", top=non_null.astype(str).value_counts().head(5).to_dict())
    fp["semantic_type"] = semantic_type(name, fp)
    return fp


def semantic_type(name: str, fp: dict[str, Any]) -> str:
    lname = name.lower()
    for pattern, sem in NAME_RULES:
        if re.search(pattern, lname):
            if sem == "DateTime" and fp["kind"] != "datetime":
                continue
            if sem in ("Money", "Discount", "Quantity") and fp["kind"] != "number":
                continue
            return sem
    if fp["kind"] == "datetime":
        return "DateTime"
    if fp["kind"] == "number":
        return "Number"
    if fp["distinct"] <= 30 and fp["cardinality_ratio"] < 0.2:
        return "Category"
    if fp["cardinality_ratio"] > 0.95:
        return "ID"
    return "Text"


def table_role(fields: list[dict[str, Any]]) -> str:
    sems = {f["semantic_type"] for f in fields}
    if {"CaseID", "Activity", "DateTime"} <= sems:
        return "EventLog"
    if "DateTime" in sems and "Money" in sems:
        return "TransactionTable"
    if "DateTime" in sems:
        return "TimeSeriesTable"
    return "GenericTable"


def profile_frame(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {"role": "GenericTable", "fields": [], "rows": 0}
    fields = [fingerprint(c, df[c]) for c in df.columns]
    role = table_role(fields)
    pick = lambda sem: next((f["name"] for f in fields if f["semantic_type"] == sem), None)  # noqa: E731
    return {
        "rows": int(len(df)),
        "role": role,
        "fields": fields,
        "roles": {  # the "dimension bindings" templates use
            "time": pick("DateTime"), "money": pick("Money"), "case": pick("CaseID"),
            "activity": pick("Activity"), "resource": pick("Resource"), "discount": pick("Discount"),
            "quantity": pick("Quantity"),
        },
    }
