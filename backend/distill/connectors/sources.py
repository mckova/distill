"""Concrete connectors: file (CSV/JSON), REST API, SQL database, synthetic demo company."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Iterator

from .base import Connector, Message, Stream, infer_schema


def _coerce(value: str) -> Any:
    if value == "":
        return None
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def _dig(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    for part in path.split("."):
        obj = obj[part] if isinstance(obj, dict) else obj[int(part)]
    return obj


class FileConnector(Connector):
    """CSV or JSON (array or JSON-lines). Content may be inline (`content`) or on disk (`path`)."""

    kind = "file"

    @classmethod
    def spec(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["stream"],
            "properties": {
                "stream": {"type": "string", "title": "Stream name"},
                "format": {"type": "string", "enum": ["csv", "json"], "default": "csv"},
                "path": {"type": "string", "title": "File path"},
                "content": {"type": "string", "title": "Inline content"},
                "cursor_field": {"type": "string", "title": "Cursor field (incremental)"},
            },
        }

    def _text(self) -> str:
        if self.config.get("content") is not None:
            return self.config["content"]
        return Path(self.config["path"]).read_text(encoding="utf-8-sig")

    def _rows(self) -> list[dict[str, Any]]:
        text = self._text()
        fmt = self.config.get("format") or ("json" if text.lstrip()[:1] in "[{" else "csv")
        if fmt == "csv":
            return [{k.strip(): _coerce(v) for k, v in r.items() if k} for r in csv.DictReader(io.StringIO(text))]
        text = text.strip()
        if text.startswith("["):
            return json.loads(text)
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def check(self) -> tuple[bool, str]:
        try:
            n = len(self._rows())
            return True, f"{n} rows readable"
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            return False, str(exc)

    def discover(self) -> list[Stream]:
        rows = self._rows()
        return [Stream(self.config["stream"], infer_schema(rows), self.config.get("cursor_field") or None)]

    def read(self, streams: list[Stream], state: dict[str, Any]) -> Iterator[Message]:
        rows = self._rows()
        for stream in streams:
            yield from self.incremental(rows, stream, state)


class RestConnector(Connector):
    """Polls a JSON HTTP endpoint. Supports bearer auth, a records path and page-number pagination."""

    kind = "rest"

    @classmethod
    def spec(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["stream", "url"],
            "properties": {
                "stream": {"type": "string"},
                "url": {"type": "string", "format": "uri"},
                "records_path": {"type": "string", "description": "dot path to the array, e.g. data.items"},
                "bearer_token": {"type": "string", "secret": True},
                "page_param": {"type": "string", "description": "query param for page number"},
                "max_pages": {"type": "integer", "default": 10},
                "cursor_field": {"type": "string"},
            },
        }

    def _fetch(self) -> list[dict[str, Any]]:
        import httpx

        headers = {"Accept": "application/json"}
        if self.config.get("bearer_token"):
            headers["Authorization"] = f"Bearer {self.config['bearer_token']}"
        rows: list[dict[str, Any]] = []
        page_param = self.config.get("page_param")
        pages = int(self.config.get("max_pages") or 10) if page_param else 1
        with httpx.Client(timeout=20) as client:
            for page in range(1, pages + 1):
                params = {page_param: page} if page_param else None
                resp = client.get(self.config["url"], headers=headers, params=params)
                resp.raise_for_status()
                batch = _dig(resp.json(), self.config.get("records_path"))
                if not batch:
                    break
                rows.extend(batch)
        return rows

    def check(self) -> tuple[bool, str]:
        try:
            return True, f"{len(self._fetch())} records reachable"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def discover(self) -> list[Stream]:
        return [Stream(self.config["stream"], infer_schema(self._fetch()), self.config.get("cursor_field") or None)]

    def read(self, streams: list[Stream], state: dict[str, Any]) -> Iterator[Message]:
        rows = self._fetch()
        for stream in streams:
            yield from self.incremental(rows, stream, state)


class SqlConnector(Connector):
    """Any SQLAlchemy URL + one query per stream (read-only use is the operator's responsibility)."""

    kind = "sql"

    @classmethod
    def spec(cls) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["url", "queries"],
            "properties": {
                "url": {"type": "string", "secret": True},
                "queries": {"type": "object", "additionalProperties": {"type": "string"},
                            "description": "stream name -> SELECT statement"},
                "cursor_fields": {"type": "object", "additionalProperties": {"type": "string"}},
            },
        }

    def _query(self, sql: str) -> list[dict[str, Any]]:
        from sqlalchemy import create_engine, text

        engine = create_engine(self.config["url"])
        with engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql))]

    def check(self) -> tuple[bool, str]:
        try:
            for sql in self.config["queries"].values():
                self._query(f"SELECT * FROM ({sql}) AS q LIMIT 1")
            return True, "all queries valid"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def discover(self) -> list[Stream]:
        cursors = self.config.get("cursor_fields") or {}
        return [Stream(name, infer_schema(self._query(f"SELECT * FROM ({sql}) AS q LIMIT 50")), cursors.get(name))
                for name, sql in self.config["queries"].items()]

    def read(self, streams: list[Stream], state: dict[str, Any]) -> Iterator[Message]:
        for stream in streams:
            rows = self._query(self.config["queries"][stream.name])
            rows = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]
            yield from self.incremental(rows, stream, state)


class DemoConnector(Connector):
    """A synthetic mid-size company with planted patterns (see distill.demo)."""

    kind = "demo"

    @classmethod
    def spec(cls) -> dict[str, Any]:
        return {"type": "object", "properties": {"seed": {"type": "integer", "default": 7},
                                                 "days": {"type": "integer", "default": 120}}}

    def _data(self) -> dict[str, list[dict[str, Any]]]:
        from ..demo import generate_company

        return generate_company(seed=int(self.config.get("seed", 7)), days=int(self.config.get("days", 120)))

    def check(self) -> tuple[bool, str]:
        return True, "synthetic data generator"

    def discover(self) -> list[Stream]:
        cursors = {"orders": "created_at", "support_events": "timestamp"}
        return [Stream(name, infer_schema(rows), cursors.get(name)) for name, rows in self._data().items()]

    def read(self, streams: list[Stream], state: dict[str, Any]) -> Iterator[Message]:
        data = self._data()
        for stream in streams:
            yield Message.log(f"generating {stream.name}")
            yield from self.incremental(data[stream.name], stream, state)


class WebhookConnector(Connector):
    """Push-based: records arrive via POST /api/sources/{id}/events. `read` is a no-op."""

    kind = "webhook"

    @classmethod
    def spec(cls) -> dict[str, Any]:
        return {"type": "object", "required": ["stream"], "properties": {"stream": {"type": "string"}}}

    def check(self) -> tuple[bool, str]:
        return True, "listening"

    def discover(self) -> list[Stream]:
        return [Stream(self.config["stream"])]

    def read(self, streams: list[Stream], state: dict[str, Any]) -> Iterator[Message]:
        return iter(())


REGISTRY: dict[str, type[Connector]] = {
    c.kind: c for c in (FileConnector, RestConnector, SqlConnector, DemoConnector, WebhookConnector)
}


def build(kind: str, config: dict[str, Any]) -> Connector:
    if kind not in REGISTRY:
        raise ValueError(f"unknown connector kind: {kind}")
    return REGISTRY[kind](config)
