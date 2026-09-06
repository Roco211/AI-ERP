# Forge ERP — beUI web reconstruction

## Current UI authorization (2026-09-06)

The user explicitly canceled the unfinished BoardUI redesign and requested installing the official beUI MCP or Skill and fully replacing the old UI to match beui.dev demos. The canceled edits are archived under task work/boardui-stopped; do not resume or publish them. Work on ui/beui from accepted AI baseline `21cb299`. Official beUI skill installed from starc007/ui-components at `04d6f76e9e67e35cded996b1b8d08a5ddcebc13a`. Use official beUI source, tokens and example compositions, replacing the old presentation rather than retaining its theme. Keep existing business APIs, records, tenant/RLS/permissions, precise server amounts, identity caches, idempotency and human draft review. beUI uses Motion/React primitives; integrate them through existing application-facing adapters, retaining Base UI for accessibility where appropriate and documenting the presentation-library choice. No backend redesign, new business stage, infrastructure, production deployment, merge or release. Verify real browser workflows, component behavior, responsive layout, keyboard access, reduced motion, frontend checks/build and CI. Report actual browser/tool limitations and reference provenance.


## Current AI implementation authorization (2026-09-06)

Funds and Operations were merged/released as prereleases; main is `ed7749d03b291073d2d86bb846dfa87130422be5`, database `0015_reporting`. See `../Forge-ERP-v0.9-v0.10-release-record.md`. User requested continuing and explicitly selected “明确规范后直接分步实现” for v0.11: inventory/operating questions, daily briefs, natural-language sales/purchase draft previews and reviewed creation. This supersedes historical AI prohibitions only for this scope. Work on `ai/v0.11`; read `docs/ai-v0.11.md`, its acceptance and ADR0017. Complete A0–A5 incrementally with tests. Do not stop at the specification or claim mock responses prove real model acceptance. The user then explicitly required WEB-configurable LLM suppliers: implement organization-admin settings for custom OpenAI-compatible base URL/model/encrypted key, enabling and synthetic connection tests. Do not require terminal credential entry or freeze the earlier preset provider. Provider-config changes invalidate old conversation context; credentials never appear in read DTOs/logs/audit.

Keep one LangGraph assistant and fixed tools; current authenticated identity/permissions never come from the model/checkpoint. New AI tables require organization+owner FORCE RLS. Existing Query output must pass its Pydantic DTO before model use. Model waits occur outside database transactions; respect reporting snapshot vs pricing FOR SHARE differences. All displayed business numbers and sources come from server facts. No overdue claims without due-date facts. Risk 1 is reviewed creation only; no confirmation/POST/cash or other Risk 2–4 tools. AI draft receipt, Command and Audit/Outbox commit atomically and remain deduplicated after generic idempotency expiry. Preserve existing data/migrations/tags; no implicit merge/release, production deployment or v1.0 scope.

## Operations implementation baseline

Funds final commit `7fded4f730d87a8bedaf60491bbaf52f7d358564` passed push CI 33983285324 and PR CI 33983287501: 473 backend tests, 74 frontend tests and 14 browser scenarios. Its 27-item acceptance is complete; exact evidence is in `../Forge-ERP-Funds-v0.9-verification.json`. Operations implementation now proceeds on `operations/v0.10` from that accepted commit, with database baseline `0012_funds`. Read `docs/operations-v0.10.md`, its 42-item checklist and ADR0016. Implement O1–O5 within the existing authorization; do not stop at funds or automatically merge/tag/release.

## Current authorization (2026-09-06)

The user explicitly requested “OK，请进行阶段v0.9和V0.10” after reviewing the development plan. This authorizes specification and incremental implementation of lightweight funds (receivables, receipts, payables, payments, allocations, return credits/refunds, reversals, opening balances), followed by formal Excel import, deterministic replenishment and a traceable operating overview. Finish and verify v0.9 before implementing v0.10 business features. No further permission is required for this authorized implementation. Earlier stage-specific prohibitions below are historical boundaries, superseded only for this scope. AI business tools, general accounting and new infrastructure remain excluded.

Sales v0.8 has been merged and published: main `5a5c0e07053e1935ae11fdbf78609246308c8901`, tag `sales-v0.8`, database head `0011_sales_returns`. See `../Forge-ERP-sales-v0.8-release-record.md`. Preserve existing data, posted facts and published tags. Work on `funds/v0.9`, then an operations branch from its accepted result. This request authorizes implementation and reviewable PRs, not automatic production deployment. Keep unverified acceptance items unchecked.

Read `docs/funds-v0.9.md` and `docs/funds-v0.9-acceptance.md` for the current stage. Core writes and their Audit/Outbox/idempotency receipt must remain atomic; money stays Decimal/NUMERIC, never browser arithmetic. Every new tenant table requires FORCE RLS and composite tenant references. Existing migrations are append-only. Document unresolved policy choices rather than silently changing existing sales/purchasing/inventory semantics.

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

## Sales S4 authorization

After S3 delivery (cc023a5, 305 backend tests, current CI green), the user said “继续”. Implement S4 sales workspace: orders, shipments, returns/reversal, authorized margin/history, quote provenance, keyboard selection, server amounts, independent permissions and safe retries. Extend read DTOs where needed to avoid browser business arithmetic, without changing domain commands or adding migrations. Add real browser and frontend regressions. Keep S5 optional seed/final-stage acceptance and publishing pending; do not merge or release PR #4.

