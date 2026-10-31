"""Sync engine: drives a connector's message stream into datasets, persists cursors, logs the run."""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors.sources import build
from .db import Dataset, Record, Source, SyncRun, utcnow
from .profiler import profile_frame

BATCH = 2000


def get_or_create_dataset(session: Session, source: Source, stream: str) -> Dataset:
    ds = session.scalar(select(Dataset).where(Dataset.source_id == source.id, Dataset.stream == stream))
    if ds is None:
        ds = Dataset(source_id=source.id, stream=stream)
        session.add(ds)
        session.flush()
    return ds


def load_frame(session: Session, dataset_id: int) -> pd.DataFrame:
    rows = session.scalars(select(Record.data).where(Record.dataset_id == dataset_id)).all()
    return pd.DataFrame(list(rows))


def refresh_profile(session: Session, ds: Dataset) -> None:
    df = load_frame(session, ds.id)
    ds.row_count = len(df)
    old_glossary = (ds.profile or {}).get("glossary")
    ds.profile = profile_frame(df)
    if old_glossary and {c["name"] for c in old_glossary.get("columns", [])} >= {f["name"] for f in ds.profile["fields"]}:
        ds.profile = {**ds.profile, "glossary": old_glossary}  # AI glossary survives re-syncs
    ds.updated_at = utcnow()


def append_records(session: Session, ds: Dataset, rows: list[dict[str, Any]]) -> None:
    session.add_all(Record(dataset_id=ds.id, data=r) for r in rows)


def sync_source(session: Session, source: Source) -> SyncRun:
    run = SyncRun(source_id=source.id, status="running", log=[])
    session.add(run)
    session.flush()
    connector = build(source.kind, source.config)
    state = dict(source.state or {})
    touched: dict[str, Dataset] = {}
    buffer: dict[str, list[dict[str, Any]]] = {}
    try:
        streams = connector.discover()
        for msg in connector.read(streams, state):
            if msg.type == "RECORD":
                assert msg.stream and msg.data is not None
                buffer.setdefault(msg.stream, []).append(msg.data)
                run.records += 1
                if len(buffer[msg.stream]) >= BATCH:
                    ds = touched.setdefault(msg.stream, get_or_create_dataset(session, source, msg.stream))
                    append_records(session, ds, buffer.pop(msg.stream))
            elif msg.type == "STATE":
                assert msg.stream and msg.data is not None
                state[msg.stream] = msg.data["cursor"]
            else:
                run.log = [*run.log, (msg.data or {}).get("message", "")]
        for stream, rows in buffer.items():
            ds = touched.setdefault(stream, get_or_create_dataset(session, source, stream))
            append_records(session, ds, rows)
        for s in streams:  # make empty streams visible too
            touched.setdefault(s.name, get_or_create_dataset(session, source, s.name))
        session.flush()
        for ds in touched.values():
            refresh_profile(session, ds)
        source.state = state
        run.status = "succeeded"
    except Exception as exc:  # noqa: BLE001 - recorded on the run, re-raised for the API layer
        run.status = "failed"
        run.log = [*run.log, f"error: {exc}"]
        raise
    finally:
        run.finished_at = utcnow()
        session.commit()
    return run
