# ADR 0007 — OpenAPI contract generation
Status: Accepted (user specification)

Pydantic/FastAPI OpenAPI is authoritative. Export docs/api/openapi.json, generate apps/web/generated/api/schema.d.ts using openapi-typescript, call through openapi-fetch. Never edit generated files. CI regenerates and rejects drift. Browser requests use same-origin /api/v1; Next rewrites /api to a server-only API_INTERNAL_URL. API_INTERNAL_URL is never NEXT_PUBLIC.
