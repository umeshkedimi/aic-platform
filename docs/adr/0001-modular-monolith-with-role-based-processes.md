# ADR-0001: Modular monolith, split by process role — not microservices

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Umesh (owner), Staff Eng review

## Context

An Enterprise RAG platform has several workloads with genuinely different runtime
profiles:

| Workload | Profile | Scaling trigger |
|---|---|---|
| HTTP API | I/O-bound, latency-sensitive, spiky | concurrent requests |
| Document extraction | CPU-bound (PDF parsing), minutes-long | queue depth |
| Embedding | network I/O + rate-limited by provider | queue depth, provider quota |
| Chat/generation | long-lived streaming connections | concurrent streams |

The naive reading is "different scaling profiles ⇒ microservices." That reasoning
is wrong at this stage, and it is one of the most expensive mistakes a team can
make in year one.

## Decision

We build **one Python package (`app/`) deployed as one container image, run as
several process roles**:

- `api` — Uvicorn serving FastAPI
- `worker-cpu` — Celery prefork pool (extraction, chunking)
- `worker-io` — Celery gevent/thread pool (embedding, external calls)
- `beat` — Celery scheduler (reconciliation, retention, eval runs)

Same image, different entrypoint command. Each role scales independently in
Kubernetes as its own Deployment with its own HPA.

## Why this over microservices

**Independent scaling does not require independent deployment.** Four
Deployments from one image already give us independent replica counts, resource
limits, and autoscaling. That is 90% of the benefit of microservices for 5% of
the cost.

**Microservices trade in-process function calls for network calls.** A network
call introduces partial failure, retries, timeouts, serialisation, versioned
contracts, and distributed tracing as a *requirement* rather than a nicety. You
pay that tax on every boundary, forever. You should only pay it where you get
something back.

**We do not yet know where the seams are.** Service boundaries are extremely
expensive to move once teams and deploy pipelines form around them. The honest
position in month one is that our bounded contexts are a hypothesis. A modular
monolith lets us move a boundary with a refactor instead of a migration project.

**One team.** Conway's Law cuts both ways: microservices pay off when they let
independent teams deploy independently. With one team, they mostly add ceremony.

## What we do instead — earning the right to split later

The point of a *modular* monolith is that the split stays cheap. We enforce:

1. **Ports (`app/domain/ports.py`)** — every external system sits behind a
   `Protocol`. A port is already a service boundary that happens to be a
   function call today.
2. **Dependency direction** — `domain/` imports nothing from `app.*`. Layers
   point inward. Enforced in CI by an import-linter contract, not by good
   intentions.
3. **The queue is already a network boundary.** API and workers communicate via
   Celery/Redis, never by importing each other's code. That seam is real from
   day one and is where a split would actually happen first.

## Consequences

**Accepted costs**

- One image is larger than a set of purpose-built images. Mitigated by making
  heavy optional deps (`torch`, `sentence-transformers`) an extra, built into a
  separate `worker-local-embed` image variant only when self-hosted embedding is
  enabled.
- A dependency upgrade affects all roles simultaneously. Mitigated by rolling
  deploys with health gating per role.
- Requires discipline: nothing stops a developer importing across layers except
  the CI contract. That contract is therefore non-negotiable.

**Benefits**

- One test suite, one migration path, one release.
- Refactoring across "service" boundaries is a rename, not a coordination
  problem.
- Local development is `docker compose up`, not a service mesh.

## Revisit when

- A single workload's release cadence is genuinely blocked by another's.
- A workload needs a different language or runtime (e.g. a Rust reranker).
- Team count exceeds ~2 and deploy contention becomes measurable.

At that point the split target is already obvious: it follows a port.
