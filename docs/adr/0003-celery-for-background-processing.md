# ADR-0003: Celery for background processing

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

Document ingestion must never run inside an HTTP request. A 200-page PDF takes
minutes: extraction is CPU-bound, embedding is rate-limited network I/O, and any
stage can fail transiently. The upload endpoint must return `202 Accepted` with
a job id and let the work proceed out of band.

Ingestion is not one task — it is a **multi-stage pipeline with different failure
semantics per stage**:

```
validate → store → scan → extract → clean → chunk → embed → index → finalize
```

`extract` failing on a corrupt PDF is permanent (do not retry). `embed` failing
on a provider 429 is transient (retry with backoff and jitter). `index` failing
on a Qdrant restart is transient but must be idempotent on replay. The task
framework has to express that distinction, or we hand-roll it.

## Options considered

**Arq** — async-native, Redis-only, ~2k LOC, pairs beautifully with FastAPI's
async model. *Rejected:* small ecosystem, no workflow primitives (chains/groups),
thin operational tooling, and limited OpenTelemetry/Prometheus integration. In an
enterprise context we would be writing our own retry/DLQ/monitoring layer — which
is precisely the undifferentiated work we should not be doing.

**Dramatiq** — genuinely cleaner API than Celery, excellent middleware model,
good defaults (retries and dead-lettering are built in rather than bolted on).
*Rejected, narrowly:* smaller ecosystem, fewer managed-hosting and APM
integrations, and pipelines are less expressive than Celery's canvas for our DAG.
This is the closest runner-up; if the team already knew Dramatiq, it would be a
defensible choice.

**RQ** — simple, sync-only, no native scheduling, weak retry semantics.
*Rejected:* too little for a production pipeline.

**Celery** — **selected.**

## Decision

Use **Celery 5.x** with Redis as broker and Postgres as the durable job store.

Reasons, in order of weight:

1. **Canvas expresses our pipeline directly.** `chain()`, `group()`, `chord()`
   model the DAG natively — including the fan-out/fan-in of "embed 400 chunks in
   parallel batches, then index once all complete." Hand-rolling that
   coordination correctly is a distributed-systems problem we do not need to own.

2. **Per-task retry policy is first class.** `autoretry_for`, `retry_backoff`,
   `retry_jitter`, and `max_retries` are declared per task, so "permanent vs
   transient" lives next to the code that knows the difference.

3. **Queue routing gives us the pool-type split.** Our workloads need *different
   concurrency models*, and this is the subtle point:
   - `queue=cpu` → **prefork** pool. PDF parsing is CPU-bound; it would block an
     event loop and does not benefit from async.
   - `queue=io` → **gevent/thread** pool, high concurrency. Embedding is
     network-bound and provider-rate-limited.

   One framework, two pools, routed by queue. This is the standard production
   shape and neither Arq nor RQ offers it.

4. **Operational maturity.** Flower, `celery-exporter` for Prometheus, native
   OpenTelemetry instrumentation, mature broker semantics, and — importantly —
   a decade of documented failure modes. When it breaks at 2am, the answer is
   searchable.

## Explicitly accepted downsides

- **Celery is sync-first.** Our API is async; our workers largely are not. This
  is fine and intentional: the async-first rule exists to maximise I/O
  concurrency on the request path, where it pays. In a prefork worker doing CPU
  work, async buys nothing. Where a task *is* I/O-heavy (batch embedding), we run
  it on the `io` queue and use `asyncio.run()` at the task boundary to drive the
  async provider client, keeping one event loop per task invocation.
- Heavier and more configuration-surface than Dramatiq. Mitigated by keeping all
  configuration in one reviewed module (`app/workers/celery_app.py`) rather than
  scattered decorators.
- Redis as broker is at-least-once, not exactly-once. **This is why every task
  must be idempotent** — see ADR-0004 and the job state machine in M4. Treat
  duplicate delivery as a certainty, not an edge case.

## Non-negotiable constraints this creates

1. Every task is **idempotent** and keyed by a deterministic idempotency key
   (`document_id` + stage + content hash).
2. Task arguments are **ids and primitives only** — never ORM objects, never
   file contents. Payloads stay small; workers re-read from Postgres/MinIO.
3. Job state lives in **Postgres**, not in Celery's result backend. Celery
   result state is ephemeral and not queryable for our `GET /jobs/{id}` API,
   audit trail, or retention policy.

## Revisit when

- Pipeline throughput requires stream processing semantics (Kafka + consumer
  groups) rather than task queuing — i.e. continuous high-volume connector sync
  rather than bursty user uploads.
