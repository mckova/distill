"""Connector protocol.

A connector is four verbs (spec / check / discover / read) and `read`
yields a stream of typed messages (RECORD, STATE, LOG). The engine never knows connector internals; it only
consumes messages. STATE messages carry an incremental cursor per stream so
the next sync resumes where the last one stopped. Connectors run in-process
as plain Python classes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

MessageType = Literal["RECORD", "STATE", "LOG"]


@dataclass
class Stream:
    name: str
    json_schema: dict[str, Any] = field(default_factory=dict)
    cursor_field: str | None = None  # enables incremental sync

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "json_schema": self.json_schema, "cursor_field": self.cursor_field}


@dataclass
class Message:
    type: MessageType
    stream: str | None = None
    data: dict[str, Any] | None = None
    emitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def record(cls, stream: str, data: dict[str, Any]) -> "Message":
        return cls("RECORD", stream, data)

    @classmethod
    def state(cls, stream: str, cursor: Any) -> "Message":
        return cls("STATE", stream, {"cursor": cursor})

    @classmethod
    def log(cls, text: str) -> "Message":
        return cls("LOG", None, {"message": text})


def infer_schema(rows: list[dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for row in rows[:200]:
        for k, v in row.items():
            t = "number" if isinstance(v, (int, float)) and not isinstance(v, bool) else (
                "boolean" if isinstance(v, bool) else "string")
            props.setdefault(k, {"type": t})
    return {"type": "object", "properties": props}


class Connector(ABC):
    kind: str = "base"

    @classmethod
    @abstractmethod
    def spec(cls) -> dict[str, Any]:
        """JSON schema of the connector config (rendered as a form in the UI)."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @abstractmethod
    def check(self) -> tuple[bool, str]:
        ...

    @abstractmethod
    def discover(self) -> list[Stream]:
        ...

    @abstractmethod
    def read(self, streams: list[Stream], state: dict[str, Any]) -> Iterator[Message]:
        ...

    @staticmethod
    def incremental(rows: list[dict[str, Any]], stream: Stream, state: dict[str, Any]) -> Iterator[Message]:
        """Shared incremental logic: emit rows past the saved cursor, then a STATE message."""
        cursor_field = stream.cursor_field
        last = state.get(stream.name) if cursor_field else None
        max_cursor = last
        for row in rows:
            if cursor_field:
                value = row.get(cursor_field)
                if value is None:
                    continue
                if last is not None and str(value) <= str(last):
                    continue
                if max_cursor is None or str(value) > str(max_cursor):
                    max_cursor = value
            yield Message.record(stream.name, row)
        if cursor_field and max_cursor is not None:
            yield Message.state(stream.name, max_cursor)
