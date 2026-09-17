import asyncio
import threading

import pytest

from cluster import (
    ClusterConfig,
    ClusterUnavailable,
    Coordinator,
    LockUnavailable,
    advisory_key,
    coordinate_engine,
    uses_fine_grained_mutation_lease,
)


class Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class FakePostgres:
    def __init__(self):
        self.guard = threading.Lock()
        self.owners = {}

    def database(self, identity):
        return FakeDatabase(self, identity)


class FakeDatabase:
    is_postgres = True
    target = "postgresql://synthetic-test"

    def __init__(self, server, identity):
        self.server, self.identity, self.failed = server, identity, False

    def execute(self, sql, parameters=()):
        if self.failed:
            raise ConnectionError("database session lost")
        key = parameters[0] if parameters else None
        with self.server.guard:
            if "pg_try_advisory_lock" in sql:
                owner = self.server.owners.get(key)
                if owner is None:
                    self.server.owners[key] = self.identity
                    return Result(True)
                return Result(owner == self.identity)
            if "pg_advisory_unlock" in sql:
                if self.server.owners.get(key) == self.identity:
                    del self.server.owners[key]
                    return Result(True)
                return Result(False)
            return Result(True)

    def disconnect(self):
        with self.server.guard:
            self.failed = True
            self.server.owners = {key: owner for key, owner in self.server.owners.items()
                                  if owner != self.identity}


def config(enabled):
    return ClusterConfig(enabled, "postgresql://synthetic-test" if enabled else "", "test", 0.05, 0.005)


@pytest.mark.asyncio
async def test_local_lock_is_bounded_and_reusable():
    coordinator = Coordinator(config(False))
    first = await coordinator.acquire("workspace-session", "one")
    with pytest.raises(LockUnavailable):
        await coordinator.acquire("workspace-session", "one", 0)
    other = await coordinator.acquire("workspace-session", "two", 0)
    await other.release()
    await first.release()
    again = await coordinator.acquire("workspace-session", "one", 0)
    await again.release()


@pytest.mark.asyncio
async def test_lost_lock_session_fences_old_owner_and_allows_takeover():
    server = FakePostgres()
    old_db, new_db = server.database("old"), server.database("new")
    old = Coordinator(config(True), old_db)
    new = Coordinator(config(True), new_db)
    lease = await old.acquire("workspace-session", "card")
    with pytest.raises(LockUnavailable):
        await new.acquire("workspace-session", "card", 0.01)

    old_db.disconnect()
    with pytest.raises(ConnectionError):
        old_db.execute("UPDATE workspace")
    with pytest.raises(ClusterUnavailable):
        await lease.release()

    takeover = await new.acquire("workspace-session", "card", 0)
    await takeover.release()


@pytest.mark.asyncio
async def test_engine_wrapper_serializes_same_session_across_replicas():
    server = FakePostgres()
    active = maximum = 0

    class Engine:
        async def handle(self, sid, event):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.02)
            active -= 1
            return event

    first = coordinate_engine(Engine(), Coordinator(config(True), server.database("one")))
    second = coordinate_engine(Engine(), Coordinator(config(True), server.database("two")))
    await asyncio.gather(first.handle("same", {"n": 1}), second.handle("same", {"n": 2}))
    assert maximum == 1
    assert advisory_key("dialogue-session", "same") != advisory_key("workspace-session", "same")


@pytest.mark.asyncio
async def test_cancelled_wait_does_not_orphan_local_or_database_lease():
    server = FakePostgres()
    holder = Coordinator(config(True), server.database("holder"))
    waiter = Coordinator(config(True), server.database("waiter"))
    first = await holder.acquire("workspace-session", "cancel")
    pending = asyncio.create_task(waiter.acquire("workspace-session", "cancel"))
    await asyncio.sleep(0.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await first.release()
    replacement = await waiter.acquire("workspace-session", "cancel", 0)
    await replacement.release()


def test_fine_grained_http_allowlist_is_exact_and_method_aware():
    sid = "123e4567-e89b-42d3-a456-426614174000"
    allowed = {
        ("PUT", f"/api/v1/student/sessions/{sid}/card"),
        ("POST", f"/api/v1/student/sessions/{sid}/messages"),
        ("POST", f"/api/v1/student/sessions/{sid}/finish"),
        ("POST", f"/api/v1/student/sessions/{sid}/call/recover"),
        ("POST", f"/api/v1/instructor/sessions/{sid}/feedback"),
        ("POST", f"/api/v1/instructor/sessions/{sid}/finish"),
        ("POST", f"/api/v1/student/lessons/{sid}/next"),
        ("POST", f"/api/v1/instructor/lessons/{sid}/start"),
        ("POST", f"/api/v1/instructor/lessons/{sid}/finish"),
    }
    assert all(uses_fine_grained_mutation_lease(method, path) for method, path in allowed)

    guarded = {
        ("POST", "/api/v1/student/sessions"),
        ("POST", f"/api/v1/student/sessions/{sid}/ai-review"),
        ("PUT", f"/api/v1/instructor/sessions/{sid}/feedback"),
        ("POST", f"/api/v1/student/sessions/{sid}/messages/extra"),
        ("POST", "/api/v1/student/sessions/not-a-uuid/messages"),
        ("POST", "/api/v1/instructor/lessons"),
        ("PATCH", f"/api/v1/student/sessions/{sid}/card"),
    }
    assert not any(uses_fine_grained_mutation_lease(method, path) for method, path in guarded)
