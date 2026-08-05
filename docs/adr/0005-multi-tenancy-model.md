# ADR-0005: Multi-tenancy via organization scoping, enforced at the repository layer

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

"Enterprise" means more than one customer, or at minimum more than one isolated
department, sharing a deployment. Tenancy is the single hardest thing to retrofit
into a schema: it touches every table, every query, every index, and every
vector-store filter. Teams that defer it spend a quarter on the migration and
still ship a cross-tenant leak.

We therefore decide it now, in the first schema, before any table exists.

## Decision

**Shared database, shared schema, `organization_id` on every tenant-owned row.**
Isolation is enforced in the repository layer, not left to callers.

```
Organization 1──* User
             1──* Collection 1──* Document 1──* Chunk
             1──* ApiKey, ChatSession, Job, AuditLog
```

Three enforcement layers, defence in depth:

1. **Schema.** `organization_id UUID NOT NULL REFERENCES organizations(id)` on
   every tenant table. Composite indexes lead with it:
   `(organization_id, collection_id, created_at)`. Uniqueness constraints are
   scoped: `UNIQUE (organization_id, collection_id, content_sha256)` — the same
   file uploaded by two tenants is two independent documents.

2. **Repository.** Tenant-scoped repositories take an `organization_id` at
   construction (from the authenticated principal, via DI) and inject the
   predicate into every query. **There is no repository method that can be called
   without a tenant scope.** This is the load-bearing rule: security that depends
   on every caller remembering a `WHERE` clause is not security.

3. **Vector store.** A mandatory `organization_id` payload filter on every
   Qdrant search, with a payload index on that field. The `VectorStore` port's
   signature makes the tenant a *required positional argument*, so it cannot be
   forgotten.

## Alternatives considered

**Database per tenant.** Strongest isolation, and the right answer for a handful
of large regulated customers. *Rejected for now:* connection-pool exhaustion at
scale, migrations become an N-database orchestration problem, and cross-tenant
platform analytics become impossible. Our port-based design does not preclude
adding it later for a specific enterprise tier.

**Schema per tenant (Postgres schemas).** A middle ground. *Rejected:* migration
complexity is nearly as bad as database-per-tenant, `search_path` juggling is a
persistent source of subtle bugs, and Postgres degrades with thousands of
schemas.

**Postgres Row-Level Security (RLS).** Genuinely attractive — enforcement in the
engine itself, below any application bug. *Deferred, not rejected:* it requires
every connection to carry a session variable, which interacts awkwardly with
connection pooling (a pooled connection must reliably reset it), and it makes
worker/admin paths that legitimately cross tenants harder to express. We keep
the schema RLS-compatible so it can be layered on as belt-and-braces later, and
revisit if a customer's compliance review requires it.

## Vector store: shared collection with filter, not collection-per-tenant

Qdrant offers both. We use **one collection per embedding-model configuration,
partitioned by `organization_id` payload index**, because:

- Collection-per-tenant multiplies HNSW graph memory overhead per collection; a
  thousand small collections is dramatically less memory-efficient than one
  large partitioned one.
- Qdrant's documented multitenancy guidance is exactly this pattern, with the
  tenant field payload-indexed so filtering happens during graph traversal
  rather than as a post-filter.
- Creating a collection per tenant makes tenant onboarding a schema operation
  with its own failure mode.

**Why per embedding-model, though:** vectors from different models are not
comparable and often differ in dimensionality. The collection name encodes
`{model}_{dim}` so a model migration is a new collection plus an atomic alias
switch — which also gives us zero-downtime re-embedding.

The escape hatch for a customer contractually requiring physical separation is a
per-organization collection override resolved in the adapter. The port does not
change.

## Consequences

- Every table in M1 carries `organization_id` from the first migration. No
  retrofit.
- The base repository class is the security-critical file in the codebase and
  gets reviewed accordingly.
- Integration tests must include an explicit **cross-tenant leakage suite**: seed
  two orgs, then assert every read path returns nothing for the other. This is a
  standing test category, not a one-off.
- Admin/platform operations that legitimately span tenants use a separate,
  explicitly-named unscoped repository, so a cross-tenant query is always
  visible in review.
