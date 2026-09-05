# ADR 0001 — Modular monolith
Status: Accepted (user specification)

One FastAPI application with module domain/application/infrastructure/api boundaries. Celery performs asynchronous follow-up only. Bootstrap implements identity, organization, audit and core foundations. All other modules remain absent until their milestone. Business writes enter application commands; routers orchestrate HTTP only.
