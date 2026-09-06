# AI assistant workspace experience

This increment follows the user's approval of a beUI implementation inspired by the public BoardUI AI Chat layout. No BoardUI Pro source or dependencies are installed. It continues `ui/beui` / PR #8 from accepted beUI revision `80c5f1b`.

## Layout and behavior

At viewport widths of 1280px and above, the assistant has a 208px conversation rail, a flexible chat pane, and a 336px evidence/review pane. At 1600px these side panes become 224px and 380px. Smaller screens display one pane at a time with explicit history/context/back controls. The ERP navigation remains available. The assistant uses the remaining viewport beneath the global header, keeping the single composer visible while messages and review content scroll independently.

The history rail searches only the server's current page, labels that scope, and retains pagination and private seven-day history information. Unsent prompts stay in memory per conversation and authenticated identity. Starter prompts only fill the input; they do not submit, call a model, or create an order. There are no decorative attachment, microphone, model-switch, token-meter or stop controls without a supported business capability.

Messages link to their own business evidence and proposal in the review pane. The context selector can revisit prior results. All current-conversation review editors remain mounted while switching panes or selected messages; responsive CSS does not create duplicate editors. A real server proposal revision/status still changes its original key so refreshed previews and durable receipts remain authoritative. Evidence retains exact server values, query time, scope, truncation notices and validated internal source links. Narrow draft forms keep all fields and put detailed server line snapshots in expandable disclosures.

The composer keeps the official beUI IME/newline/submission behavior and shows the actual configured provider/model. Daily briefs and destructive conversation deletion remain explicit actions in disclosures. Assistant answers may be copied using the browser clipboard after a user click, with an honest failure message if access is unavailable.

Temporary network/server read failures preserve the mounted editor but disable submission until reads recover. Deleted/expired history (404/410) and authority rejection remove the prior conversation content. Passive conversation-list refreshes cannot change the initially selected conversation during a pending turn. Uncertain-submission feedback is shared above all panes, including mobile review. Programmatic quick navigation and logout use the same multi-owner pending guard as ordinary links/history; an expired-login redirect remains possible for authentication recovery.

## Source and application boundaries

The official `chat-app` registry item was inspected. Only its `components/agents/chat-app.tsx` composition entry is added: its actual imports resolve to existing AnimatedSidebar and utils source. The registry's optional demo bundle (code/diffs, generated media, Shiki and unrelated agent tools) is not imported. The original source hash and partial installation scope are recorded in `docs/vendor/beui/source-manifest.json`. Existing Message, MessageScroller, PromptInput, ApprovalCard, Button and Input implementations are reused.

No API, database migration, model reasoning or business capability changes. Preserve original same-origin routes, identity/permission isolation, proposal preview/revision/hash validation, exact amount strings, durable creation receipts, submission uncertainty locks and identical-key/body retries. UI tests use isolated test tenants and controlled local transport; they do not spend the user's configured model credits.

## Verification

Each implementation increment runs relevant component checks. Final full frontend, production browser, build and current-revision CI results are recorded in PR #8 and the external `Forge-ERP-AI-experience-verification.json` delivery record. Earlier beUI acceptance alone is not evidence for this change. Browser coverage must include three panes, mobile switches, a visible unique composer, prompt isolation, prompt shortcuts without requests, selected-message evidence, preserved dirty editors and existing business approval/retry safeguards.
