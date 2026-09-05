# ADR 0002 — PostgreSQL primary database
Status: Accepted (user specification)

PostgreSQL 18, pgvector and pg_trgm; database uuidv7() IDs. SQLAlchemy 2 async with psycopg3, Alembic schema history. Redis is a Celery broker, never business truth. Development uses dedicated Docker volumes and loopback ports 55438/56379. Applications use forge_app; migration/seed use a distinct elevated connection. Container image major tags and lockfiles are the current version policy; pin deployment image digests before production.
