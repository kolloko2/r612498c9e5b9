"""Cross-replica coordination for the bounded Backend cluster profile.

PostgreSQL advisory locks are held on the same database session used by protected
writes. Losing that session releases the lease server-side and also prevents the
old owner from writing through a still-live connection. This coordinates Backend
processes; it is not PostgreSQL HA.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import socket
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Callable


_UUID_PATH = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
_FINE_GRAINED_MUTATIONS = (
    ("PUT", re.compile(rf"^/api/v1/student/sessions/{_UUID_PATH}/card$")),
    ("POST", re.compile(rf"^/api/v1/student/sessions/{_UUID_PATH}/(?:messages|services|notifications|processed|links|call|call/recover|finish)$")),
    ("POST", re.compile(rf"^/api/v1/instructor/sessions/{_UUID_PATH}/(?:finish|feedback)$")),
    ("POST", re.compile(rf"^/api/v1/student/lessons/{_UUID_PATH}/next$")),
    ("POST", re.compile(rf"^/api/v1/instructor/lessons/{_UUID_PATH}/(?:start|finish)$")),
)


def uses_fine_grained_mutation_lease(method: str, path: str) -> bool:
    """True only for audited routes that hold their own aggregate lease.

    Keep this an exact allowlist: a new endpoint remains under the global guard
    until its complete read-modify-write scope has a reviewed distributed lease.
    """
    return any(method == allowed and pattern.fullmatch(path)
               for allowed, pattern in _FINE_GRAINED_MUTATIONS)


class ClusterConfigurationError(RuntimeError):
    pass


class ClusterUnavailable(RuntimeError):
    pass


class LockUnavailable(RuntimeError):
    pass


def _boolean(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def advisory_key(namespace: str, resource: str) -> int:
    """Return a stable signed 64-bit key accepted by PostgreSQL."""
    digest = hashlib.blake2b(f"trainer112:{namespace}:{resource}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@dataclass(frozen=True)
class ClusterConfig:
    enabled: bool
    database_url: str
    instance_id: str
    lock_timeout_s: float = 8.0
    poll_interval_s: float = 0.05

    @classmethod
    def from_environment(cls, database_url: str | None = None) -> "ClusterConfig":
        enabled = _boolean(os.getenv("CLUSTER_ENABLED"))
        target = database_url or os.getenv("DATABASE_URL", "")
        timeout = float(os.getenv("CLUSTER_LOCK_TIMEOUT_SECONDS", "8"))
        poll = float(os.getenv("CLUSTER_LOCK_POLL_SECONDS", "0.05"))
        if not 0.1 <= timeout <= 60:
            raise ClusterConfigurationError("CLUSTER_LOCK_TIMEOUT_SECONDS must be between 0.1 and 60")
        if not 0.01 <= poll <= 1:
            raise ClusterConfigurationError("CLUSTER_LOCK_POLL_SECONDS must be between 0.01 and 1")
        if enabled and not target.startswith(("postgresql://", "postgres://")):
            raise ClusterConfigurationError("CLUSTER_ENABLED requires a shared PostgreSQL DATABASE_URL")
        return cls(enabled, target, os.getenv("CLUSTER_INSTANCE_ID", socket.gethostname())[:100], timeout, poll)


class Lease:
    def __init__(self, release: Callable[[], None] | None, namespace: str, resource: str,
                 *, local_lock: asyncio.Lock | None = None):
        self._release = release
        self.namespace = namespace
        self.resource = resource
        self._local_lock = local_lock
        self._released = False

    def release_sync(self) -> None:
        if not self._released:
            self._released = True
            if self._release:
                self._release()

    async def release(self) -> None:
        if self._released:
            return
        try:
            if self._release:
                task = asyncio.create_task(asyncio.to_thread(self.release_sync))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # Do not release the local guard until the database unlock
                    # definitively completed (or failed and fenced this process).
                    await task
                    raise
            else:
                self._released = True
        finally:
            if self._local_lock and self._local_lock.locked():
                self._local_lock.release()

    async def __aenter__(self) -> "Lease":
        return self

    async def __aexit__(self, _type, _value, _traceback) -> None:
        await self.release()


class Coordinator:
    """Acquire bounded local or PostgreSQL-backed named leases."""

    def __init__(self, config: ClusterConfig, database=None):
        self.config = config
        self._database = database
        self._local: dict[int, asyncio.Lock] = {}

    @classmethod
    def from_database(cls, database) -> "Coordinator":
        return cls(ClusterConfig.from_environment(getattr(database, "target", "")), database)

    def status(self) -> dict:
        return {
            "enabled": self.config.enabled,
            "instance_id": self.config.instance_id,
            "coordination": "postgres_advisory_locks" if self.config.enabled else "process_local",
            "backend_replica_failover": self.config.enabled,
            "database_automatic_failover": False,
        }

    def _acquire_postgres(self, key: int, namespace: str, resource: str, timeout_s: float,
                          cancelled: threading.Event) -> Lease | None:
        if self._database is None or not getattr(self._database, "is_postgres", False):
            raise ClusterConfigurationError("Cluster coordinator requires the application's PostgreSQL connection")
        deadline = time.monotonic() + timeout_s
        try:
            while True:
                if cancelled.is_set():
                    return None
                # Deliberately use the same session as protected application writes.
                # If this session is lost, an old lease holder cannot write through
                # another live connection after PostgreSQL releases its lock.
                row = self._database.execute("SELECT pg_try_advisory_lock(?)", (key,)).fetchone()
                if row and row[0]:
                    def release() -> None:
                        try:
                            self._database.execute("SELECT pg_advisory_unlock(?)", (key,))
                        except Exception as exc:
                            raise ClusterUnavailable("PostgreSQL coordination was lost") from exc

                    return Lease(release, namespace, resource)
                if time.monotonic() >= deadline:
                    return None
                time.sleep(min(self.config.poll_interval_s, max(0, deadline - time.monotonic())))
        except Exception as exc:
            raise ClusterUnavailable("PostgreSQL coordination was interrupted") from exc

    async def acquire(self, namespace: str, resource: str, timeout_s: float | None = None) -> Lease:
        if not namespace or not resource:
            raise ValueError("Lock namespace and resource are required")
        timeout = self.config.lock_timeout_s if timeout_s is None else timeout_s
        if not 0 <= timeout <= 60:
            raise ValueError("Lock timeout must be between 0 and 60 seconds")
        key = advisory_key(namespace, resource)
        started = time.monotonic()
        lock = self._local.setdefault(key, asyncio.Lock())
        if timeout == 0:
            if lock.locked():
                local_acquired = False
            else:
                await lock.acquire()
                local_acquired = True
        else:
            try:
                await asyncio.wait_for(lock.acquire(), timeout)
            except TimeoutError:
                local_acquired = False
            else:
                local_acquired = True
        if not local_acquired:
            raise LockUnavailable(f"Timed out waiting for {namespace} lease")
        if self.config.enabled:
            cancelled = threading.Event()
            task = asyncio.create_task(asyncio.to_thread(
                self._acquire_postgres, key, namespace, resource,
                max(0, timeout - (time.monotonic() - started)), cancelled
            ))
            try:
                lease = await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled.set()
                lease = await task
                if lease is not None:
                    lease._local_lock = lock
                    await lease.release()
                else:
                    lock.release()
                raise
            except Exception:
                lock.release()
                raise
            if lease is None:
                lock.release()
                raise LockUnavailable(f"Timed out waiting for {namespace} lease")
            lease._local_lock = lock
        else:
            lease = Lease(None, namespace, resource, local_lock=lock)
        return lease

    @asynccontextmanager
    async def hold(self, namespace: str, resource: str, timeout_s: float | None = None):
        lease = await self.acquire(namespace, resource, timeout_s)
        try:
            yield lease
        finally:
            await lease.release()


def install_global_mutation_guard(app, coordinator: Coordinator) -> None:
    """Serialize mutations except exact routes with audited fine-grained leases.

    This intentionally bounds write throughput to one request at a time across the
    Backend cluster. Reads still scale. WebSockets do not pass through this guard and
    require their own lifetime/session leases.
    """
    if not coordinator.config.enabled:
        return

    @app.middleware("http")
    async def cluster_mutation_guard(request, call_next):
        if (request.method not in {"POST", "PUT", "PATCH", "DELETE"}
                or uses_fine_grained_mutation_lease(request.method, request.url.path)):
            return await call_next(request)
        try:
            async with coordinator.hold("http-mutation", "global"):
                return await call_next(request)
        except (LockUnavailable, ClusterUnavailable):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Cluster mutation coordination is unavailable"}, status_code=503)


class CoordinatedEngine:
    """Apply a cross-replica dialogue-session lease around an existing Engine."""

    def __init__(self, engine, coordinator: Coordinator):
        self._engine = engine
        self._coordinator = coordinator

    def __getattr__(self, name):
        return getattr(self._engine, name)

    async def handle(self, sid, event):
        async with self._coordinator.hold("dialogue-session", str(sid)):
            return await self._engine.handle(sid, event)


def coordinate_engine(engine, coordinator: Coordinator):
    return CoordinatedEngine(engine, coordinator)
