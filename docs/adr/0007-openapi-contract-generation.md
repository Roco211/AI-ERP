# ADR 0007 — OpenAPI contract generation
Status: Accepted (user specification)

Pydantic/FastAPI OpenAPI is authoritative. Export docs/api/openapi.json, generate apps/web/generated/api/schema.d.ts using openapi-typescript, call through openapi-fetch. Never edit generated files. CI regenerates and rejects drift. Browser requests use same-origin /api/v1; Next rewrites /api to a server-only API_INTERNAL_URL. API_INTERNAL_URL is never NEXT_PUBLIC.

Operations v0.10 adds XLSX upload/download. `scripts/generate_api.mjs` uses the existing generator's AST transform to map OpenAPI `string` + `format: binary` to browser `Blob`; ordinary strings remain strings. Multipart transports serialize these fields as FormData. Binary responses declare their actual XLSX media type. This avoids disguising File objects as strings or maintaining hand-written parallel API contracts.
