# ADR 0011 — Local product embeddings

Status: user authorized local-computer/server execution on 2026-09-05.

Run BGE-M3 (1024 dimensions) through Ollama as an optional Docker Compose profile. This is a local model runtime, not a business microservice. PostgreSQL remains the only search store. Bind the runtime to loopback, disable cloud features, and never send product content to a cloud fallback. CPU execution is the initial portable choice. Pin the installed image digest and model identity in deployment documentation.

Product embeddings are tenant-scoped derived data with FORCE RLS, a composite tenant foreign key, model identity and product version. Committed product Outbox events trigger refresh; existing records can be rebuilt by an explicit tenant-scoped application command. Store only vectors for the matching version and model. Failed indexing leaves Outbox pending for retry. Inactive and stale products cannot contribute semantic results.

Keep exact/structured/keyword/trigram priority; semantic candidates are returned only when ordinary search has no matches. On model timeout/failure, ordinary search continues. Bound timeouts, response dimensions and finite values. Floats are used only for model vectors/similarity, never money, rates or quantities. Tests use explicit vector fixtures for boundary logic; separate live-model evaluation proves semantic behavior with real Chinese examples.
