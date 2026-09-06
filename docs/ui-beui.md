# Forge ERP · beUI reconstruction

The user canceled BoardUI on 2026-09-06. Its unfinished changes were archived outside the repository and this branch starts from accepted AI v0.11 (`21cb299`). The old green ERP shell/theme and the canceled BoardUI presentation are not used.

## Reference and installation

- Official website and demo: https://beui.dev/components/motion/animated-sidebar
- Official agent guide: https://beui.dev/docs/ai-agents.md
- Official skill: `starc007/ui-components/skills/beui`, installed at commit `04d6f76e9e67e35cded996b1b8d08a5ddcebc13a` into the user's Codex skills directory.
- Source installation uses the official registry JSON's declared files and dependencies. The registry is configured in `apps/web/components.json`. The license, original file hashes and registry item list are in `docs/vendor/beui`.
- There is no `beui` runtime package. We use its actual React/Motion source. New dependencies are Motion, TanStack Virtual, Geist and next-themes; framework versions remain unchanged.

## Presentation delivered

Official Current dark theme by default, with the official animated theme toggle for Current light. Geist/Geist Mono, 256px collapsible sidebar/68px icon rail, mobile sheet, official capsule buttons and fields, animated tabs, natural-height Table pages, badges, command palette, and agent message/composer/approval components.

Coverage: login, shared shell, dashboard, AI conversation and draft review, products and foundational records, customers/suppliers, inventory, purchasing, sales, funds, replenishment, imports, profile and settings including model providers. Reports continues to show its real availability status; the redesign does not fabricate report data or add business capability.

The ERP-specific content is composed from official beUI parts. Native business selects and Base UI editor dialogs retain their existing accessible interaction/transaction locks, using the new theme and dimensions. The shell, typography, colors, spacing and motion follow the official examples; this is an ERP adaptation, not a copy of the documentation website or its fictional demo records.

## Contracts preserved

Same-origin `/api`, opaque cookie session, permissions, tenant/identity cache keys, exact server quantities/prices/money, immutable retry keys/bodies, transaction uncertainty locks, AI human review and secrets never returned to the browser. No API, database or migration changes. A theme preference is the only newly persisted browser setting (`forge-theme`, written after a user changes theme); it contains no business records or credentials.

See ADR0019 for narrow source adaptations: native form/RHF reset behavior, full multiline Table rows, accessible tabs, Next navigation, focus management and approval locking. The beUI compiler-migration warnings remain visible in lint only for the explicitly listed vendor source files; application lint remains strict, and ordinary hooks, dependency, TypeScript and accessibility checks remain enabled. No React Compiler transformation is enabled for this application.

## Run and inspect

Use the existing repository commands: `make env install infra migrate seed`, then the usual API/worker/frontend start commands in the README. Frontend checks are `pnpm --filter @forge/web lint`, `pnpm --filter @forge/web typecheck`, `pnpm --filter @forge/web test`, `pnpm --filter @forge/web build`. Browser workflows run with `bash scripts/test_browser_with_worker.sh` after the production build.

Local preview: http://localhost:3100. The theme switch is in the top-right corner. All existing accounts and model provider configuration are retained.

## Verification record

Pending final integration evidence. Do not treat earlier Bootstrap/AI results as proof for this UI branch. New focused coverage includes real native input/RHF setValue/reset, preserved exact decimal strings, file inputs, controlled fields, actual submit/disabled behavior, tab permission changes, complete multiline server pages and browser keyboard/mobile/theme/AI submission flows.

Browser reference screenshots and local UI screenshots are generated using the repository's Chromium runner. The Codex in-app browser automation tool fails at its sandbox URI initialization in this environment; opening a tab through the UI tool alone is not counted as browser verification.
