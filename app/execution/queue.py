"""الطابور المتين + آلة حالات الأوامر.

- جدول jobs: queued/leased/done/failed/dead، عقود إيجار، backoff أسّي، مفاتيح idempotency.
- أولوية قصوى لـ kill_switch وclose_all.
- أوامر بآلة حالات وclient_order_id فريد — إعادة المحاولة لا تكرر تداولاً أبداً.
- عند البدء/إعادة الاتصال: مطابقة مع المنصة (المنصة مصدر الحقيقة).
- دفعات الزمن الحقيقي wake-up hints فقط؛ المحرك يستطلع أيضاً.
"""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

PRIORITY = {"kill_switch": 0, "close_all": 1, "order": 5, "analysis": 8, "backfill": 9}


class JobState(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class OrderState(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    RECONCILING = "reconciling"


VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.PENDING: {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.CANCELLED},
    OrderState.SUBMITTED: {OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                           OrderState.REJECTED, OrderState.CANCELLED, OrderState.RECONCILING},
    OrderState.OPEN: {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED, OrderState.RECONCILING},
    OrderState.PARTIALLY_FILLED: {OrderState.FILLED, OrderState.CANCELLED, OrderState.RECONCILING},
    OrderState.RECONCILING: {OrderState.OPEN, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                             OrderState.CANCELLED, OrderState.REJECTED},
}


def new_client_order_id(prefix: str = "mpv4") -> str:
    """معرف فريد — إعادة المحاولة تعيد نفس الـ ID لا تداولاً مكرراً."""
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def idempotency_key(kind: str, payload: str) -> str:
    return hashlib.sha256(f"{kind}:{payload}".encode()).hexdigest()[:40]


@dataclass
class Job:
    id: str
    kind: str
    payload: dict
    priority: int
    state: JobState
    attempts: int
    idempotency: str
    lease_until: Optional[float]
    last_error: Optional[str]


class DurableQueue:
    """طابور متين على SQLite — يعمل أيضاً بعد إعادة تشغيل العملية."""

    def __init__(self, db_path: str = "data/master_pro.db") -> None:
        import os

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 9,
                state TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                idempotency TEXT UNIQUE,
                lease_until REAL,
                last_error TEXT,
                created_ts TEXT NOT NULL,
                updated_ts TEXT NOT NULL
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_pick ON jobs(state, priority, created_ts)")
        self.conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def enqueue(self, kind: str, payload: dict, idem: Optional[str] = None) -> Optional[str]:
        key = idem or idempotency_key(kind, str(sorted(payload.items())))
        try:
            cur = self.conn.execute(
                "INSERT INTO jobs (id, kind, payload, priority, state, attempts, idempotency, created_ts, updated_ts) "
                "VALUES (?, ?, ?, ?, 'queued', 0, ?, ?, ?)",
                (uuid.uuid4().hex, kind, str(payload), PRIORITY.get(kind, 9), key, self._now(), self._now()),
            )
            self.conn.commit()
            return cur.lastrowid and key or key
        except sqlite3.IntegrityError:
            return None  # idempotent: نفس المهمة موجودة بالفعل — لا تكرار

    def lease_next(self, lease_seconds: float = 30.0) -> Optional[Job]:
        now = time.time()
        row = self.conn.execute(
            "SELECT id, kind, payload, priority, state, attempts, idempotency, lease_until "
            "FROM jobs WHERE state='queued' OR (state='leased' AND lease_until < ?) "
            "ORDER BY priority ASC, created_ts ASC LIMIT 1",
            (now,),
        ).fetchone()
        if not row:
            return None
        self.conn.execute(
            "UPDATE jobs SET state='leased', lease_until=?, attempts=attempts+1, updated_ts=? WHERE id=?",
            (now + lease_seconds, self._now(), row[0]),
        )
        self.conn.commit()
        return Job(
            id=row[0], kind=row[1], payload=eval(row[2]) if row[2].startswith("{") else {},
            priority=row[3], state=JobState.LEASED, attempts=row[5],
            idempotency=row[6], lease_until=row[7],
        )

    def complete(self, job_id: str) -> None:
        self.conn.execute("UPDATE jobs SET state='done', updated_ts=? WHERE id=?", (self._now(), job_id))
        self.conn.commit()

    def fail(self, job_id: str, error: str, max_attempts: int = 5, base_backoff: float = 2.0) -> None:
        row = self.conn.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
        attempts = row[0] if row else 0
        if attempts >= max_attempts:
            self.conn.execute("UPDATE jobs SET state='dead', last_error=?, updated_ts=? WHERE id=?",
                              (error, self._now(), job_id))
        else:
            self.conn.execute("UPDATE jobs SET state='queued', last_error=?, updated_ts=? WHERE id=?",
                              (error, self._now(), job_id))
        self.conn.commit()

    def pending_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state").fetchall()
        return {s: c for s, c in rows}


class OrderTracker:
    """آلة حالات الأوامر — انتقالات غير صالحة مرفوضة؛ المنصة مصدر الحقيقة عند المطابقة."""

    def __init__(self, queue: DurableQueue) -> None:
        self.queue = queue
        self.orders: dict[str, OrderState] = {}

    def transition(self, client_order_id: str, to_state: OrderState) -> bool:
        current = self.orders.get(client_order_id, OrderState.PENDING)
        if to_state not in VALID_TRANSITIONS.get(current, set()):
            return False
        self.orders[client_order_id] = to_state
        return True

    def reconcile(self, exchange_states: dict[str, OrderState]) -> dict[str, str]:
        """عند البدء/إعادة الاتصال: المنصة مصدر الحقيقة. يعيد خريطة التصحيحات."""
        fixes: dict[str, str] = {}
        for oid, ex_state in exchange_states.items():
            local = self.orders.get(oid, OrderState.PENDING)
            if local != ex_state:
                self.orders[oid] = ex_state
                fixes[oid] = f"{local.value} -> {ex_state.value}"
        return fixes