# ADR 0006 — Audit, Outbox and Idempotency
Status: Accepted with Bootstrap consumer policy requiring review

Command mutation, append-only Audit and Outbox insert commit in one database transaction. Idempotency serializes identical actor/tenant/operation/key through transaction advisory locks, compares keyed canonical request fingerprints, and stores results in the same transaction. Errors roll back all records. Completed keys expire after 24 hours; callers must not reuse keys after this retention window.

Celery/Redis polls the database; FOR UPDATE SKIP LOCKED claims tenant-scoped rows. Bootstrap handles identity.session.created/revoked only with structured operational logging. Unknown events remain pending. Processing is at least once: a crash between an external effect and commit can duplicate delivery. Future consumers must deduplicate by event ID. No business handlers are introduced. Audit has INSERT/SELECT only for forge_app and never includes credentials or token hashes.
