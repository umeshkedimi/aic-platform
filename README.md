# Enterprise RAG Platform

A multi-tenant, production-grade Retrieval-Augmented Generation platform:
users upload documents into collections, the platform ingests them
asynchronously, and queries are answered with cited, streaming responses.

**Status:** in development — see [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Documentation

| Document | Contents |
|---|---|
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Target architecture, package layout, and the 13-milestone implementation plan |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records — the *why* behind the load-bearing choices |
| [`CLAUDE.md`](CLAUDE.md) | Working agreement and the non-negotiable architecture rules |

## Architecture decisions

- [ADR-0001](docs/adr/0001-modular-monolith-with-role-based-processes.md) — Modular monolith, split by process role, not microservices
- [ADR-0002](docs/adr/0002-langchain-at-the-edges.md) — LangChain at the edges, never as the backbone
- [ADR-0003](docs/adr/0003-celery-for-background-processing.md) — Celery for background processing
- [ADR-0004](docs/adr/0004-postgres-is-the-source-of-truth-for-chunks.md) — Postgres is the source of truth; the vector DB is a derived index
- [ADR-0005](docs/adr/0005-multi-tenancy-model.md) — Multi-tenancy via organization scoping, enforced at the repository layer

## Stack

Python 3.13 · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL ·
Redis · Qdrant · MinIO · Celery · LangChain · OpenTelemetry · Prometheus ·
Grafana · Docker · Kubernetes · Helm
