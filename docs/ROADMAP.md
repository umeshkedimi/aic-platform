# Enterprise RAG Platform — Implementation Roadmap

> **This document is the contract between sessions.** Each milestone is designed
> to be completed in one working session, starting from a cold context. Read
> [How to run a session](#how-to-run-a-session) before starting any milestone.

- **Status:** M0 complete; M1 not started
- **Last updated:** 2026-08-06
- **Architecture decisions:** [`docs/adr/`](./adr/)

---

## 1. What we are building

A multi-tenant Retrieval-Augmented Generation platform that a Fortune 500 could
deploy internally: users upload documents into collections, the platform ingests
them asynchronously, and users query them through a cited, streaming chat API.

**In scope (v1):** PDF, DOCX, Markdown, TXT, HTML.
**Planned (v2):** Confluence, Notion, GitHub, SharePoint connectors — the
ingestion pipeline is designed with a `SourceConnector` port so these are new
adapters, not new pipelines.

### The five properties every design decision is judged against

1. **Correctness under partial failure** — the queue is at-least-once, providers
   rate-limit, workers get OOM-killed mid-pipeline. Everything is idempotent and
   resumable.
2. **Tenant isolation** — a cross-tenant leak is the one bug that ends the
   product. Enforced structurally, not by convention (ADR-0005).
3. **Swappability at the edges** — embedding models, LLM providers and vector
   stores all change within a year. They sit behind ports (ADR-0002).
4. **Observability by construction** — if we cannot answer "why was this answer
   wrong and what did it cost", we cannot operate it.
5. **Measurable quality** — RAG regressions are silent. An eval gate in CI is not
   optional (M11).

---

## 2. Target architecture

### 2.1 Request paths

```
                         ┌──────────────────────────────────────────┐
  ingest (write path)    │                                          │
  ─────────────────────  │  POST /v1/documents ──▶ validate ──▶ MinIO│
                         │        │                    (content-    │
                         │        │                     addressed)  │
                         │        ▼                                 │
                         │   Job(PENDING) ──▶ Redis ──▶ Celery       │
                         │                              │           │
                         │   extract ▶ clean ▶ chunk ▶ embed ▶ index │
                         │      (cpu queue)          (io queue)      │
                         │        │                       │         │
                         │        ▼                       ▼         │
                         │    Postgres                 Qdrant       │
                         │  (chunk text =            (vectors +     │
                         │   source of truth)         filters only) │
                         └──────────────────────────────────────────┘

                         ┌──────────────────────────────────────────┐
  query (read path)      │  POST /v1/chat  (SSE stream)             │
  ─────────────────────  │        │                                 │
                         │        ▼                                 │
                         │  Retrieval pipeline                      │
                         │   ├ query transform (multi-query/HyDE)    │
                         │   ├ dense + sparse search (Qdrant)        │
                         │   ├ fuse (RRF)                            │
                         │   ├ hydrate chunk text (Postgres, batched)│
                         │   ├ rerank (cross-encoder)                │
                         │   └ compress / parent-expand              │
                         │        │                                 │
                         │        ▼                                 │
                         │  LCEL answer chain ──▶ LLM ──▶ tokens ──▶ │
                         │        │                                 │
                         │        └─▶ citations + cost + trace       │
                         └──────────────────────────────────────────┘
```

### 2.2 Package layout

```
app/
├── main.py                  # ASGI app factory — wiring only, no logic
├── core/                    # cross-cutting, no business logic
│   ├── config.py            # pydantic-settings, fail-fast at boot
│   ├── logging.py           # structlog, JSON, correlation ids
│   ├── security.py          # hashing, token crypto
│   ├── errors.py            # domain error → HTTP mapping
│   └── telemetry.py         # OTel setup
├── domain/                  # ◀── imports NOTHING from app.*
│   ├── entities.py          # Document, Chunk, Collection, Job … (dataclasses)
│   ├── value_objects.py     # ChunkRef, ScoredChunkRef, TokenUsage, Cost
│   ├── ports.py             # Protocols: VectorStore, EmbeddingProvider,
│   │                        #   LLMProvider, ObjectStore, DocumentParser,
│   │                        #   Chunker, Reranker, VirusScanner, SourceConnector
│   └── errors.py            # domain exceptions
├── models/                  # SQLAlchemy 2 ORM — persistence shape
├── schemas/                 # Pydantic v2 — wire shape (never reuse ORM models)
├── repositories/            # persistence adapters; tenant scoping enforced here
├── services/                # use cases; orchestrates ports + repositories
├── infrastructure/          # adapters implementing domain/ports.py
│   ├── embeddings/          # openai, gemini, voyage, ollama, sentence_transformers
│   ├── llms/                # anthropic, openai, gemini, ollama, vllm
│   ├── vectorstores/        # qdrant, pgvector
│   ├── storage/             # minio (s3-compatible)
│   ├── parsers/             # pdf, docx, markdown, html, txt
│   ├── chunkers/            # recursive, token, markdown, code, semantic, parent_child
│   ├── rerankers/           # cross_encoder, cohere
│   └── cache/               # redis: embedding cache, llm cache, rate limit
├── retrieval/               # the composable retrieval pipeline (our differentiator)
├── api/
│   ├── v1/routers/          # thin: parse → call service → serialise
│   ├── dependencies/        # DI: current_user, require_permission, get_session
│   └── middleware/          # correlation id, rate limit, timing, error handler
├── workers/
│   ├── celery_app.py        # single reviewed config surface
│   ├── tasks/               # thin task wrappers
│   └── pipeline/            # ingestion stages (pure functions where possible)
├── prompts/                 # versioned prompt templates + registry
└── observability/           # metrics definitions, tracing helpers
```

**Dependency rule — enforced in CI by `import-linter`, not by good intentions:**

```
api ──▶ services ──▶ repositories ──▶ models
         │              │
         ▼              ▼
      domain  ◀──── infrastructure
   (imports nothing from app.*)
```

Notable deviations from the "standard tutorial" layout, and why:

- **`domain/` exists.** Ports live somewhere that neither the web layer nor the
  adapters own. Without it, "abstraction" degrades into an interface defined in
  the same file as its only implementation.
- **No `utils/`.** `utils/` is a naming failure: it means we did not know where
  something belonged. Every function has a home — text normalisation goes in
  `infrastructure/parsers/`, token counting in `domain/value_objects.py`.
- **`retrieval/` is top-level, not under `infrastructure/`.** Retrieval quality
  *is* the product. It is business logic, not an adapter.
- **`schemas/` never reuses `models/`.** The wire contract and the storage schema
  change for different reasons and at different rates. Coupling them means a
  column rename is a breaking API change.

### 2.3 Runtime roles (one image, four entrypoints — ADR-0001)

| Role | Pool | Queues | Scales on |
|---|---|---|---|
| `api` | uvicorn async | — | concurrent requests |
| `worker-cpu` | prefork | `cpu` | queue depth |
| `worker-io` | gevent | `io` | queue depth, provider quota |
| `beat` | — | — | singleton |

---

## 3. Milestones

Each milestone is a session. **Do not start M(n+1) until M(n)'s Done-when gate
passes.** Milestones marked ⚠️ are large and may take two sessions; the split
point is noted.

| # | Milestone | Sessions | Gate |
|---|---|---|---|
| M0 | Foundation & developer experience ✅ | 1 | `make check` + `make up` green |
| M1 | Domain model & persistence | 1–2 ⚠️ | migrations apply, tenant-leak suite passes |
| M2 | AuthN / AuthZ / audit | 1 | full auth flow + RBAC tested |
| M3 | Object storage & upload | 1 | `202` + job row + object in MinIO |
| M4 | Async backbone (Celery) | 1 | no-op pipeline runs end to end |
| M5 | Extraction & chunking | 1–2 ⚠️ | 5 formats → chunks in Postgres |
| M6 | Embeddings | 1 | 2+ providers, cache hit measured |
| M7 | Vector store & indexing | 1–2 ⚠️ | Qdrant + pgvector pass same suite |
| M8 | Retrieval pipeline | 2 ⚠️ | `POST /search` with hybrid + rerank |
| M9 | LLM layer & RAG chain | 2 ⚠️ | streaming cited answers |
| M10 | Observability | 1 | trace spans API→worker→LLM |
| M11 | Evaluation & quality gates | 1–2 ⚠️ | eval gate blocks CI on regression |
| M12 | Kubernetes, Helm, CI/CD, hardening | 2 ⚠️ | deployable chart + full pipeline |

---

### M0 — Foundation & developer experience

**Objective.** A repository where every quality gate exists *before* the first
line of business logic. Retrofitting type checking and test infrastructure onto
an existing codebase is a multi-week slog; adding it to an empty one is an hour.

**Architecture.** Configuration is the first real design decision: settings are a
single frozen `Settings` object built from environment variables, validated at
import time, and injected — never read via `os.getenv` at point of use, never
mutated. A misconfigured deployment must fail at boot with a clear message, not
at 3am on the first request that touches the bad value.

**Files.**
```
pyproject.toml               # uv, deps, ruff, mypy, pytest config in one place
uv.lock
Makefile                     # up, down, check, test, migrate, logs
.env.example                 # every var documented, no real secrets
.pre-commit-config.yaml
.importlinter                # the layer contract
app/core/config.py
app/core/logging.py
app/core/errors.py
app/main.py
app/api/v1/routers/health.py
app/api/middleware/correlation.py
docker/Dockerfile            # multi-stage, non-root, uv
docker/docker-compose.yml    # postgres, redis, qdrant, minio, api
tests/conftest.py
tests/unit/test_config.py
.github/workflows/ci.yml
```

**Concepts.** 12-factor config; pydantic-settings v2; structured logging and why
JSON logs beat pretty ones the moment you have more than one replica;
correlation-id propagation via `contextvars`; liveness vs readiness (they answer
different questions and conflating them causes rolling-deploy outages); Docker
layer caching and why dependency install must precede source copy; multi-stage
builds and non-root containers.

**Testing.** Unit tests for settings validation (missing required var must raise
at boot). A smoke test hitting `/health/live`. CI runs ruff, mypy `--strict`,
import-linter, pytest.

**Done when.** `make up` brings up all five services; `curl /health/ready`
returns 200 only when Postgres/Redis/Qdrant/MinIO are all reachable;
`make check` passes; CI is green on a PR.

---

### M1 — Domain model & persistence ⚠️

**Objective.** The full relational schema, with tenancy baked in from migration
one (ADR-0005), and a repository layer that makes cross-tenant queries
structurally impossible.

**Architecture.** Entities: `Organization`, `User`, `Role`, `Permission`,
`ApiKey`, `Collection`, `Document`, `Chunk`, `Job`, `ChatSession`, `Message`,
`AuditLog`. Async SQLAlchemy 2 with `Mapped[]` typing throughout. Session
lifecycle is per-request via DI, with an explicit unit-of-work boundary in the
service layer — the repository never commits; the caller owns the transaction.

**Files.**
```
app/models/base.py           # DeclarativeBase, UUID pk, timestamps, soft delete
app/models/{organization,user,rbac,collection,document,chunk,job,chat,audit,api_key}.py
app/domain/entities.py
app/domain/value_objects.py
app/repositories/base.py     # ◀── security-critical: tenant scoping lives here
app/repositories/{document,collection,chunk,job,user,chat,audit}_repository.py
app/core/database.py         # engine, session factory, DI dependency
alembic/  + alembic.ini + alembic/versions/0001_initial.py
tests/integration/test_repositories.py
tests/integration/test_tenant_isolation.py   # ◀── standing suite
```

**Concepts.** SQLAlchemy 2 declarative typing; async sessions and why lazy
loading is a trap under async (`selectinload` vs N+1); unit of work vs
repository-commits-itself; composite index design driven by actual query
patterns, leading with `organization_id`; partial indexes for soft deletes;
`CHECK` constraints as the last line of defence; Alembic autogenerate's blind
spots (it misses index renames, constraint changes, and enum alterations —
always read the generated migration); expand/contract migration strategy for
zero-downtime schema changes.

**Testing.** Repository integration tests against a real Postgres via
`testcontainers` — not SQLite. SQLite lies about types, constraints, and
concurrency; a test suite that passes on SQLite and fails on Postgres is worse
than no test suite. Plus the cross-tenant leakage suite: seed two orgs, assert
every read path returns empty for the other.

**Done when.** `alembic upgrade head` then `downgrade base` round-trips cleanly;
all repository tests pass; tenant isolation suite passes.

**Split point if two sessions:** models + migration first, repositories + tests
second.

---

### M2 — AuthN / AuthZ / audit

**Objective.** Production authentication that would survive a security review.

**Architecture.** Argon2id password hashing. Short-lived JWT access tokens
(15 min) + long-lived opaque refresh tokens stored hashed in Postgres, with
**rotation and reuse detection**: presenting an already-used refresh token
invalidates the entire family, because that is the signature of a stolen token.
RBAC as role→permission mappings checked by a `require_permission("documents:write")`
dependency — never by scattered `if user.role == "admin"` checks, which are
unauditable. API keys are hashed at rest with a short lookup prefix, so a leaked
database dump does not yield usable keys.

**Files.**
```
app/core/security.py
app/services/auth_service.py
app/services/audit_service.py
app/api/v1/routers/auth.py          # register, login, refresh, logout, me
app/api/dependencies/auth.py        # CurrentUser, require_permission, api_key auth
app/api/middleware/rate_limit.py    # redis sliding-window
app/schemas/auth.py
tests/integration/test_auth_flow.py
tests/unit/test_rbac.py
```

**Concepts.** Why access tokens are short and refresh tokens are opaque and
revocable (a JWT you cannot revoke is a 15-minute liability, not a 30-day one);
refresh token rotation + reuse detection; RBAC vs ABAC and when the extra
complexity is earned; timing-safe comparison; rate limiting algorithms (fixed
window vs sliding window vs token bucket) and their failure modes at boundaries;
audit logs as an append-only, tamper-evident record.

**Testing.** Full flow integration test; RBAC matrix unit tests; explicit
negative tests — expired token, reused refresh token, insufficient permission,
rate limit exceeded, cross-tenant access with a valid token.

**Done when.** Register → login → access protected route → refresh → reuse old
refresh token → family revoked. Every auth-relevant action lands in `audit_logs`.

---

### M3 — Object storage & upload

**Objective.** Accept a file safely and durably, then hand off to async
processing.

**Architecture.** `ObjectStore` port with a MinIO/S3 adapter. Objects are
**content-addressed** (`{org_id}/{sha256[:2]}/{sha256}`): deduplication is free,
re-upload is idempotent, and the storage key is verifiable. Upload streams to
storage while hashing — the file never lands fully in memory. Validation is
defence-in-depth: declared size → actual streamed size cap → magic-byte MIME
sniffing (never trust the extension or the client's `Content-Type`) → per-format
structural check. A `VirusScanner` port with a no-op default and a ClamAV adapter
sits before the file is marked usable.

**Files.**
```
app/domain/ports.py                   # ObjectStore, VirusScanner
app/infrastructure/storage/minio_store.py
app/infrastructure/scanning/{noop,clamav}_scanner.py
app/services/document_service.py
app/api/v1/routers/documents.py       # POST, GET list, GET one, DELETE
app/api/v1/routers/collections.py
app/schemas/{document,collection}.py
tests/integration/test_upload.py
```

**Concepts.** Streaming multipart uploads and backpressure; content addressing;
presigned URLs and when to let clients upload directly to storage (bypassing the
API entirely for large files); MIME sniffing vs extension trust; the
zip-bomb/decompression-bomb class of attack; why `202 Accepted` + job id is the
only honest response for work that takes minutes; S3 migration path (the adapter
is already S3-API; only credentials and endpoint change).

**Testing.** Upload each of the 5 formats; oversized file rejected; extension/
content mismatch rejected; duplicate upload deduplicates; delete removes DB row,
object, and (later) vectors.

**Done when.** `POST /v1/documents` returns `202` with a job id, the object is in
MinIO under its content hash, and a `Job` row exists in `PENDING`.

---

### M4 — Async backbone

**Objective.** The pipeline skeleton — correct job semantics before any real
processing logic, so that when extraction is added we are debugging extraction,
not the queue.

**Architecture.** Celery (ADR-0003) with `cpu` and `io` queues on separate
worker roles. The ingestion pipeline is an explicit **state machine** persisted
in Postgres:

```
PENDING → VALIDATING → SCANNING → EXTRACTING → CHUNKING
        → EMBEDDING → INDEXING → COMPLETED
                                ↘ FAILED(stage, reason, retryable)
                                ↘ CANCELLED
```

Each stage checkpoints on completion, so a retry resumes rather than restarts —
re-running a 12-minute extraction because embedding hit a 429 is unacceptable.
Every task is idempotent, keyed by `(document_id, stage, content_sha256)`.

**Files.**
```
app/workers/celery_app.py
app/workers/tasks/ingestion.py
app/workers/pipeline/{state_machine,stages}.py
app/services/job_service.py
app/api/v1/routers/jobs.py            # GET /jobs/{id}, GET /jobs, POST cancel
docker/docker-compose.yml             # + worker-cpu, worker-io, beat, flower
tests/integration/test_pipeline_state_machine.py
```

**Concepts.** At-least-once delivery and why idempotency is mandatory, not
defensive; `acks_late` + `visibility_timeout` and the duplicate-execution window
they create; transient vs permanent failure classification; exponential backoff
with jitter (and why jitter matters — synchronised retries are a self-inflicted
DDoS); dead-letter queues and poison messages; graceful shutdown on SIGTERM
during a rolling deploy; why task args are ids, never payloads; prefork vs gevent
pool selection.

**Testing.** State machine transitions including illegal ones; idempotency —
running a stage twice produces one result; retry resumes from checkpoint; task
routing lands on the right queue.

**Done when.** A no-op pipeline runs upload → COMPLETED with every transition
visible via `GET /jobs/{id}`, and killing a worker mid-stage results in resumption
rather than restart or loss.

---

### M5 — Extraction & chunking ⚠️

**Objective.** Turn 5 file formats into well-formed, retrievable chunks. **This
milestone determines the ceiling on retrieval quality** — no reranker recovers
from bad chunk boundaries.

**Architecture.** `DocumentParser` port + registry (Strategy + Factory) resolving
by sniffed MIME type. Parsers emit normalised text plus structural metadata
(page numbers, heading hierarchy, table markers) — structure is what makes
citations precise and metadata filtering possible, and it is lost forever if the
parser discards it. `Chunker` port with multiple strategies, selected per
collection.

**Chunking strategies and when each is correct:**

| Strategy | Use when | Failure mode |
|---|---|---|
| Fixed/recursive character | baseline, unstructured prose | splits mid-sentence, orphans context |
| Token-aware | always, layered on the above | none — but requires the right tokenizer per model |
| Markdown/heading-aware | docs, wikis, READMEs | degenerates on flat documents |
| Code-aware (AST) | source repos | language-specific parsers needed |
| Semantic (embedding-distance boundaries) | heterogeneous long-form | expensive: embeds twice; unstable boundaries |
| Parent-child | when precision and context conflict | doubles storage; needs hydration logic |

The default is **token-aware recursive with heading enrichment and a
parent-child index**: retrieve small children for precision, feed large parents
to the LLM for context. This resolves the central chunking tension rather than
compromising on a middle chunk size that is bad at both.

**Files.**
```
app/domain/ports.py                   # DocumentParser, Chunker
app/infrastructure/parsers/{registry,pdf,docx,markdown,html,text}_parser.py
app/infrastructure/parsers/normalizer.py
app/infrastructure/chunkers/{registry,recursive,token,markdown,code,semantic,parent_child}.py
app/workers/pipeline/stages.py        # extract, clean, chunk implemented
tests/unit/test_chunkers.py
tests/integration/test_extraction.py
tests/fixtures/documents/             # real sample files per format
```

**Concepts.** PDF extraction is genuinely hard — text layer vs OCR, multi-column
reading order, headers/footers, tables; why HTML needs boilerplate removal before
chunking; Unicode normalisation and whitespace collapse; tokenizers differ per
model, so "512 tokens" is model-relative; overlap as insurance against boundary
loss and its storage cost; contextual chunk headers (prepending document/section
title to each chunk) as a cheap, large retrieval win.

**Testing.** Golden-file tests per format — parse a fixture, assert the extracted
text against a committed expectation, so parser-library upgrades cannot silently
change output. Chunker property tests: no chunk exceeds max tokens, overlap is
correct, concatenation reconstructs the source, no chunk is empty.

**Done when.** All 5 formats produce chunks in Postgres with page/heading
metadata intact, and chunker unit tests pass.

**Split point:** parsers first, chunkers second.

---

### M6 — Embeddings

**Objective.** A provider-agnostic embedding layer with caching and cost
accounting.

**Architecture.** `EmbeddingProvider` port exposing `embed_documents`,
`embed_query` (they differ — asymmetric models use different prefixes),
`dimensions`, and `model_id`. Adapters: OpenAI, Gemini, Voyage, Ollama,
sentence-transformers (BGE/Nomic, behind an optional extra so the API image does
not carry `torch`). A Redis cache keyed by
`sha256(model_id + normalized_text)` — the model id **must** be in the key, or a
model change silently serves stale vectors, which is the kind of bug that takes
a week to find.

**Files.**
```
app/domain/ports.py                   # EmbeddingProvider
app/infrastructure/embeddings/{base,openai,gemini,voyage,ollama,sentence_transformers}.py
app/infrastructure/embeddings/factory.py
app/infrastructure/cache/embedding_cache.py
app/services/embedding_service.py     # batching, concurrency, cost accounting
app/workers/pipeline/stages.py        # embed implemented
tests/unit/test_embedding_cache.py
tests/integration/test_embedding_providers.py
```

**Concepts.** Dense embeddings and cosine similarity; symmetric vs asymmetric
models and why query/document prefixes matter; dimensionality vs quality vs cost;
Matryoshka embeddings (truncatable dimensions); batch sizing against provider
token limits; handling 429s with backoff and a concurrency semaphore; the
migration problem — changing model means re-embedding everything (ADR-0004 is
what makes this survivable); normalisation and when the vector store expects it.

**Testing.** Contract test suite run against *every* adapter — same assertions,
each provider (dimensions match declared, deterministic for identical input,
batch equals sequential). Cache hit/miss behaviour. Live provider tests marked
and excluded from default CI.

**Done when.** At least two providers pass the shared contract suite, cache hit
rate is observable, and per-batch token cost is recorded.

---

### M7 — Vector store & indexing ⚠️

**Objective.** The vector layer, with a second implementation proving the
abstraction is real.

**Architecture.** `VectorStore` port: `ensure_collection`, `upsert`,
`delete_by_document`, `search` (dense), `search_hybrid`, `count`. Tenant is a
**required argument**, not an optional filter (ADR-0005). Search returns
`ScoredChunkRef` — ids and scores, never text (ADR-0004). Qdrant adapter uses
named vectors (dense + sparse) for hybrid search and payload indexes on
`organization_id`, `collection_id`, `document_id`. Collections are named
`{model_id}_{dim}` with an alias, so model migration is build-new → switch-alias
→ drop-old with no downtime.

**The pgvector adapter is not optional.** A port with one implementation is a
hypothesis, not an abstraction. The second implementation is what proves the
interface does not leak Qdrant concepts — and it costs half a day now versus a
rewrite later.

**Files.**
```
app/domain/ports.py                   # VectorStore
app/infrastructure/vectorstores/{base,qdrant_store,pgvector_store,factory}.py
app/infrastructure/vectorstores/collection_naming.py
app/services/indexing_service.py
app/workers/tasks/reconciliation.py   # beat job: diff Postgres ↔ Qdrant
app/workers/pipeline/stages.py        # index implemented
tests/integration/test_vectorstore_contract.py   # runs against BOTH adapters
tests/integration/test_reconciliation.py
```

**Concepts.** HNSW — `m`, `ef_construct`, `ef` and the recall/latency/memory
triangle; why ANN is approximate and what recall@k actually costs; pre-filter vs
post-filter and why an unindexed payload filter destroys performance;
quantisation (scalar/binary) for memory reduction; sparse vectors (BM25/SPLADE)
alongside dense; write ordering and the drift it permits; zero-downtime
reindexing via aliases.

**Testing.** One contract suite executed against Qdrant and pgvector — if it
passes on both, the abstraction holds. Tenant filter enforcement. Reconciliation
detects and repairs injected drift in both directions.

**Done when.** Both adapters pass the same suite; full ingestion runs
upload → searchable; reconciliation reports zero drift on a clean corpus.

**Split point:** Qdrant + contract suite first, pgvector + reconciliation second.

---

### M8 — Retrieval pipeline ⚠️ (2 sessions)

**Objective.** The core differentiator: composable, measurable retrieval.

**Architecture.** A pipeline of small, individually testable, individually
toggleable stages — *not* a monolithic retriever class:

```
query → [transform] → [dense ∥ sparse] → [fuse RRF] → [hydrate]
      → [rerank] → [parent expand] → [compress] → context
```

Each stage is configurable per collection and emits its own latency + result-count
metrics, so "retrieval is slow" is always answerable with "which stage".

**Techniques and their honest tradeoffs:**

| Technique | Gains | Costs |
|---|---|---|
| Similarity (dense) | semantic matching, baseline | misses exact terms, IDs, rare jargon |
| MMR | diversity, less redundancy | can drop the single best result |
| Sparse/BM25 | exact terms, acronyms, code identifiers | no semantics |
| Hybrid + RRF | best of both; largest single quality win | 2 searches, fusion tuning |
| Metadata filtering | precision, tenancy, recency | over-filtering silently empties results |
| Parent-document | precision + context together | storage, hydration complexity |
| Multi-query | recall on vague queries | N× search cost + an LLM call of latency |
| Cross-encoder rerank | **largest quality gain per unit effort** | 50–200ms; a GPU or hosted API |
| Contextual compression | fits context budget, less distraction | an extra LLM call; can drop needed detail |

**Files.**
```
app/retrieval/pipeline.py             # orchestrator
app/retrieval/stages/{query_transform,dense,sparse,fusion,hydrate,rerank,parent_expand,compress}.py
app/retrieval/config.py               # per-collection retrieval profile
app/infrastructure/rerankers/{cross_encoder,cohere}_reranker.py
app/services/search_service.py
app/api/v1/routers/search.py
tests/unit/test_fusion.py
tests/integration/test_retrieval_pipeline.py
```

**Concepts.** Recall vs precision at each stage and why the funnel is
retrieve-wide-then-rerank-narrow; RRF and why rank fusion beats score fusion
(scores from different systems are not comparable); bi-encoder vs cross-encoder;
the lost-in-the-middle effect and context ordering; HyDE; over-filtering as a
silent failure mode; the retrieval latency budget and where it goes.

**Testing.** Fusion unit tests with known rankings. A small labelled query set
with recall@k / MRR assertions. Per-stage latency assertions. Ablation tests —
each stage measurably improves the metric it claims to.

**Done when.** `POST /v1/search` supports hybrid + filters + rerank, returns
scored chunks with provenance, and per-stage timings appear in the response
debug block.

---

### M9 — LLM layer & RAG chain ⚠️ (2 sessions)

**Objective.** Grounded, cited, streaming answers with full cost attribution.

**Architecture.** `LLMProvider` port with adapters for Claude, OpenAI, Gemini,
Ollama, vLLM. Answer generation is composed with **LCEL** — the one place
LangChain genuinely earns its keep, giving streaming, batching, and
`astream_events` for free (ADR-0002). Prompts live in a versioned registry, not
in f-strings scattered through services: a prompt is a deployed artifact and must
be diffable, reviewable, and attributable in eval results. Citations are
structural — the model is given identified chunks and required to emit
references, which are then validated against the actual retrieved set and
**dropped if hallucinated**.

**Files.**
```
app/domain/ports.py                   # LLMProvider
app/infrastructure/llms/{base,anthropic,openai,gemini,ollama,vllm,factory}.py
app/prompts/{registry,rag_answer_v1,query_rewrite_v1,compress_v1}.py
app/services/chat_service.py
app/retrieval/citation.py
app/api/v1/routers/chat.py            # SSE streaming
app/infrastructure/cache/llm_cache.py
app/core/security_llm.py              # injection defences, PII redaction
tests/integration/test_chat_streaming.py
tests/unit/test_citation_validation.py
```

**Concepts.** LCEL and the `Runnable` protocol — why composition beats
inheritance for chains; SSE vs WebSocket for token streaming and why SSE is
right here; conversation history management under a token budget (windowing vs
summarisation); grounding and refusal — "I don't know" is a correct answer and
must be reachable; prompt injection via *retrieved documents* (the RAG-specific
threat: your corpus is untrusted input); structural defences (delimiting,
instruction hierarchy) and why they are mitigation, not prevention; token
accounting and per-tenant cost attribution.

**Testing.** Streaming integration test asserting token order and terminal event;
citation validation rejects fabricated references; injection corpus tests;
context-budget enforcement; provider contract suite across all adapters.

**Done when.** `POST /v1/chat` streams a cited answer over SSE, persists the
session and messages, records token usage and cost per request, and refuses when
retrieval returns nothing relevant.

---

### M10 — Observability

**Objective.** Answer "why was this answer wrong, how slow was it, and what did
it cost" from telemetry alone.

**Architecture.** One OpenTelemetry trace spanning API → Celery → Qdrant → LLM
(context propagated through the task message header — this is the part everyone
gets wrong and the reason worker spans show up orphaned). Prometheus metrics in
three families: RED (rate/errors/duration) per endpoint; pipeline (stage
duration, queue depth, failure rate by stage); RAG-specific (retrieval latency
per stage, chunks retrieved, rerank delta, tokens in/out by model, **cost per
request by tenant**).

**Files.**
```
app/core/telemetry.py
app/observability/metrics.py
app/observability/tracing.py
app/api/middleware/{timing,metrics}.py
app/workers/telemetry.py              # trace context propagation into tasks
monitoring/prometheus/prometheus.yml + rules/alerts.yml
monitoring/grafana/dashboards/{api,ingestion,rag_quality,cost}.json
docker/docker-compose.observability.yml
```

**Concepts.** Traces vs metrics vs logs and what each is for; cardinality as the
thing that kills metrics backends (never label by user id or query text);
histograms and why P99 is computed from buckets, not averages; exemplars linking
a slow metric to its trace; correlation id → trace id; span attributes for LLM
calls (model, tokens, cost, cache hit); tail-based sampling.

**Testing.** Assert spans are emitted and parented correctly across the queue
boundary; metrics endpoint scrapes clean; alert rules unit-tested with
`promtool`.

**Done when.** A single upload→chat flow shows one connected trace across all
processes, and Grafana shows P50/P95/P99 latency and cost per tenant.

---

### M11 — Evaluation & quality gates ⚠️

**Objective.** Make RAG quality a number that CI can block on. **RAG regressions
are silent** — a prompt tweak or chunker change degrades answers without any test
failing. This milestone is what separates a demo from a product.

**Architecture.** A versioned golden dataset (question, ground-truth answer,
expected source chunks) committed to the repo. An offline eval harness runs the
full pipeline and scores with RAGAS/DeepEval: **faithfulness** (is the answer
grounded in retrieved context — the hallucination metric), **context precision/
recall** (did retrieval find the right things), **answer correctness/relevancy**.
Retrieval and generation are scored *separately* — otherwise you cannot tell
whether a bad answer came from bad retrieval or a bad prompt, which is the first
question you always need answered.

**Files.**
```
eval/datasets/golden_v1.jsonl
eval/harness.py
eval/metrics/{faithfulness,context_precision,answer_correctness,latency}.py
eval/report.py                        # markdown + JSON, PR-commentable
scripts/run_eval.py
.github/workflows/eval.yml            # gate on regression vs baseline
tests/eval/test_regression_gate.py
```

**Concepts.** LLM-as-judge and its biases (position, verbosity, self-preference)
and how to control for them; separating retrieval from generation metrics;
dataset construction and the synthetic-vs-human tradeoff; statistical
significance on small eval sets — do not chase noise; latency budgets as a
first-class quality metric; regression gating thresholds and how to set them
without blocking every PR.

**Testing.** The harness is itself tested against known-good and known-bad
fixtures. The CI gate demonstrably fails on a deliberately degraded config.

**Done when.** `make eval` produces a scored report, and a PR that worsens
faithfulness by more than the threshold fails CI.

---

### M12 — Kubernetes, Helm, CI/CD & hardening ⚠️ (2 sessions)

**Objective.** Deployable, secured, and operable by someone who is not you.

**Architecture.** Helm chart with per-role Deployments (ADR-0001), HPA on CPU for
`api` and on queue depth (KEDA) for workers, migrations as a pre-upgrade Job with
a hook, PodDisruptionBudgets, resource requests/limits, liveness/readiness/startup
probes, NetworkPolicies defaulting to deny, secrets via External Secrets rather
than committed values. CI/CD: lint → type → test → build → scan (Trivy image,
Bandit/Semgrep code, `pip-audit` deps, SBOM) → eval gate → deploy.

**Files.**
```
helm/rag-platform/{Chart.yaml,values.yaml,values-prod.yaml,templates/*}
k8s/base/                              # raw manifests for reference/learning
.github/workflows/{ci.yml,cd.yml,security.yml}
docker/Dockerfile                      # hardened: distroless/slim, non-root, read-only fs
scripts/{smoke_test.sh,load_test.py}
docs/RUNBOOK.md                        # on-call: symptoms → diagnosis → action
```

**Concepts.** Requests vs limits and why CPU limits often hurt more than help;
probe semantics and how a wrong readiness probe causes rolling-deploy outages;
graceful shutdown, `preStop`, and `terminationGracePeriodSeconds` matched to the
longest task; HPA on the right signal (queue depth, not CPU, for workers);
migrations in a Job with expand/contract so old and new pods coexist safely;
secret management; supply-chain scanning and SBOMs; blue/green vs canary.

**Testing.** Helm lint + template snapshot tests; `kind` cluster deploy in CI;
smoke tests post-deploy; load test establishing throughput and P99 baselines.

**Done when.** `helm install` on a local `kind` cluster brings up a working
platform; CI runs the full pipeline including security scans and the eval gate.

---

## 4. How to run a session

Because each milestone starts from a cold context, every session follows the same
protocol.

**Start of session**
1. Read `CLAUDE.md` (auto-loaded) and this file's milestone entry.
2. Read the ADRs referenced by that milestone.
3. `git log --oneline -10` and `make check` to confirm the previous gate still passes.
4. State the milestone objective and the Done-when gate before writing code.

**During**
- Architecture and rationale *before* code. Alternatives and tradeoffs stated.
- Code in reviewable increments, not one dump.
- Tests alongside, not after.

**End of session — the milestone review**
1. Re-read the Done-when gate; demonstrate it passing.
2. **Architecture review:** what we built, what we would do differently.
3. **Code review:** critique the code as if it arrived as someone else's PR.
4. **Production enhancements:** what a Fortune 500 would additionally require.
5. Write any new decisions as ADRs.
6. Update this file's status line and the milestone table, and the tracker in
   `CLAUDE.md` §Remaining work.
7. Confirm the final commit of the session references the milestone.
8. Ask whether to continue to the next milestone.

**Session hygiene**
- One milestone per session. If a milestone is running long, stop at its split
  point and record where you stopped.
- Never start the next milestone in the same session as a review.
- Commit incrementally as reviewable pieces land (e.g. models → migration →
  repositories → tests) — several commits per session, not one squashed commit
  at the end.

---

## 5. Open questions to resolve before M6 / M9

These need your input; they do not block M0–M5.

1. **LLM provider priority** — which do you actually have keys for? Determines
   which adapter gets built first and which the eval suite runs against.
2. **Embedding provider** — hosted (OpenAI/Voyage: simple, per-token cost) or
   self-hosted (BGE via sentence-transformers: free, needs GPU, heavier image)?
3. **Reranking** — cross-encoder locally (needs a GPU for acceptable latency) or
   a hosted reranker API?
4. **Scale targets** — documents, chunks, queries/sec, tenants. These set HNSW
   parameters, quantisation choice, and hardware sizing in M7 and M12.