## Sales S5 authorization

After S4 delivery (2cd3d93, 316 backend / 37 frontend / 10 browser tests and both current CI runs successful), the user said “请继续”. Complete S5 under the existing specification: finish shipment idempotency coverage, implement an optional development-only Command-based sales seed that preserves existing facts, run frozen installation and full regression/CI, and finalize the 46-item acceptance and delivery evidence. Keep existing architecture and business semantics; resolve real defects with regression tests. No Receivables/Payables/AI work, no new infrastructure, and no implicit merge, tag creation or release. Only recommend sales-v0.8 once full acceptance is verified.

## Sales S5 completion record

S1–S5 implementation is complete: 357 backend tests, 44 frontend tests and 11 production-browser scenarios passed locally. Code acceptance commit `9d7961e` passed both push CI 33977824715 and PR CI 33977827345. The final documentation commit must also pass both CI runs; its exact SHA and results are recorded in `../Forge-ERP-Sales-v0.8-verification.json` after verification. See `docs/sales-v0.8-delivery.md`, the 46-item acceptance checklist and repository tree for the final state. Application metadata is 0.8.0 / Sales; migration head remains 0011_sales_returns. PR #4 remains a draft, with no sales tag, merge or release performed. Do not infer authorization for funds or AI business modules from this completed increment.

## Funds v0.9 completion gate

F0–F5 implementation and local acceptance are complete. The baseline `f8a110b` passed both CI runs 33982396517/33982398145 with 471 backend tests. Final local verification is 74 frontend tests and 14 production-browser scenarios; two real zero-price source cases additionally passed. Independent review fixed frontend reversal permissions and the test-fixture/Outbox cleanup lock order without changing business rules. The local database is 0012_funds; application metadata is 0.9.0 / Funds.

See docs/funds-v0.9-delivery.md, its 27-item acceptance and tree. The final commit includes these last review fixes and documentation and MUST pass its own push/PR CI, recorded in ../Forge-ERP-Funds-v0.9-verification.json. Only after those results are verified may the already-authorized v0.10 implementation begin. Use operations/v0.10 from that accepted result; read docs/operations-v0.10.md and ADR0016, preserve historical facts and published tags, and do not implement AI business tools or automatically merge/release.


## Operations O5 local verification

Operations implementation on `operations/v0.10` is based on accepted funds `7fded4f`. Backend increment `67e4d58` and workspace/browser increment `3189c7c` implement O1–O4. Local full backend: 643 passed; final imports/migrations after eight additional safety cases and first-batch query optimization: 116 passed. Frontend: 99 passed. Production browser via the CI-owned worker/beat script: 18 passed. These runs overlap and must not be summed. The final CI must run the entire current suite.

Local runtime is 0.10.0 / Operations, head0015_reporting, with a pre-upgrade backup, existing DEMO/user/password/sales facts retained, funds not auto-activated, new FORCE RLS tables, and zero remaining BROWSER tenants or scoped fault triggers. The unpublished import claim index change has been applied to this already-upgraded local database and is also present in migration0013 for clean installs.

See docs/operations-v0.10-delivery.md, its 42-item checklist and full tree. PR #6 targets funds/v0.9 (#5). Exact last commit and both final CI results belong in ../Forge-ERP-Operations-v0.10-verification.json after verification; do not claim old CI covers a new commit. No implicit merge/tag/release, production deployment, AI business tools or additional stage implementation is authorized.


## Operations accepted code and final-delivery gate

Code `3189c7c5b169c4218eabee23ed8c928bdfe26107` passed push CI33985790211 and PR CI33985818640, each with 651 backend, 99 frontend and 18 production-browser tests. The 42-item implementation acceptance and delivery documentation are complete. The final documentation commit MUST also pass its own push/PR CI before final delivery; record its exact SHA and results in ../Forge-ERP-Operations-v0.10-verification.json. No required implementation remains after that gate. Do not start another phase or merge/tag/release implicitly.


## AI v0.11 final-delivery gate

A0–A5 implementation is complete on `ai/v0.11`, with the user's web-managed provider requirement implemented. Code `0c585f42ad973f0c3a1466354474af829b6d85ee` passed push CI33993106772 and PR CI33993119410, each with 1069 backend, 136 frontend and 24 production-browser tests. Real configured-model product/inventory queries and both exact draft previews passed; previews were rejected without changing order counts. An ambiguous model request was safely rejected, not counted as correct model clarification. See the 47-item acceptance and delivery/evidence documents.

The final documentation commit MUST pass its own push/PR CI before final delivery. Record its exact SHA and counts in `../Forge-ERP-AI-v0.11-verification.json`, without treating the code CI as its proof. Live runtime is 0.11.0, migration head0018_ai_retention; original account, encrypted provider configuration and posted facts are preserved. Browser test tenants/fault objects have no residue. LangSmith remote remains disabled/unconfigured; its metadata-only boundary is tested locally. Do not start v1.0, merge PR #7, tag or release without user authorization.
