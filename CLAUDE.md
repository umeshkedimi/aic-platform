# Enterprise RAG Platform — working agreement

Multi-tenant production RAG platform. **Every session starts by reading
[`docs/ROADMAP.md`](docs/ROADMAP.md)** — it holds the milestone plan, the current
status, and the per-session protocol.

## Current state

- **Milestone:** M1 (Domain model & persistence) — not started
- **Last completed:** M0 (Foundation) at `398ae8e`

## Remaining work

One task = one milestone = one session, worked in this order. Full detail
(architecture, files, concepts, testing, Done-when gate) is in
[`docs/ROADMAP.md`](docs/ROADMAP.md) §3 — this table is a tracker, not the spec.

| # | Task | Gate | Status |
|---|---|---|---|
| M1 | Domain model & persistence | migrations apply; tenant-leak suite passes | not started |
| M2 | AuthN / AuthZ / audit | full auth flow + RBAC tested | blocked on M1 |
| M3 | Object storage & upload | `202` + job row + object in MinIO | blocked on M2 |
| M4 | Async backbone (Celery) | no-op pipeline runs end to end | blocked on M3 |
| M5 | Extraction & chunking | 5 formats → chunks in Postgres | blocked on M4 |
| M6 | Embeddings | 2+ providers pass one contract suite; cache hit measured | blocked on M5 |
| M7 | Vector store & indexing | Qdrant + pgvector pass the same suite | blocked on M6 |
| M8 | Retrieval pipeline | `POST /search` with hybrid + rerank | blocked on M7 |
| M9 | LLM layer & RAG chain | streaming cited answers | blocked on M8 |
| M10 | Observability | one trace spans API → worker → LLM | blocked on M9 |
| M11 | Evaluation & quality gates | eval gate blocks CI on regression | blocked on M10 |
| M12 | Kubernetes, Helm, CI/CD, hardening | deployable chart + full pipeline | blocked on M11 |

Milestones marked ⚠️ in the roadmap table (M1, M5, M7, M8, M9, M11, M12) are
large enough to span two sessions — stop at the documented split point rather
than compressing the work.

Each session commits incrementally as pieces land (e.g. models → migration →
repositories → tests), not as one squashed commit at the end. Update this
table's **Status** column and the **Current state** block above at the end of
every session.

## Working mode

This is a mentored build. The user is a strong Python/FastAPI/SQLAlchemy/Postgres
engineer learning production RAG, LangChain, and AI infrastructure. Therefore:

1. Architecture and **why** before code. Name the alternatives and the tradeoff.
2. Never justify a decision with "because LangChain/the framework does it."
3. No thousand-line dumps. Reviewable increments, explained after they land.
4. Production quality only — no TODOs unless explicitly agreed, no tutorial
   shortcuts, no placeholder error handling.
5. Challenge the user's assumptions. Review their code critically.
6. One milestone per session, committed incrementally (multiple commits per
   session, not one dump at the end); end with the review ritual in ROADMAP §4.

## Non-negotiable architecture rules

These come from the ADRs in `docs/adr/`. Violating one is a review blocker.

| Rule | Source |
|---|---|
| `app/domain/` imports nothing from `app.*`. Layers point inward, enforced by import-linter. | ADR-0001 |
| `langchain` must not be imported in `domain/`, `services/`, `repositories/`, or `models/`. Convert at the adapter boundary. | ADR-0002 |
| Every Celery task is idempotent. Task args are ids and primitives — never ORM objects or file contents. | ADR-0003 |
| Job state lives in Postgres, not Celery's result backend. | ADR-0003 |
| Chunk text is owned by Postgres. The vector store holds vectors + filterable scalars only; search returns ids and scores, never text. | ADR-0004 |
| Write order is always Postgres commit → vector upsert. Never the reverse. | ADR-0004 |
| `organization_id` on every tenant-owned row. Tenant scoping is enforced in the repository base class and is a required argument on the `VectorStore` port. | ADR-0005 |
| No document processing inside an HTTP request. Upload returns `202` + job id. | ADR-0003 |
| Pydantic schemas are never SQLAlchemy models. Wire shape ≠ storage shape. | ROADMAP §2.2 |
| No `utils/`. Everything has a real home. | ROADMAP §2.2 |
| Config is read once into a frozen `Settings` object and injected. No `os.getenv` at point of use. | M0 |

## Stack

Python 3.13 · uv · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) · Alembic ·
Postgres · Redis · Qdrant (+ pgvector adapter) · MinIO · Celery · LangChain
(edges only) · OpenTelemetry · Prometheus/Grafana · Docker · Helm/K8s ·
pytest + testcontainers · Ruff · Mypy `--strict`

Use current library versions. Never introduce a deprecated API.

## Commands

Defined in `Makefile` from M0 onward:

```
make up        # docker compose up (postgres, redis, qdrant, minio, api)
make check     # ruff + mypy --strict + import-linter + pytest
make test      # pytest only
make migrate   # alembic upgrade head
make eval      # RAG evaluation harness (from M11)
```

## Testing rules

- Integration tests run against **real** Postgres/Redis/Qdrant via
  testcontainers. Never SQLite — it lies about types, constraints, and
  concurrency.
- Ports get a **shared contract suite** run against every adapter. An abstraction
  with one implementation is a hypothesis, not an abstraction.
- `tests/integration/test_tenant_isolation.py` is a standing suite; every new
  read path gets a cross-tenant case.
