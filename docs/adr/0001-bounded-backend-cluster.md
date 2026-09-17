# ADR-0001: Bounded Backend replica coordination

- Status: accepted for an opt-in test/deployment profile
- Date: 2026-09-15

## Context

Backend card, lesson, review and dialogue updates are read-modify-write operations.
Their current `asyncio.Lock` dictionaries and the WebSocket `active` set protect one
Python process only. Running two Backend processes without another coordination
mechanism can lose updates, issue duplicate lesson cards, run duplicate reviews, or
accept two Voice control sockets for the same session.

The project rules prohibit adding Redis, a broker or a new business microservice
without a separate architecture decision. PostgreSQL is already the deployed source
of truth and `psycopg` is already a Backend dependency.

## Decision

The opt-in cluster profile uses session-level PostgreSQL advisory locks on the same
database session that performs protected writes. This is a fencing invariant: if
the session is lost, PostgreSQL releases its locks and the previous owner also loses
its write path, so it cannot continue on a separate live application connection.
Lock keys are stable signed 64-bit hashes of a namespace and resource ID. A local
async lock is retained because advisory locks are reentrant within one PostgreSQL
session. Separate namespaces preserve
the current separation between workspace, lesson, review, dialogue and Voice socket
lifecycles. Acquisition is bounded; timeout or database loss returns unavailable
instead of proceeding without serialization.

A small HAProxy deployment component health-checks and balances two Backend
containers. WebSocket upgrade uses normal HTTP mode and a one-hour tunnel timeout.
No sticky session is required because persisted dialogue state and event-id
deduplication live in PostgreSQL. One Voice WebSocket lease is held for the entire
connection. If a Backend process dies, PostgreSQL closes its lock connections and
Voice's existing durable outbox reconnects through the load balancer.

SQLite remains strictly single-process. Enabling cluster mode without a PostgreSQL
URL fails at startup. Local locks remain the default outside cluster mode.

## Consequences and failure semantics

- A Backend replica failure removes it from new load-balanced requests after the
  health-check interval. An in-flight HTTP response may fail; only naturally safe
  reads or mutations carrying their existing idempotency key may be retried.
- A Voice socket drops when its replica fails. Voice reconnects with its bounded
  retry policy and resends its unacknowledged event. The database event ID prevents
  duplicate dialogue effects once the distributed Engine lock is integrated.
- A lock wait exceeding the configured bound fails with HTTP 503 (or WebSocket 1013)
  and does not execute the mutation unlocked.
- Loss of PostgreSQL makes coordinated mutations unavailable. The included single
  PostgreSQL container is still a single point of failure.
- The application does not reconnect its database session transparently. This is
  intentional for fencing; a process with a lost session must fail health checks and
  restart before it can accept new coordinated writes.
- HAProxy is an additional deployment component, not a business microservice. In
  this Compose profile it is also a single ingress point; production ingress HA is
  an infrastructure responsibility outside this repository.

## Rejected alternatives

- Multiple replicas with process-local locks: unsafe for read-modify-write state.
- SQLite on a shared volume: unsupported and unsafe for this topology.
- Redis/etcd/broker locks: unnecessary new infrastructure for the bounded profile.
- Claiming PostgreSQL standby promotion as automatic HA: rejected without quorum,
  fencing, tested recovery-point objectives and at least three failure-domain-aware
  voting members (or an equivalent managed HA service).
