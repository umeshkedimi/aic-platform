"""Domain-level exceptions.

Services and repositories raise these, never ``fastapi.HTTPException`` — the
domain does not know it is being served over HTTP (it might be called from a
Celery task or a CLI). The mapping to a status code lives entirely in
``app.core.errors``, at the one boundary that does know.
"""


class DomainError(Exception):
    """Base class for all domain-level errors."""


class NotFoundError(DomainError):
    """A requested entity does not exist, or is not visible to the caller.

    Deliberately used for both "does not exist" and "exists but belongs to
    another tenant" — a repository must never distinguish the two in its
    response, or it leaks the existence of other tenants' data.
    """


class ConflictError(DomainError):
    """The requested operation conflicts with existing state (e.g. duplicate)."""


class ValidationError(DomainError):
    """Input is well-formed but violates a domain rule."""


class PermissionDeniedError(DomainError):
    """The authenticated principal lacks permission for this operation."""


class UnauthenticatedError(DomainError):
    """No valid principal is associated with this request."""
