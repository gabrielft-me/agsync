# Decisions

## D-013 — Runtime pinned to Node 22 LTS
- **Date:** 2026-03-04
- **Decision:** Pin the service runtime to Node 22 LTS.

## D-021 — Sessions live in Redis, not the primary database
- **Date:** 2026-03-05
- **Decision:** Session state moves out of Postgres and into Redis.

## D-021 — Responses use the JSON:API envelope
- **Date:** 2026-03-06
- **Decision:** Every endpoint returns a JSON:API envelope.

## D-022 — Background jobs run on the hosted queue
- **Date:** 2026-03-04
- **Decision:** JOB_BACKEND defaults to the hosted queue.
- **Rationale:** Retries and dead-lettering come for free.

## D-025 — Jobs move to the self-hosted worker (amends D-022)
- **Date:** 2026-03-05
- **Decision:** `JOB_BACKEND` default changes to the self-hosted worker, by
  explicit user direction (cost/latency).
- **Consequences:** Amends D-022's rationale. If task 18's throughput target
  fails on the worker, move back to the hosted queue.

## D-027 — Rate-limit headers are advisory (supersedes D-019)
- **Decision:** Headers inform clients; they never reject a request.
