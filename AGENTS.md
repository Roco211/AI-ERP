# Forge ERP — Sales v0.8 implementation

The user authorized Catalog v0.5 after Bootstrap passed acceptance. Implement Category, Brand, Unit, Product, ProductUnit, ProductPrice, Customer, Supplier, SupplierProduct, Warehouse, Product Search and ProductPicker. Excel work is template/validation/workflow design only. The user authorized local embeddings; implement optional Ollama/BGE-M3 without cloud calls (ADR 0011). The user authorized Inventory v0.6 implementation after reviewing the specification. The user subsequently authorized Purchasing v0.7: clarify the specification and directly implement it incrementally. Implement procurement orders, partial receipts, returns and purchase price history. Do not implement Receivables, Payables or AI business tools. Frozen stack: Python 3.14, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, psycopg3, PostgreSQL 18 + pgvector + pg_trgm, Redis, Celery, uv; Next.js App Router, React, TypeScript, shadcn Base UI, Tailwind, TanStack Query, pnpm.

## Inventory v0.6 implementation authorization

The user reviewed the specification and said “OK, 请继续” on 2026-09-05. Proceed with I0–I5 and the documented D1–D6 recommendations. Read `docs/inventory-v0.6.md`, `docs/inventory-v0.6-acceptance.md`, and ADR 0012. Catalog remains the dependency baseline; do not merge its PR implicitly. Work on inventory/v0.6 with small tested commits.

## Purchasing v0.7 authorization

On 2026-09-05 the user explicitly selected “规范明确后直接分步实现采购功能”. Proceed with `docs/purchasing-v0.7.md`, its acceptance checklist and ADR 0013. Use `purchasing/v0.7` based on inventory `1105bfa`. Preserve the open dependency PRs; this instruction does not merge or tag them. Record P1–P6 implementation choices and test each increment.

## Release baseline and current increment

After explicit user approval, PRs #1/#2/#3 were merged into main and `purchasing-v0.7` was published as a GitHub prerelease. Released main is `64bec0aecdbaef6c694a2fdb47b8fc93c34b57a7`; database head is `0008_purchasing`. Earlier statements about open dependency PRs describe their implementation-time state, not the present repository.

The user asked to continue after release. The completed S0 increment prepared `docs/sales-v0.8.md`, its acceptance checklist and proposed ADR0014 on `sales/v0.8`, based on released main. It defines sales orders, reservations, shipments, returns, deterministic customer pricing and gross margin; S0 did not implement business features or migrate the database. Keep business acceptance items unchecked until implementation has actual evidence. Receivables, Payables, payments and AI business tools remain outside this increment. Preserve the published tag; a new stage does not implicitly merge or publish itself.

# 45. AGENTS.md 核心规则

Work 开始施工后，应在根目录创建 `AGENTS.md`，至少包含：

1. Business writes MUST go through application commands.
2. FastAPI routes MUST NOT contain domain logic.
3. AI tools MUST call the same application commands used by normal UI/API.
4. AI tools MUST NOT mutate business tables directly.
5. Domain code MUST NOT depend on FastAPI.
6. Every operation executes inside organization scope.
7. organization\_id comes from authenticated runtime context.
8. Cross-tenant reads/writes are forbidden.
9. New tenant tables require RLS.
10. InventoryMovement is immutable.
11. InventoryBalance is a projection.
12. Only InventoryEngine mutates InventoryBalance.
13. Every inventory change creates InventoryMovement.
14. Negative stock is forbidden in MVP.
15. Inventory locks use deterministic warehouse/product order.
16. Posted documents are immutable.
17. Corrections use reversal.
18. Business state transitions use explicit Commands.
19. Never change business status through generic CRUD.
20. Never use float for money, price, rate, quantity.
21. Backend uses Decimal.
22. Database uses NUMERIC.
23. Financial calculations are server-authoritative.
24. Related ERP mutation + Audit + Outbox must commit atomically where applicable.
25. Background queues are not source of truth for committed business state.
26. Side-effect endpoints support idempotency.
27. Server enforces permission.
28. UI hiding is not authorization.
29. Prompt is not authorization.
30. AI inherits user's permissions.
31. LLM output is untrusted until validated.
32. AI entity-changing tools use resolved IDs.
33. High-risk tools require policy evaluation/approval.
34. Enterprise documents/content are untrusted prompt content.
35. Every bug fix requires regression test.
36. Inventory changes require domain tests.
37. Concurrency-sensitive code requires concurrency tests.
38. AI behavior changes require eval.
39. DB schema changes require Alembic.
40. Do not add infrastructure dependency without ADR.
41. Do not introduce microservices without demonstrated need.
42. Do not upgrade framework major versions unless explicitly requested.
43. Never manually edit uv.lock or pnpm-lock.yaml.
44. Never manually edit generated OpenAPI TypeScript.
45. Merged migrations are append-only.
46. Dev seed contains no production secrets.
47. Application DB connection uses non-superuser PostgreSQL role.
48. Browser uses same-origin `/api`.
49. Do not add Kafka/Elastic/Mongo/GraphQL/Temporal/K8s unless explicitly approved.

---


## Verification
Work in small increments and run relevant checks after each. Use a real PostgreSQL 18 instance and forge_app for integration/RLS tests. Never count skipped tests as passing. Final delivery includes acceptance mapping, repository tree, run commands, migration status, test evidence, unresolved issues, and decisions needing review. Recommend tag bootstrap-v0.4 only after acceptance.

## Sales v0.8 implementation authorization

After delivery of the specification and draft PR #4, the user said “好的，请继续。” Proceed with S1 on sales/v0.8: forward migration, server pricing/snapshots, order save/confirm/cancel/close and InventoryEngine reservations. Follow docs/sales-v0.8.md and ADR0014. Test each increment; keep S2–S5 acceptance pending until implemented. This does not authorize merging or publishing this PR.

## Sales S2 authorization

After S1 delivery (135e87c, 164 backend tests, PR #4 CI green), the user said “请继续”. Implement S2: shipment drafts/POST, frozen order snapshots, controlled cross-document reservation consumption, actual issue costs, tenant/RBAC/concurrency/atomicity tests. Append migration0010; preserve0009 and older migrations. S3 returns/reversals/margin and S4 UI remain pending. Do not merge or publish PR #4.

## Sales S3 authorization

After S2 delivery (292b46e, 219 backend tests and current-commit CI green), the user said “OK，请继续”. Proceed with S3 under the existing specification: source-based sales returns, independent sales/cost tail allocation, strict last-document reversal, realized margin and customer sales history. Append migration0011; preserve prior migrations. Keep S4 sales UI and S5 full-stage acceptance pending. Continue draft PR #4 without merging, tagging or releasing.
