"""Background scheduler: keeps data fresh and runs enabled automations on their cadence.

Sources opt in with `config.schedule_hours`; automations use `rule.every_hours`. One daemon thread,
one tick per minute, each job in its own DB session so a failure never stops the loop."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import Automation, Source, SyncRun, get_session

log = logging.getLogger("distill.scheduler")


def _due(last: datetime | None, every_hours: float, now: datetime) -> bool:
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= timedelta(hours=every_hours)


def tick(now: datetime | None = None) -> dict[str, int]:
    from .ingest import sync_source
    from .service import refresh_intelligence, run_automation

    now = now or datetime.now(timezone.utc)
    done = {"syncs": 0, "automations": 0}
    s = get_session()
    try:
        for src in s.scalars(select(Source)):
            hours = (src.config or {}).get("schedule_hours")
            if not hours or src.kind == "webhook":
                continue
            last = s.scalar(select(SyncRun.started_at).where(SyncRun.source_id == src.id).order_by(SyncRun.id.desc()))
            if _due(last, float(hours), now):
                try:
                    sync_source(s, src)
                    refresh_intelligence(s, list(src.datasets))
                    done["syncs"] += 1
                except Exception:  # noqa: BLE001
                    log.exception("scheduled sync failed for source %s", src.id)
        for a in s.scalars(select(Automation).where(Automation.enabled.is_(True))):
            every = a.rule.get("every_hours")
            if not every:
                continue
            last = datetime.fromisoformat(a.runs[-1]["at"]) if a.runs else None
            if _due(last, float(every), now):
                try:
                    run_automation(s, a)
                    done["automations"] += 1
                except Exception:  # noqa: BLE001
                    log.exception("scheduled automation %s failed", a.id)
    finally:
        s.close()
    return done


def start(interval_seconds: int = 60) -> threading.Event:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval_seconds):
            tick()

    threading.Thread(target=loop, name="distill-scheduler", daemon=True).start()
    return stop
