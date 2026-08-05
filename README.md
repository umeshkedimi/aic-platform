# Enterprise RAG Platform

A multi-tenant, production-grade **Retrieval-Augmented Generation** platform —
built the way a platform team would build it, not the way a tutorial would.

Users upload documents into collections; the platform ingests them through an
asynchronous, resumable pipeline; queries are answered with cited, streaming
responses backed by hybrid retrieval and reranking.

> **Status:** in active development — **M0 (Foundation)**, not yet runnable.
> The architecture and the 13-milestone plan are complete and reviewed:
> see [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Why this repo exists

Most open RAG examples optimise for a working demo in 50 lines. This one
optimises for the things that decide whether a RAG system survives contact with
a real organisation:

- **Correctness under partial failure** — the queue is at-least-once, providers
  rate-limit, workers get OOM-killed mid-pipeline. Every stage is idempotent and
  resumable from a checkpoint.
- **Tenant isolation** — enforced structurally in the repository layer and the
  vector-store port signature, not by convention.
- **Swappability at the edges** — embedding models, LLM providers and vector
  stores all change within a year. Each sits behind a port with a shared contract
  test suite.
- **Observability by construction** — one trace spans API → worker → vector store
  → LLM, with token and cost attribution per tenant.
- **Measurable quality** — RAG regressions are silent. An evaluation gate blocks
  CI on a faithfulness drop.

Every non-obvious decision is written down as an ADR with the alternatives that
were rejected and why.

---

## Architecture

```
  WRITE PATH                                READ PATH
  ─────────────────────────────────         ─────────────────────────────────
  POST /v1/documents                        POST /v1/chat   (SSE stream)
        │                                         │
        ▼  stream + content-hash                  ▼
     MinIO  ──────────────▶ 202 + job id    Retrieval pipeline
        │                                    ├ query transform
        ▼                                    ├ dense ∥ sparse search
   Job(PENDING) ──▶ Redis ──▶ Celery         ├ fuse (RRF)
        │                                    ├ hydrate text (Postgres)
        ▼                                    ├ cross-encoder rerank
   extract ▶ clean ▶ chunk ▶ embed ▶ index   └ parent expand / compress
    └── cpu queue ──┘   └── io queue ──┘           │
        │                       │                  ▼
        ▼                       ▼            LCEL answer chain ──▶ LLM
    Postgres                 Qdrant                │
  (chunk text =          (vectors +                ▼
   source of truth)       filters only)     cited tokens + cost + trace
```

**Runtime roles** — one image, four entrypoints, four independently scaled
Deployments: `api` (uvicorn), `worker-cpu` (prefork), `worker-io` (gevent),
`beat` (scheduler).

### Layer dependency rule

```
api ──▶ services ──▶ repositories ──▶ models
         │              │
         ▼              ▼
      domain  ◀──── infrastructure
   (imports nothing from app.*)
```

Enforced in CI by an `import-linter` contract — not by good intentions.

---

## Design decisions

The five choices that gate everything downstream. Full reasoning, rejected
alternatives, and revisit conditions are in [`docs/adr/`](docs/adr/).

| # | Decision | The short version |
|---|---|---|
| [0001](docs/adr/0001-modular-monolith-with-role-based-processes.md) | **Modular monolith, split by process role** | Independent *scaling* doesn't require independent *deployment*. Four Deployments from one image gives 90% of the microservices benefit for 5% of the cost — and the seams that would become services are already ports. |
| [0002](docs/adr/0002-langchain-at-the-edges.md) | **LangChain at the edges, never the backbone** | Loaders, splitters, LCEL and output parsers: yes. Its `VectorStore`, `Retriever` and `Embeddings` as *our* interfaces: no. Retrieval is the thing we tune and get paged about; it doesn't belong in a third party's inheritance hierarchy. |
| [0003](docs/adr/0003-celery-for-background-processing.md) | **Celery for background processing** | Ingestion is a DAG with fan-out/fan-in and per-stage retry semantics. Celery's canvas models that natively; Arq and RQ would mean hand-rolling it. Async-first applies to the API, not to CPU-bound workers. |
| [0004](docs/adr/0004-postgres-is-the-source-of-truth-for-chunks.md) | **Postgres owns chunk text; the vector DB is a derived index** | Makes re-embedding a `SELECT` instead of a full re-extraction, makes GDPR deletes transactional and provable, and turns a corrupted index into a rebuild rather than a data-loss incident. |
| [0005](docs/adr/0005-multi-tenancy-model.md) | **Org-scoped multi-tenancy, enforced in the repository layer** | Decided in the first migration, because tenancy is the single hardest thing to retrofit. Security that depends on every caller remembering a `WHERE` clause is not security. |

---

## Package layout

```
app/
├── core/            # config, logging, security, errors, telemetry
├── domain/          # entities, value objects, ports (Protocols) — depends on nothing
├── models/          # SQLAlchemy 2 ORM          (persistence shape)
├── schemas/         # Pydantic v2              (wire shape — never the same thing)
├── repositories/    # persistence adapters; tenant scoping enforced here
├── services/        # use cases
├── infrastructure/  # adapters: embeddings, llms, vectorstores, storage,
│                    #           parsers, chunkers, rerankers, cache
├── retrieval/       # the composable retrieval pipeline
├── api/             # routers, dependencies, middleware
├── workers/         # celery app, tasks, pipeline stages
├── prompts/         # versioned prompt registry
└── observability/   # metrics, tracing
```

No `utils/` — it's a naming failure, and it means we didn't know where something
belonged.

---

## Capabilities

**Ingestion** — PDF, DOCX, Markdown, TXT, HTML. Streaming upload with
content-addressed storage, magic-byte MIME sniffing, size limits and a virus-scan
port. Connectors for Confluence, Notion, GitHub and SharePoint are planned as
adapters behind a `SourceConnector` port, not as new pipelines.

**Chunking** — recursive, token-aware, Markdown/heading-aware, code-aware (AST),
semantic, and parent-child. Default is token-aware recursive with heading
enrichment plus a parent-child index: retrieve small children for precision, feed
large parents to the LLM for context.

**Retrieval** — dense similarity, MMR, sparse/BM25, hybrid with Reciprocal Rank
Fusion, metadata filtering, parent-document expansion, multi-query, cross-encoder
reranking and contextual compression. Each stage is individually toggleable and
emits its own latency metric.

**Providers** — pluggable behind ports, each validated by a shared contract suite:

| Layer | Adapters |
|---|---|
| Embeddings | OpenAI · Gemini · Voyage · Ollama · sentence-transformers (BGE, Nomic) |
| LLM | Claude · OpenAI · Gemini · Ollama · vLLM |
| Vector store | Qdrant · pgvector |
| Object store | MinIO / any S3-compatible |
| Reranker | local cross-encoder · hosted API |

---

## Stack

| Concern | Choice |
|---|---|
| Language / tooling | Python 3.13, uv, Ruff, Mypy `--strict` |
| API | FastAPI, Pydantic v2, SSE streaming |
| Persistence | PostgreSQL, SQLAlchemy 2 (async), Alembic |
| Vector | Qdrant (primary), pgvector (contract-proving second adapter) |
| Cache / queue | Redis — caching, rate limits, sessions, embedding + LLM cache |
| Object storage | MinIO (S3-compatible) |
| Background | Celery — `cpu` and `io` queues on separate worker roles |
| LLM orchestration | LangChain (`langchain-core` LCEL) at adapter edges only |
| Observability | OpenTelemetry, Prometheus, Grafana, structlog |
| Testing | pytest, testcontainers, RAGAS / DeepEval |
| Delivery | Docker, Kubernetes, Helm, GitHub Actions |

---

## Roadmap

Each milestone is scoped to one working session and has an explicit **Done-when**
gate. Full detail — objectives, architecture, file lists, concepts and testing
strategy — in [`docs/ROADMAP.md`](docs/ROADMAP.md).

| # | Milestone | Gate |
|---|---|---|
| M0 | Foundation & developer experience | `make check` + `make up` green |
| M1 | Domain model & persistence | migrations apply; tenant-leak suite passes |
| M2 | AuthN / AuthZ / audit | full auth flow + RBAC tested |
| M3 | Object storage & upload | `202` + job row + object in MinIO |
| M4 | Async backbone (Celery) | no-op pipeline runs end to end |
| M5 | Extraction & chunking | 5 formats → chunks in Postgres |
| M6 | Embeddings | 2+ providers pass one contract suite; cache hit measured |
| M7 | Vector store & indexing | Qdrant + pgvector pass the same suite |
| M8 | Retrieval pipeline | `POST /search` with hybrid + rerank |
| M9 | LLM layer & RAG chain | streaming cited answers |
| M10 | Observability | one trace spans API → worker → LLM |
| M11 | Evaluation & quality gates | eval gate blocks CI on regression |
| M12 | Kubernetes, Helm, CI/CD | deployable chart + full pipeline |

---

## Getting started

Not yet runnable — M0 lands the toolchain, `docker-compose.yml` and the `Makefile`.
Once it does:

```bash
cp .env.example .env      # every variable is documented
make up                   # postgres, redis, qdrant, minio, api
make migrate              # alembic upgrade head
make check                # ruff + mypy --strict + import-linter + pytest
```

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Target architecture, package layout, milestone plan, session protocol |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records — the *why*, including rejected alternatives |
| [`CLAUDE.md`](CLAUDE.md) | Working agreement and the non-negotiable architecture rules |
