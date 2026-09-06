# AI assistant streaming and beUI experience

## Scope and baseline

This is the user's 2026-09-06 continuation of `ui/beui` and draft PR #8. It follows the accepted beUI reconstruction and the conversation workspace increment. The user now authorizes real application progress, streamed replies, casual conversation with a return to ERP after repeated casual turns, and distinct business result cards. This expands the earlier UI-only boundary: the assistant response transport, public turn DTO and existing graph's conversation behavior change in this increment.

The scope keeps one LangGraph assistant, the fixed allowlisted business tools, current application Queries and Commands, authenticated cookie/tenant/RBAC checks, evidence provenance, owner RLS, retention and explicit human draft approval. It introduces no business stage, infrastructure, database migration, merge or release. The database head remains `0018_ai_retention`.

## Official component provenance

The presentation uses the official [beUI Chat App](https://beui.dev/r/chat-app.json) and [Thinking Shimmer](https://beui.dev/r/thinking-shimmer.json) registry sources, fetched on 2026-09-06. There is no beUI runtime package. The application composes the installed `ChatApp`, `Message`, `MessageScroller`, `PromptInput`, `AgentActivity`, `ThinkingShimmer`, `ApprovalCard` and button exports.

The new vendor files are limited to:

```text
apps/web/components/agents/agent-activity/index.tsx
apps/web/components/agents/agent-activity/activity-row.tsx
apps/web/components/agents/agent-activity/types.ts
apps/web/components/agents/loading-states/thinking-shimmer.tsx
apps/web/components/motion/text-shimmer.tsx
apps/web/lib/text-shimmer.ts
```

The exact upstream UTF-8 source-content SHA-256 values and normalized installed hashes are recorded separately in [the source manifest](vendor/beui/source-manifest.json). `AgentActivity` uses its component-specific attribution comment in place of the Chat App registry comment. The two registry versions of `ThinkingShimmer` differ only in their attribution comment, which is omitted in the installed file. The remaining four files match the retrieved source content. Historical source hashes and the initial Chat App shell-only installation record are preserved. No fictional demo activity or business records, paid BoardUI source, or Shiki installation is included.

## Public activity and streamed responses

The activity disclosure describes actual application operations: selecting the permitted business capability, executing a registered query, validating a draft preview, and producing a public reply. An operation starts when its corresponding application work starts and completes after that work returns. Failure remains visible as incomplete work. The UI uses only server-recorded activity; it does not present hidden reasoning, provider reasoning fields, fabricated tool calls, token counts or timer-generated progress.

Public progress is stored in the existing bounded private turn JSON, capped at 32 activity entries. Activity includes its ID, kind, user-facing title, state and start/finish timestamps. Existing organization-and-owner isolation, lease/attempt fencing, conversation retention and provider-context invalidation continue to apply. The model is awaited outside database transactions. Authorization is rechecked while publishing output, including queued events and heartbeats.

The existing assistant message, brief and retry flow negotiates SSE through `Accept: text/event-stream`. Its envelope has four event types:

| Event | Meaning |
| --- | --- |
| `snapshot` | A server-authored turn snapshot, including current public activity and available query evidence. |
| `delta` | Actual public reply text from the current run. Provider casual text is buffered through sentence validation before exposure; the deterministic ERP guidance may also be emitted as a delta. |
| `complete` | The terminal turn returned by the same application execution. It is the final response used to settle the submission. |
| `error` | A bounded public error with its status/code and request identifier. |

Business result text and cards continue to be based on validated Query DTOs and draft previews. Query snapshots are published when queries finish. The UI does not split a completed response into timed characters to imitate upstream streaming. SSE keep-alive comments carry no business activity and do not increment the visible trace.

The reader bounds frame size, total response size and accumulated delta text, preserves UTF-8 across chunk boundaries, validates event type and turn identity, and rejects incomplete/malformed frames. A final `complete` event or a compatible terminal JSON response settles the submission. A `RUNNING` acknowledgement, partial stream or early disconnect does not prove completion.

Disconnect recovery uses the existing pending-submission behavior: the original request body and idempotency key remain locked for **重试原提交**. There is no new key, implicit duplicate send or client-side business commit. Server conversation history is reread to reconcile committed results. A cancelled unfinished run retains its existing lease; recovery can require waiting for that lease's 120-second lifetime to expire. When identity, permission or provider context changes, the client stops receiving that run and clears its visible private state.

## Casual conversation and return to ERP

Casual conversation is a route in the same graph. The structured decision still selects between casual response, allowlisted business work, clarification and unsupported requests. Explicit enterprise queries cannot use the casual route to supply unsupported business figures or claim an operation happened. Public casual text is checked before display for unsupported business assertions and hidden-reasoning markup.

The server counts the immediately preceding completed turns it classified as casual in this conversation. Failed turns do not increase the count; a business request interrupts the casual sequence even when it fails. Starting with the third consecutive casual turn, it appends a gentle invitation to use Forge ERP, such as checking inventory or preparing a reviewed draft. The user can continue chatting. The browser and model do not control the counter, and the invitation does not execute a query or create a document. `interaction` and `guided` describe the persisted server result.

## Workspace and business cards

The desktop opens with a history rail and a focused chat/composer. **切换业务依据与草稿** opens the third panel when needed; a result card can select its corresponding turn and open the same panel. Mobile displays one panel at a time. Hiding the review panel or changing which turn is shown does not unmount its draft editors. Unsubmitted edits and uncertainty locks therefore remain attached to their existing editor while the user inspects another result or changes screen size.

`BusinessResultCards({ turn, onReview })` renders compact summaries within the thread. Its callback only opens the mounted review/evidence panel. It contains no business write or duplicate draft editor.

| Result | Summary composition |
| --- | --- |
| Product search/details | Product identity, specification and available server price strings. |
| Inventory/low stock | Product and warehouse, available quantity, and existing on-hand/reserved or minimum-stock facts. |
| Operating overview/daily brief evidence | Representative server metrics across business groups, with the source scope and statistical caveats. No inferred trend or historical-period balance. |
| Replenishment | Suggested base quantity, unit, current availability, inbound purchase quantity, preferred supplier and server explanation. |
| Sales/purchase draft preview | Customer/supplier, warehouse, line quantities and amounts, server total, warnings and **复核草稿**. |
| Creation receipt | Persisted draft state and approved business-page link, without reusing the old preview total as a fresh receipt amount. |
| Other registered query | A bounded factual summary with access to the complete evidence. |

Row previews are bounded to three rows; larger results explicitly point to full evidence. Money, quantity and price strings are displayed unchanged: the browser does no arithmetic, number parsing, rounding, unit conversion or availability calculation. Time, scope and server statistical caveats remain visible. Source links are revalidated against the existing route/parameter allowlist. Business text is rendered as escaped React text. Buttons and links remain keyboard accessible; cards add no custom animation, while the installed animated primitives honor reduced-motion settings. A separate polite status outside the busy chat log announces the current stage without reading every text delta. Each draft's submitting label follows its own approval request, including an original-key retry; unrelated chat only applies the existing global lock.

## Runtime and repository map

Development continues from `D:\Codex\Forge ERP`, the existing junction to the original working tree. Run Git, dependency commands, tests, builds and application processes in WSL `Ubuntu-24.04`; do not install Windows dependencies into the shared tree. The existing encrypted, web-managed provider remains configured, and no credential appears in this document or verification output. Preview is [localhost:3100](http://localhost:3100); the API remains at port 8100 and the browser uses same-origin `/api`.

```text
apps/api/src/forge_erp/modules/assistant/
  application/runtime.py, state.py, streaming.py
  domain/casual.py, chat.py
  infrastructure/provider.py
apps/web/features/ai/
  assistant-client.ts, assistant-stream.ts, turn-activity.tsx
  workspace.tsx, business-result-cards.tsx, context-panel.tsx
apps/api/tests/test_assistant_streaming.py
apps/web/tests/assistant-stream.test.ts
apps/web/tests/assistant-live-workspace.test.tsx
apps/web/tests/assistant-result-cards.test.tsx
apps/web/e2e/assistant-experience.spec.ts
```

For example, from PowerShell:

```powershell
wsl.exe -d Ubuntu-24.04 --cd '/mnt/d/Codex/Forge ERP/apps/web' -- bash -lc 'pnpm exec vitest run tests/assistant-result-cards.test.tsx'
```

The main delivery must also record the actual final backend/frontend/browser commands, migration check, current model verification and exact tested revision. Do not substitute prior acceptance counts or controlled transport tests for a real configured-provider result.

## Acceptance evidence for this increment

The list below is specific to this scope. Unchecked items are pending final evidence, even where implementation or test files already exist. No older beUI or AI acceptance run is counted here.

- [x] Twelve focused business-card tests pass in WSL: exact large decimal strings, source time/scope, escaped text, bounded rows, distinct overview/replenishment presentation, statistical caveats, generic fallback, both draft kinds, review-only behavior, rejected/expired states and safe creation/source links. Command: `pnpm exec vitest run tests/assistant-result-cards.test.tsx`; result: 12 passed, 1 file, 2026-09-06 16:08 local start, 53.34 seconds.
- [x] Controlled backend transport proves actual progress precedes completion and public deltas originate from the current provider stream or the explicit server guidance. Covered by the final related backend regression: 112 passed.
- [x] Backend behavior evals cover first/second/third casual turns, sequence reset by business work, failed-business-turn handling, business/secret requests and public-content guard failures. Included in the same 112-test backend run, not an additional count.
- [x] Stream/parser tests cover UTF-8/chunk boundaries, malformed/oversized input, cross-turn events, terminal JSON compatibility and missing terminal completion: 27 tests in `assistant-stream.test.ts` passed.
- [x] Client integration tests prove original-body/key recovery after disconnect, uncertainty locking, one active submission and reconciliation with server history: 37 existing workspace tests plus 6 live-workspace tests passed. Together with the 27 parser tests, the focused run is 70 passed.
- [x] Focused backend tests verify session and owner/tenant access, permission/provider-context changes, lease fencing and authorization checks before queued stream output. These are part of the related 112-test run; final migration/retention-state verification remains a separate gate below.
- [x] Real browser verifies default two-column desktop, on-demand third panel, mobile navigation, in-thread cards, composer visibility and no horizontal page overflow: both `assistant-experience.spec.ts` scenarios passed after the desktop toggle correction.
- [x] Real browser verifies dirty draft editor identity/value survives panel toggling, turn changes and resizing; no preview button creates a business document. The same two-scenario run checks the input's original DOM identity and absence of business writes.
- [ ] Keyboard and reduced-motion behavior pass on the final UI.
- [x] The already-configured provider is tested separately through the actual Next proxy without revealing credentials. Seven real requests cover casual replies, third-turn guidance, business reset, an ambiguous product clarification and a successful exact-SKU inventory lookup. Detailed outcomes are recorded below; the initial ambiguous lookup is not counted as inventory success.
- [x] Backend lint/format checks passed across 204 files, and source Pyright passed. These static checks do not substitute for a full test-suite rerun.
- [x] Final production build passed and the WSL API/worker/beat/web were restarted successfully. Full `make lint` passed: Ruff/format, Pyright, frontend ESLint with zero errors and 21 existing component/TanStack warnings, and TypeScript. The final two UI fixes additionally passed their focused tests/lint and the final build's TypeScript check.
- [ ] Complete final frontend/backend/browser suites pass on the committed revision; record exact counts and revision separately from the targeted runs below.
- [ ] Database migration head is verified unchanged at `0018_ai_retention`, existing facts/provider configuration are retained, and temporary verification data is cleaned up.
- [ ] Final current-revision push/PR CI and the draft PR description reflect this expanded scope; no merge, tag, release or production deployment is performed.

## Recorded local regression results

These results refer to the current increment's working-tree runs reported during this continuation, not a final committed-SHA acceptance. The focused runs overlap the initial full-suite runs and must not be added together.

| Run | Actual outcome | Interpretation |
| --- | --- | --- |
| Initial complete backend suite | 1108 passed, 2 failed | The two test-boundary failures were corrected and verified in the targeted run below. A complete final local backend rerun has not been established here. |
| Final related backend regression | 112 passed | Focused verification after those corrections; it does not turn the earlier complete-suite result into a green full-suite run. |
| Backend static checks | Ruff/format passed for 204 files; source Pyright passed | Static checks are complete for the reported run. |
| Initial complete frontend suite | 222 passed, 3 failed | Followed by focused correction and verification; not a green final full-suite result. |
| Focused workspace and stream regressions | 70 passed: 37 existing workspace, 6 live-workspace, 27 parser | The previously failing affected flows pass in this focused run. This is one combined count. |
| Business cards | 12 passed | Separate card-focused evidence described above; also overlaps the complete frontend suite. |
| Production browser, assistant layout | 2 passed in 7.3 seconds | Desktop/mobile, exact cards, panel toggling, preserved draft DOM and composer visibility after the toggle correction. |
| Production browser, actual assistant API | 7 passed in 1.4 minutes | Real browser→Next→FastAPI→controlled provider streaming, early text before completion, repeated-casual guidance and reset, reviewed creation/replay, permission/provider isolation, brief and settings. |
| Final live-workspace accessibility regression | 6 passed | Independent phase announcement remains outside the busy log, excludes text deltas and clears at completion, alongside the existing stream recovery tests. Overlaps the earlier focused run. |
| Final workspace and draft-state regression | 38 passed | Includes unrelated chat not claiming a draft submission, actual approval state and original-key retry after loss of its response. Overlaps the earlier workspace run. |

The desktop review-toggle defect found afterward in a real browser was fixed, rebuilt and verified by the two layout scenarios above. The 70-test focused result does not cover that subsequent change. The complete cross-module browser suite and final exact-revision build/CI remain separate gates. The final committed revision and CI outcomes are recorded in the draft PR and the adjacent `Forge-ERP-AI-streaming-verification.json`, after the workflows finish; this document records evidence available when committed.

## Real configured-model evidence

The live probe ran in WSL with the existing DEMO login and configured provider, through `http://localhost:3100/api/v1/...`. It did not replace the provider with a controlled server, change provider configuration, approve a draft or create business documents. Credentials and cookies remained in memory. The seven requests used two newly created probe conversations, both deleted through their own conversation endpoint afterward.

| Real request | Observed outcome |
| --- | --- |
| Ordinary walking conversation | `COMPLETED`, casual, `guided=false`; 5 snapshots, 2 actual reply deltas and a completion. |
| Ordinary music conversation | `COMPLETED`, casual, `guided=false`; 5 snapshots, 2 reply deltas and a completion. |
| Third casual turn about a daily habit | `COMPLETED`, casual, `guided=true`; 5 snapshots, 3 deltas including the explicit server ERP invitation, and a completion. |
| Broad request for 螺栓 inventory | `COMPLETED`, business clarification after `search_products`; no `get_inventory` evidence. The probe records its inventory-success check as failed, correctly preserving this ambiguous result. |
| Casual conversation after that business clarification | `COMPLETED`, casual, `guided=false`; reset behavior observed. |
| Follow-up for the observed exact SKU `INV-DEMO-BOLT` | `COMPLETED`, business, `guided=false`; `search_products` followed by `get_inventory`, with one actual inventory evidence row. 11 snapshots and completion; the fixed business summary is not represented as invented text deltas. |
| Casual conversation after the exact inventory query | `COMPLETED`, casual, `guided=false`; an actual reply delta followed by completion. |

All seven original message requests were replayed with the same body and idempotency key. Each replay returned the same turn, with unchanged turn count and model-call counters. This verifies completed-request deduplication; disconnect recovery is separately covered by the controlled transport/client tests.

The client retained httpx's default `Accept-Encoding: gzip, deflate, zstd`; SSE responses used identity encoding. Measured delivery confirms incremental output through the actual proxy: the exact-SKU query's first snapshot arrived at 79 ms and its completion at 6925 ms. The final casual reply's delta arrived at 2487 ms, before completion at 2561 ms. Other casual first-delta lead times were 61–327 ms. These are observed network timings for this run, not a latency guarantee or a claim of unbuffered individual model tokens.

The sanitized main record is `../../work/assistant-streaming-live-verification.json` relative to the working-tree root; it preserves the initial five-round result and appends `focused_inventory_followup`. `assistant-streaming-live-focused-verification.json` preserves the two-round follow-up separately. The main record's initial `passed=false` is intentionally retained because the broad inventory prompt only clarified; it is not overwritten by the successful exact-SKU follow-up. No provider endpoint, credentials or session cookie is reproduced in this document.

## Remaining delivery work

Focused backend/frontend checks and the real configured-model observations above are recorded. The browser runner initially lacked `libnspr`; the existing browser dependency directory was restored. Browser verification then found a real desktop review-toggle issue, which has been fixed and is awaiting the rebuilt app and final browser run. Keyboard/reduced-motion checks, responsive/editor-preservation browser checks, migration-state verification, final build and exact-SHA CI remain open until the main delivery records their actual results. Neither local complete suite is claimed green on the basis of overlapping focused reruns.
