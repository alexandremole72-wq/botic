"""Where runs are kept: a local SQLite file by default, PostgreSQL when
BOTIC_DB_URL (or DATABASE_URL) points to one. One table, bench_runs; it does
not touch the tables of the existing demo app."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (JSON, Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine,
                        delete, desc, select)

metadata = MetaData()

runs = Table(
    "bench_runs", metadata,
    Column("id", String(32), primary_key=True),
    Column("batch_id", String(32), index=True),
    Column("created_at", DateTime(timezone=True), index=True),
    Column("agent", String(160), index=True),
    Column("provider", String(20)),
    Column("model", String(120)),
    Column("policy", String(20)),
    Column("source", String(20)),  # runner | mcp | web
    Column("scenario_id", String(8), index=True),
    Column("mission_id", String(4)),
    Column("variant", Integer),
    Column("trap", String(40)),
    Column("outcome", String(10), index=True),  # pass | safe | fail | critical | error
    Column("passed", Boolean),
    Column("money_at_risk_cents", Integer),
    Column("spent_cents", Integer),
    Column("tool_calls", Integer),
    Column("model_calls", Integer),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("duration_ms", Integer),
    Column("task", Text),
    Column("summary", Text),
    Column("final_report", Text),
    Column("error", Text),
    Column("verdict", JSON),
    Column("events", JSON),
)

LIGHT_COLUMNS = [c for c in runs.c if c.name not in ("events", "verdict", "task")]


def db_url(explicit: Optional[str] = None) -> str:
    url = explicit or os.environ.get("BOTIC_DB_URL") or os.environ.get("DATABASE_URL") or "sqlite:///botic_bench.db"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


class RunStore:
    def __init__(self, url: Optional[str] = None):
        self.url = db_url(url)
        kwargs: Dict[str, Any] = {"pool_pre_ping": True}
        if self.url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(self.url, **kwargs)
        metadata.create_all(self.engine)

    def save(self, record: Dict[str, Any]) -> str:
        row = {k: record.get(k) for k in runs.c.keys()}
        row["created_at"] = row.get("created_at") or datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            conn.execute(runs.insert().values(**row))
        return row["id"]

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        return dict(row) if row else None

    def list(self, limit: int = 500, agent: Optional[str] = None, trap: Optional[str] = None,
             outcome: Optional[str] = None, batch_id: Optional[str] = None,
             include_scripted: bool = True) -> List[Dict[str, Any]]:
        q = select(*LIGHT_COLUMNS).order_by(desc(runs.c.created_at)).limit(limit)
        if agent:
            q = q.where(runs.c.agent == agent)
        if trap:
            q = q.where(runs.c.trap == trap)
        if outcome:
            q = q.where(runs.c.outcome == outcome)
        if batch_id:
            q = q.where(runs.c.batch_id == batch_id)
        if not include_scripted:
            q = q.where(runs.c.provider != "scripted")
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(q).mappings()]

    def delete_batch(self, batch_id: str) -> int:
        with self.engine.begin() as conn:
            return conn.execute(delete(runs).where(runs.c.batch_id == batch_id)).rowcount or 0
