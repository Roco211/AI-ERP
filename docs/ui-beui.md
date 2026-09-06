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

The initial reconstruction preserved same-origin `/api`, opaque cookie session, permissions, tenant/identity cache keys, exact server quantities/prices/money, immutable retry keys/bodies, transaction uncertainty locks, AI human review and secrets never returned to the browser. That initial UI-only increment made no API, database or migration changes. Its only new persisted browser setting was the theme preference (`forge-theme`, written after a user changes theme); it contains no business records or credentials.

The later streaming-assistant authorization expands the response transport, public turn DTO and conversation behavior. Its current scope and separate acceptance checklist are in [ai-streaming-experience.md](ai-streaming-experience.md). It keeps the same security, evidence and reviewed-write boundaries and introduces no migration or infrastructure.

See ADR0019 for narrow source adaptations: native form/RHF reset behavior, full multiline Table rows, accessible tabs, Next navigation, focus management and approval locking. The beUI compiler-migration warnings remain visible in lint only for the explicitly listed vendor source files; application lint remains strict, and ordinary hooks, dependency, TypeScript and accessibility checks remain enabled. No React Compiler transformation is enabled for this application.

## Run and inspect

The existing runtime is in WSL `Ubuntu-24.04`; `D:\Codex\Forge ERP` maps the same original working tree for editing. Run Git, dependency commands, checks, builds and application processes inside WSL. Use the API/worker/frontend start commands in the README. Fresh-environment setup commands in the README are not instructions to reseed the retained local data. Frontend checks are `pnpm --filter @forge/web lint`, `pnpm --filter @forge/web typecheck`, `pnpm --filter @forge/web test`, `pnpm --filter @forge/web build`. Browser workflows run with `bash scripts/test_browser_with_worker.sh` after the production build.

Local preview: http://localhost:3100. The theme switch is in the top-right corner. All existing accounts and model provider configuration are retained.

## Verification contract and recorded checks

The following counts describe the initial beUI acceptance at `80c5f1b`. The subsequent assistant workspace experience increment and its verification contract are documented in [ai-experience.md](ai-experience.md). Neither record proves the later streaming-assistant increment, whose checks are tracked separately in [ai-streaming-experience.md](ai-streaming-experience.md).

The rebuilt UI passed the complete local frontend suite (155 tests in 15 files) and the eight new browser scenarios. Production compilation and type checking passed. Lint reports zero errors and 20 visible warnings. Full cross-module browser and GitHub Actions results are recorded with their exact revision in [PR #8](https://github.com/Roco211/AI-ERP/pull/8), which is the final verification record. Historical Bootstrap/AI results do not constitute evidence for this UI branch. New focused coverage includes real native input/RHF setValue/reset, preserved exact decimal strings, file inputs, controlled fields, actual submit/disabled behavior, tab permission changes, complete multiline server pages and browser keyboard/mobile/theme/AI submission flows.

Browser reference screenshots and local UI screenshots are generated using the repository's Chromium runner. The Codex in-app browser automation tool fails at its sandbox URI initialization in this environment; opening a tab through the UI tool alone is not counted as browser verification.

## Streaming assistant reference increment

The 2026-09-06 continuation adds the official `AgentActivity` files from [the Chat App registry](https://beui.dev/r/chat-app.json), plus `ThinkingShimmer`, `TextShimmer` and its helper from [the Thinking Shimmer registry](https://beui.dev/r/thinking-shimmer.json). The source manifest records raw source-content hashes for this retrieval while preserving every historical file hash. The activity display receives actual server application states; it never substitutes the demo's fictional activity or hidden model reasoning.

The assistant now uses a two-column desktop history/chat layout, opening the existing evidence/draft panel on demand. Result cards distinguish product, inventory, overview, replenishment, sales/purchase preview and creation-receipt data, while retaining server decimal strings, source scope/time and explicit draft review. Hidden editors stay mounted. Real SSE response behavior, recovery with the original request key and gentle guidance after repeated casual conversation are described and verified under the new scope document. The existing WSL runtime and encrypted web-managed model provider continue to be used.
