# Catalog v0.5 implementation and acceptance

Authority: migration context sections 6–8, 14–31, 43–46 and 54–57; the user's six-increment plan and subsequent instruction to begin.

1. Category / Brand / Unit: tenant-isolated lists, forms, activation, optimistic versioning, audit/outbox/idempotency; validate with real database tests.
2. Product: SKU/barcode, category/brand, hardware specifications, JSONB attribute validation, base unit, list/detail/edit/deactivate.
3. ProductUnit / ProductPrice: direct base-unit conversion, immutable snapshot value, Decimal prices, explicit tiers and customer overrides; no sales/purchase history.
4. Customer / Supplier / SupplierProduct: basic contact records, price tier, supplier item code, purchase unit, lead time and preferred relation.
5. Warehouse / Search: warehouse metadata only; exact matching, trigram and structured filters. External embedding integration depends on the user's separate decision.
6. ProductPicker / Excel design / acceptance: keyboard discovery and unit choice; spreadsheet template and row-validation/duplicate/preview design; no full import platform.

Every increment must pass relevant tests. Final checks include Bootstrap regressions, real PostgreSQL RLS and composite-reference tests, concurrency/idempotency, Decimal and snapshot properties, API contract drift, frontend interaction/smoke tests, migration from v0.4 and clean DB, production build and GitHub CI. Preserve public repository history; work on catalog/v0.5 until reviewed.

## Local commands

Use the root `.env` generated during Bootstrap. `make infra migrate seed` starts infrastructure, upgrades existing or empty databases and ensures the demo administrator has Catalog permissions. `make seed-catalog` optionally creates eight hardware demo products and related dictionaries, contacts, conversions, prices and a warehouse; repeatable without overwriting user-edited master records. `make api` and `make web` open the app at http://localhost:3100. Catalog lives under 商品 / 客户 / 供应商; 设置 contains 分类 / 品牌 / 单位 / 仓库. 商品 → 快捷选品 supports keyboard lookup and conversion preview.

`make lint test contract` verifies backend/frontend contracts and tests. Run `pnpm build` before `pnpm test:e2e`; browser tests need Playwright Chromium and its Linux dependencies. `make seed-catalog` is opt-in and not required for tests. Existing Bootstrap seed credentials remain unchanged. Semantic search is not enabled; see ADR 0010.
