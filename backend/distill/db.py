"""Persistence layer: SQLAlchemy 2.0 ORM on SQLite by default, Postgres via DISTILL_DATABASE_URL."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Source(Base):
    """A configured connector instance: kind + config + saved incremental state."""

    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))  # csv | json | rest | sql | demo
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)  # incremental cursors per stream
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    datasets: Mapped[list["Dataset"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class Dataset(Base):
    """One stream materialised from a source."""

    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    stream: Mapped[str] = mapped_column(String(120))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    profile: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source: Mapped[Source] = relationship(back_populates="datasets")
    records: Mapped[list["Record"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")


class Record(Base):
    __tablename__ = "records"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    data: Mapped[dict[str, Any]] = mapped_column()
    emitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dataset: Mapped[Dataset] = relationship(back_populates="records")


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(String(20))
    records: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[list[Any]] = mapped_column(default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Insight(Base):
    __tablename__ = "insights"
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # dedupe across re-analysis
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    severity: Mapped[float] = mapped_column(Float)  # 0..1 statistical strength
    score: Mapped[float] = mapped_column(Float, default=0.0)  # learned rank score
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | useful | dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Automation(Base):
    __tablename__ = "automations"
    id: Mapped[int] = mapped_column(primary_key=True)
    insight_id: Mapped[int | None] = mapped_column(ForeignKey("insights.id"), nullable=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # alert | process | report
    rule: Mapped[dict[str, Any]] = mapped_column(default=dict)
    n8n_workflow: Mapped[dict[str, Any]] = mapped_column(default=dict)
    skill_md: Mapped[str] = mapped_column(Text, default="")
    est_minutes_saved_per_week: Mapped[float] = mapped_column(Float, default=0.0)
    enabled: Mapped[bool] = mapped_column(default=False)
    runs: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LearningArm(Base):
    """Beta(alpha, beta) posterior per insight kind: the learned prior of 'is this kind useful here'."""

    __tablename__ = "learning_arms"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    alpha: Mapped[float] = mapped_column(Float, default=1.0)
    beta: Mapped[float] = mapped_column(Float, default=1.0)


class Setting(Base):
    """Per-metric adaptive parameters (e.g. anomaly z-threshold tuned from feedback)."""

    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Skill(Base):
    """An Agent Skill generated from company data (process SOP, data context, or automation playbook)."""

    __tablename__ = "skills"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(30))  # sop | data_context | automation
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(400))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    author: Mapped[str] = mapped_column(String(40), default="template")  # template | model id
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Memory(Base):
    """Company memory: durable facts the AI learns (from feedback, investigations, or the user)."""

    __tablename__ = "memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40))  # feedback | agent | user
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    """Multi-turn copilot conversation. `messages` holds the exact API transcript (append-only)."""

    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    messages: Mapped[list[Any]] = mapped_column(default=list)
    turns: Mapped[list[Any]] = mapped_column(default=list)  # UI view: [{role, text, steps}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Briefing(Base):
    """Output of an autopilot run: what the AI triaged, investigated and proposed."""

    __tablename__ = "briefings"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    steps: Mapped[list[Any]] = mapped_column(default=list)
    engine: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def normalize_url(url: str) -> str:
    """Accept the plain postgres:// and postgresql:// forms that hosting providers hand out."""
    for plain in ("postgres://", "postgresql://"):
        if url.startswith(plain):
            return "postgresql+psycopg://" + url[len(plain):]
    return url


def init_db(url: str | None = None) -> sessionmaker[Session]:
    global _engine, _SessionLocal
    url = normalize_url(url or os.environ.get("DISTILL_DATABASE_URL", "sqlite:///./distill.db"))
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(_engine, expire_on_commit=False)
    return _SessionLocal


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()
