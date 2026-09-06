# ADR 0019 — beUI presentation, existing ERP contracts

Date: 2026-09-06. Status: implemented, pending review.

The user canceled BoardUI and requested installing the official beUI MCP or Skill, abandoning the old UI and matching the official demos. Install the official `skills/beui` from `starc007/ui-components` at `04d6f76e9e67e35cded996b1b8d08a5ddcebc13a`. Use the live shadcn registry's declared source-file install flow described by https://beui.dev/docs/ai-agents.md; register `@beui` in components.json. Runtime source is vendored with the MIT license and manifest in docs/vendor/beui.

Adopt the official Current dark/light theme (dark by default), Geist/Geist Mono, animated sidebar demo composition, capsule buttons/inputs/tabs, Table and agent composer/message/approval components. Keep Next.js App Router, React, Tailwind, pnpm, TanStack Query and all same-origin APIs. beUI is React/Motion source, not a Base UI wrapper; this is an explicit presentation-library decision. Keep Base UI button/dialog semantics at application boundaries where required, including critical business editors. No database migration or additional backend infrastructure.

## Narrow compatibility patches

- Application Button bridges the existing variant/submit/disabled contract to the official motion Button and Base UI.
- Input adds a real native change-event bridge for React Hook Form and skips controlled values on file inputs. Existing native names, refs, autocomplete and validation remain intact. Native uncontrolled fields stay uncontrolled so RHF setValue/reset cannot be overwritten by an internal value.
- Tabs accept native disabled/ARIA props, visible keyboard focus and roving Arrow/Home/End navigation. Permission changes that remove the current tab retain a keyboard entry point without automatically switching business context. Inactive ERP editors unmount; existing permission/identity and uncertain-write boundaries are preserved.
- Table adds `virtualized=false` for server-paginated ERP pages so multiline facts and expanded price provenance are visible. Default upstream virtualization is unchanged. No client-side amount recalculation or unauthorized inline edits.
- Sidebar adds a navigation event hook for Next client routing, retaining normal modifier-click behavior. Mobile focus lists omit hidden, inert and negative-tabindex elements; the inset is a neutral wrapper around the single application main landmark.
- CommandPalette adds localizable names, modal focus containment, inert background and focus restoration. Commands remain the existing navigation routes.
- PromptInput adds a send accessible name. ApprovalCard adds independent approve locking and localizable labels. Human review, server facts, immutable retry payloads and key handling remain authoritative.

These compatibility patches must be reviewed during upstream refreshes. The source manifest records the original installed file hashes. Existing business selects keep native accessible interaction with the beUI field dimensions and theme; business modals retain Base UI focus/locking semantics. This is preferable to regressing known keyboard and transaction behavior to adopt a less capable demo interaction.

## Validation

Run lint, typecheck, Vitest, production build and browser workflows, including mobile keyboard navigation, login, unknown-submission locks, AI draft review and reduced motion. CI includes the `ui/**` branch. Record actual results in docs/ui-beui.md; historical baseline results do not constitute acceptance of this change.
