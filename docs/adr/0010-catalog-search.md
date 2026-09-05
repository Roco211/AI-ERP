# ADR 0010 — Tenant-scoped product discovery
Status: Accepted; external embedding provider decision pending

PostgreSQL alone serves catalog discovery: exact SKU/barcode/supplier-code match first, followed by escaped ILIKE and pg_trgm, constrained by category, brand and JSONB attribute filters. All queries execute under the authenticated tenant and permission. Pagination is bounded; sort fields are allowlisted. Inactive products are excluded from ProductPicker.

Search text is deterministically normalized from product names, model, specification and attributes. Outbox events describe committed changes. No business tool, AI runtime, Elasticsearch or new service is introduced. Embedding calls and data transmission require the separate provider/credential decision; no mock vectors will be reported as a working semantic search.

Provider decision (2026-09-05): user selected Command Code, chat endpoint `https://api.commandcode.ai/provider/v1/chat/completions`, model `deepseek-v4-flash-vision-exp`. Official provider documentation (https://commandcode.ai/docs/provider) lists Chat Completions, Messages and Models endpoints but does not document an embedding endpoint. No API credentials were requested or stored for this provider, no product data was sent, and no embedding support is claimed. Semantic search remains pending a documented embedding model/endpoint or explicit deferral. Do not convert chat text to fake vectors. The provider preference is recorded for later AI scope; no AI business tools are added in Catalog.

Keyword search normalizes case and multiplication symbols; exact SKU/barcode/specification outrank other results, followed by supplier code (only with supplier.read), token/trigram matching. Structured category/brand UUID filters and JSONB key/value containment run within tenant scope. The picker shows up to 20 matches and asks users to narrow the query; normal product lists paginate. ProductPrice projections are omitted (null) without product.price.read.
