# ADR-0004: Postgres is the source of truth; the vector DB holds vectors + filters only

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

Every vector database lets you store arbitrary payload alongside a vector.
Qdrant will happily hold the chunk text, the document title, the page number,
and anything else. The tutorial pattern is therefore: embed, upsert text into
the payload, retrieve, and hand the payload straight to the LLM. One store, no
join.

The question is whether the vector database is a **database** or an **index**.

## Decision

Treat Qdrant as a **derived index**, not a system of record.

| Data | Postgres | Qdrant payload |
|---|---|---|
| chunk text | ✅ source of truth | ❌ |
| chunk ordinal, offsets, token count | ✅ | ❌ |
| parent/child chunk links | ✅ | parent_id only |
| document metadata (title, author, source) | ✅ | ❌ |
| `chunk_id` (UUID) | ✅ PK | ✅ point id |
| `organization_id` | ✅ | ✅ indexed, for filtering |
| `collection_id`, `document_id` | ✅ | ✅ indexed |
| coarse filter fields (doc type, tags, date bucket) | ✅ | ✅ indexed |
| embedding vector | ❌ | ✅ |

Retrieval returns `chunk_id`s + scores from Qdrant, then hydrates full chunk
content from Postgres in a single batched query.

## Why

**Re-embedding is routine, and it must not require re-extraction.** Embedding
models get replaced — a better model ships, a provider deprecates an endpoint,
cost forces a change. If text lives only in the vector store, migrating models
means re-reading every source file from object storage and re-running the entire
extraction and chunking pipeline. With text in Postgres, re-embedding is
`SELECT id, content FROM chunks WHERE ...` → embed → upsert. Hours instead of
days, and no risk of extraction drift changing chunk boundaries mid-migration.

**Deletion must be provable.** GDPR/CCPA "delete my data" and enterprise
retention policies require an auditable, transactional delete. Postgres gives us
`ON DELETE CASCADE`, a transaction, and an audit-log row in the same commit.
Qdrant's delete is then a best-effort follow-up that a reconciliation job
verifies. If the vector store were the source of truth, "did we actually delete
it?" would have no authoritative answer.

**Vector stores are rebuildable; that is a feature worth preserving.** If Qdrant
is corrupted, misconfigured, or we switch backends (ADR: pgvector adapter in M7),
we rebuild the index from Postgres. If it held the only copy of the text, an
index problem is a data-loss incident.

**Relational data wants a relational store.** Parent-child chunk hierarchies,
joins to documents/collections/users, "which chunks were cited in which
messages", per-tenant counts for quota enforcement — these are joins and
aggregates. Doing them via vector-store payload filters is possible and
miserable.

**Payload size directly costs recall performance.** Qdrant keeps payload
alongside points; large text payloads inflate memory and slow filtered search.
Keeping the payload to a handful of indexed scalars keeps the index dense.

## The cost we accept

**An extra round trip per query.** Retrieval becomes Qdrant search → Postgres
hydrate. Mitigations:
- The hydrate is a single `WHERE id = ANY($1)` on a primary key — sub-millisecond
  for the ~20–100 ids a retrieval returns.
- Chunk content is immutable once written, making it trivially cacheable in
  Redis keyed by `chunk_id`. Hit rates are high because popular chunks recur.
- It is one round trip, not N: never hydrate in a loop.

**Two stores can drift.** Accepted and managed explicitly:
- Writes are ordered: Postgres commit first, then vector upsert. A crash between
  them leaves an orphan chunk row with no vector — invisible to search, harmless,
  and repaired by reconciliation.
- The reverse order would leave a vector pointing at a nonexistent chunk, which
  *is* user-visible (a retrieval hit that hydrates to nothing). Hence the order.
- A periodic reconciliation job (M7, Celery beat) diffs `chunk_id` sets per
  collection and repairs both directions, emitting a metric. Drift that is
  measured is drift that is manageable.

## Consequences

- The `VectorStore` port's search method returns `list[ScoredChunkRef]`
  (`chunk_id`, `score`, `parent_id`) — deliberately *not* text. The port cannot
  accidentally become the text store.
- Hydration lives in the retrieval pipeline, not in the vector adapter, so every
  backend gets it for free.
- Requires the reconciliation job to actually be built and alerted on. An
  unmonitored eventual consistency is just inconsistency.
