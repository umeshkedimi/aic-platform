# ADR-0002: LangChain at the edges, never as the backbone

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

LangChain is the default answer in most RAG tutorials: `VectorStore`,
`Retriever`, `LLM`, `Chain`, memory, agents — an abstraction for every layer. It
is genuinely useful. It is also a fast-moving framework that has restructured
its packages and rewritten its core abstractions more than once
(`langchain` → `langchain-core` + partner packages; `Chain` → LCEL/Runnable).

The question is not "LangChain: yes or no." It is **which of our layers are
allowed to know that LangChain exists.**

## Decision

LangChain is a **library we call at well-defined edges**, not the framework our
domain is built on.

**We use it for (high value, low coupling):**

| Use | Package | Why |
|---|---|---|
| Document loaders | `langchain-community` | Format parsing is genuinely tedious and undifferentiated. Wrapped behind our `DocumentParser` port. |
| Text splitters | `langchain-text-splitters` | Small, stable, well-tested separator logic. Wrapped behind our `Chunker` port. |
| LCEL / `Runnable` composition | `langchain-core` | Excellent at what it does: composing the *answer generation* step with streaming, batching, retries, and `astream_events`. |
| Output parsers | `langchain-core` | Structured output with repair semantics. |
| Callbacks / streaming | `langchain-core` | Token-level streaming hooks feed our metrics and SSE endpoint. |

**We do NOT use it for:**

| Not used | Instead | Why |
|---|---|---|
| `VectorStore` as our interface | our own `VectorStore` port | LangChain's interface does not cover collection lifecycle, payload index management, named/sparse vectors, or tenant-scoped deletes consistently across backends. We would end up dropping to the native client anyway — with a leaky abstraction in between. |
| `Retriever` as our pipeline | our own composable retrieval pipeline | Retrieval is our *core differentiator*. It is the thing we tune, measure, and get paged about. It does not belong in a third party's inheritance hierarchy. |
| `Embeddings` as our provider abstraction | our own `EmbeddingProvider` port | We need batching, per-provider rate-limit handling, cost accounting, dimension registry, and a cache key that includes model version. That is our concern, not the framework's. |
| Memory / agents | explicit DB-backed sessions | Chat history is a persistence concern with retention, tenancy, and audit requirements. Hiding it in framework "memory" makes it invisible to compliance. |
| `Document` as our domain type | our own `Chunk` entity | The single highest-leverage rule below. |

## The load-bearing rule

> **`langchain` must not appear in an import statement inside `app/domain/`,
> `app/services/`, `app/repositories/`, or `app/models/`.**

Enforced in CI via an import-linter contract. If a LangChain type needs to cross
into the domain, it is converted at the adapter boundary.

Concretely: an adapter may accept or produce `langchain_core.documents.Document`
internally, but the function that our service layer calls returns
`list[app.domain.entities.Chunk]`. The conversion is ~10 lines in one file per
adapter. That is the entire price of the insurance policy.

## Why this is worth the ~10 lines per adapter

**Version churn becomes a contained refactor.** When a LangChain release changes
`Document.metadata` semantics or moves a package, the blast radius is the
adapter directory. Without the rule, the blast radius is every file that ever
touched a retrieved chunk.

**Our domain stays testable without the framework.** Service-layer unit tests
construct `Chunk` objects directly. No LangChain import, no mock of a framework
abstraction, no surprise network call from a `langchain-community` loader.

**We can be selectively better than the framework.** Hybrid search with Reciprocal
Rank Fusion, parent-document expansion, and tenant-scoped filtering are all
things we want to implement precisely and measure. Owning the interface means we
can, without fighting a base class.

**We are not betting the company on one library's roadmap.** If LangChain's
direction diverges from ours, we replace an adapter. This is the same reasoning
that says business logic should not import `boto3` directly.

## Consequences

- Slightly more code than "just use `RetrievalQA`." Roughly one thin translation
  module per adapter.
- Contributors coming from tutorials will need to be told why they cannot return
  a `Document` from a service. The rule is documented here and in `CLAUDE.md`.
- Pin `langchain-community` narrowly; it is the least stable package and the one
  with the widest transitive dependency surface.

## Revisit when

- LangChain's core abstractions have been stable across two major versions and
  cover collection lifecycle + hybrid search natively — at which point the
  adapter layer can thin out, but the port stays.
