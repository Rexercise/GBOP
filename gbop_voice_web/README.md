# GBOP Voice Web

This is the low-latency voice front end for GBOP.

## Owner messages

`/gbopmessage text:...` now creates a private preview. It never sends immediately.
Only the existing configured GBOP owner can use it, in the configured G.T.O.P guild.
Recipients see the heading **GBOP Message**, sent by the GBOP bot.

- `audience:all` previews all currently eligible members.
- `audience:selected` opens Discord's searchable server-user picker. Select up to
  25 people to preview only those current human server members, even without GBOP access.
- `audience:all_server_members` previews all current human server members, with or without GBOP access.
- `audience:all_except` opens the same picker to exclude selected eligible GBOP members.

Leave the optional `members` field blank to use the native user picker, rather than
Discord's text mention suggestions. The picker lists server users without a GBOP-role
filter; bots and unverified/departed members cannot pass the subsequent preview check.
Selecting users creates a private preview only, not a send. Cancel and expiry close
the picker without sending. For more than 25 people, or to paste an existing list,
`members` still accepts exact user mentions or Discord user IDs separated by
spaces or commas. It does not guess from names or accept role mentions. An invalid
or unverified selected ID blocks the preview instead of silently changing its scope.
For `all` (the default) and `all_except`, current eligibility still uses the existing
role, activation, leadership acknowledgement, revocation, and owner rules. Explicit
`selected` and `all_server_members` recipients need only be verified current human members of G.T.O.P; they
can receive the message without the GBOP role, activation, or unrevoked GBOP access.
Receiving a message does not create or update their GBOP profile, activate them,
grant a role, or change their access. Replies still use the existing GBOP access
checks. Bots, external users, and stored profiles of former members are not recipients.

The preview displays the exact outgoing message and names plus IDs for every
recipient. Review all recipient pages, then press **Confirm send**, or **Cancel**.
Previews expire after ten minutes. Messages above 1,800 characters are rejected
rather than shortened. New recipients are not added after preview; current human
guild membership is refreshed immediately before each `selected`/`all_server_members` DM, and full GBOP
eligibility is refreshed for `all`/`all_except`. Unverified recipients are skipped.
Discord DM privacy settings remain enforced; blocked DMs are not bypassed.
Server-wide audiences require a fresh complete member list; an incomplete or failed
lookup blocks the preview. All sends are sequential through Discord's rate-limit
handling, not a simultaneous burst. Large audiences may take time to review and deliver.
Repeated confirmation clicks cannot resend the same preview. Delivery errors are
reported as unconfirmed; GBOP does not start a new attempt after an error. No database migration,
new credential, or permission change is needed. Scheduled shift messages are unchanged.

## Architecture
- Browser microphone -> WebRTC -> OpenAI GPT-Live-1
- GPT-Live-1 handles the natural voice conversation and interruption.
- Private GTOP data/actions use client delegation to this local backend.
- The backend reads/writes the existing ../gbop.db and uses the existing OpenAI API key from ../.env.

## Local test
From ~/GTOP-Bot-GBOP/gbop_voice_web:

    ./start.sh

Then open:

    http://localhost:8787

Microphone access works on localhost.

## Phone / iPhone
Mobile browsers require HTTPS for microphone access. Put this app behind an HTTPS host/tunnel.
A quick development option is Cloudflare Tunnel:

    cloudflared tunnel --url http://localhost:8787

Open the HTTPS URL Cloudflare prints on the phone.

## Current authentication
Owner-preview mode uses GBOP_VOICE_PIN from ../.env and the Discord owner user ID
(GTOP_OWNER_USER_ID by default). The API key never goes to the browser.

For multi-member production use, add Discord OAuth and map each signed-in Discord user
to their own GBOP user_id. The backend code is already user-id scoped.


## Handwritten journals and coaching

Discord DMs accept up to four PNG/JPEG/WebP photos (8 MB each). Send a journal
page and say “Add this to my journal.” GBOP transcribes readable details, saves
separate entries for separate trades, preserves the original image, and leaves
unknown results blank. The saved review can be corrected conversationally:
“Journal #2 was Model 1, Tier 2.” Do not use hypothetical/study pages as trade
outcomes. Handwriting extraction is model-dependent; review unclear numbers.

Available through Discord text, Discord voice tools, and browser voice backend:

- “Show my textbook Super Soup examples.”
- “Send the picture for Journal #2.” (Delivery is to the requesting member's DM.)
- “Compare my entry models” or “Review my last seven days.”
- “Save my Day Shift plan for 2026-09-30: …”
- “Compare my plan with my journal.”

Statistics use one journal outcome per linked trade, exclude study/reflection
entries and unknown outcomes from win-rate denominators, and label mixed-entry
trades instead of crediting every model with the full result. Undated handwritten
trades are excluded from date-window reports. Dates use UTC for existing records;
handwritten dates are preserved as recorded. Weekly reviews are available on request and through the existing eligible-member
private schedule described below. The model asks only for missing or ambiguous details.

Conversational journals can retain the exact authenticated market review and
selected candle when the member identifies it as their trade. Reported entry and
exit timestamps stay separate from logging time; a candle interval is not an
exact fill time. Unknown R remains unknown, including for a reported stop-out.
Existing-journal corrections preserve their original association and record
provenance. Current-market snapshots retain their original as-of/cutoff when
carried into a journal. Normal recall returns a bounded factual summary; detailed
source evidence stays saved, and full journal prose can be privately delivered.

`review_current_market` handles on-demand current/pre-shift/in-shift/off-shift
analysis separately from completed shift recaps. It exposes source precision,
snapshot freshness and forming references; it never substitutes a completed
historical shift or infers instantaneous tick action. Explicit chart anchors can
use the existing supported timeframe mappings. Default shift recaps are short,
with independent relevant Young Lefty context and named-range details on request.

“Other relevant plays/ranges” uses `review_other_market_ranges`, retaining the
verified asset, date, shift and cutoff. It includes unbranded hourly CRTs and
independent failed ranges, and states when the last range has no post-close
evidence. Completed response delivery marks only ranges actually explained;
retrieving evidence alone does not mean every range was discussed. Interrupted
or incomplete replies do not advance that history. Switching away and returning
keeps a separate history per scope; an explicit restart resets it. Discord voice
requires completed response, transcript and drained playback together. Browser
receipts require uninterrupted output and active local playback. Neither proves
the person heard their device. Text follow-up routing is deterministic; audio-only
tool intent remains model-classified, with no added transcription service.

`journal_coach.init_coach` installs additive, idempotent tables. New tables have
RLS enabled and no anon/authenticated grants: only the trusted backend accesses
them, enforcing the guild/user identity supplied by the authenticated session.
Import retries use `(guild,user,photo,entry_index)` to update the same journal.
No execution is created by a handwritten journal import. Deletion uses an explicit
confirmed preview of the entire linked record; a changed record needs a fresh preview.
Trade-linked images follow existing deletion behavior; unrelated library images remain.

## Young Lefty across markets

“See any Young Leftys anywhere?” runs `scan_young_lefty` once across all nine
supported instruments. “Any other pair with a Young Lefty?” excludes the selected
instrument. “Whatever is applicable” continues an immediately preceding scan.
Text turns prefetch the evidence; audio-only intent still depends on the model
calling the same tool. This is an on-request scan, not a subscription.

The scan retains the reviewed New York date, shift and cutoff, or uses the
current New York window if none is selected. Explicit today/tonight/now requests
refresh the cutoff. Each instrument uses its own 7 AM/PM H1 reference and source
coverage. Missing/forming evidence is separate from verified absence; invalidated
ranges retain their earlier delivery facts. Results do not establish pre-9 entry
permission, a fill or a member result. Choose a named result before linking a
journal; the scan never assigns an arbitrary market to a trade.

## One trade, one journal

A member's displayed Trade # identifies the thesis and its journal together.
Opening a trade creates its canonical journal in the same guarded transaction;
closing it updates that journal. Reflections, corrections, execution records and
any number of images remain associated with that thesis. Source imports use the
exact photo ID and page entry index for idempotent retries. Journal photo recall
combines direct page attachments, additional saved source associations and photos
linked to the same owned thesis, then applies the remaining filters together.

A standalone journal creates an IDEA or JOURNALED identity with no executions and
no assigned risk budget. Unknown actual risk and R remain unknown. When a member
explicitly supplies a journal-only Trade # to open_trade, the first real execution
uses that same thesis. An already OPEN trade uses add_entry instead.

Existing duplicate linked journals are shown as one read-only trade view with
preserved historical entries. Conflicting historical outcomes remain unknown.
An explicit update creates/adopts one canonical record and retains full original
rows plus immutable before/after audit in existing thesis events. Existing unlinked
journals stay separately addressable as Legacy journal #; ambiguous old Journal #
aliases require clarification. No automatic merge, deletion, backfill or guessed
reassignment runs at startup. No schema, grants or RLS change is required.

Member ownership and current authorization are checked inside serialized write
transactions. Conversation-generation guards prevent canceled or stale requests
from saving. Full private recall retains the update timeline; ordinary model
responses are bounded previews and mark omitted history rather than claiming it
is absent. Exact-record recall also offers read-only full-context pages, including
all saved feelings, SELF grades, execution/management history and photo notes.
Questions use those pages without sending private messages.

`latest=trade` orders by actual reported trade dates. Missing, conflicting or
overlapping date precision requires clarification; a market-review default date
is not a member execution date. `date_basis=saved` explicitly requests the latest
saved trade; `latest=journal` uses saved/updated time. A selected identity is bound
through the same turn's context pages and send even if display numbers change.

`send_journal_history` sends one complete private bundle by default. It includes
all images associated through the selected owned trade, journal source pages,
metadata or source events, deduplicated without a five-photo page limit. Explicit
text-only requests set `include_photos=false`. Unrelated account photos are never
added. The receipt reports `text_sent_count` and `photo_sent_count` separately.

## Private journal and photo delivery receipts

Photo and journal requests save small delivery receipts in the existing
backend-only runtime metadata table; no new schema or permissions are installed.
A successful empty photo lookup sends one private notice: “No saved photos match
this request (0 photos).” It does not count that notice as a delivered photo.
Confirmed messages, partial delivery and uncertain transport outcomes remain
available through `get_delivery_status` after voice interruption or reconnect.
The status tool never sends anything.
An interrupted Discord voice delivery refreshes its exact authenticated receipt
into current conversation context once. This does not restart old replies or
audio, and an already-generating answer is not rewritten. Read-only journal
lookups do not send private progress notices.

Matching requests recover their receipt for 15 minutes after a clean terminal
outcome. Pending, partial and uncertain deliveries are never automatically
repeated, regardless of age. An explicit request to send again uses
`delivery_action=resend`; text clients verify that instruction in the current
member message. Audio-only clients rely on the model identifying that explicit
spoken intent. Repeated tool calls for the same resend turn remain deduplicated.
Receipt admission fails closed if storage is unavailable. An HTTP timeout can
still mean Discord accepted the last message; that uncertainty is preserved.
Per-transaction SQL has a 15-second statement timeout. Connection establishment
has a 10-second connection timeout; pending receipts older than two
minutes are reported as uncertain rather than promised as still progressing.
Bundle deduplication uses selected record identities/content, not unrelated
account counts; an incomplete bundle cannot be automatically repeated after a
content change. No schema changes or historical journal rewrites are involved.

## Weekly Structure Study and optional private reflection

`get_weekly_structure_study` retrieves one versioned SS report combining closed
market observations and the member's optional SS contribution. The existing
leased watcher prepares one asset per cycle, starting one hour after Friday's
nominal 5 PM New York close, and rechecks hourly for late retained candles. It
stores reports without sending a new weekly SS DM. All nine assets are listed;
BTCUSD/ETHUSD use only actual completed broker W1 intervals supplied by the
collector. They remain explicitly unavailable until those source boundaries are
received; no Monday/UTC or New York cutoff is guessed. The Monday week_start is
only a reporting key around the observed interval midpoint, never an anchor.

The noncrypto window is Sunday 5 PM–Friday 5 PM New York, a nominal envelope,
not a verified broker holiday/session calendar. A later actual metals/oil reopen
is not labeled a missing trading session. Reports disclose unobserved intervals,
coverage and final-bar status; weekly extrema remain observed rather than
certified full-broker-week extrema. Equal highs/lows retain ties and M1/M5 candle
interval precision, never exact tick timing. The collector reads MT5 position 0
then filters by proven bar-close timestamp, retaining the final already-closed
bar across weekends while excluding any forming bar.

Market OHLC/extreme facts are immutable and versioned. Launchpad/PDA explanations,
structural synthesis and conditional next-week hypotheses remain human inputs.
Execution reflection retains the canonical seven questions. Saving is optional,
requires supplied answers in an authenticated member context, and binds them to
the exact asset/week/report version; blank fields stay unknown. Corrections append
member-only revision history and update the existing SS review. No SS save creates
a trade, execution, risk allocation or arbitrary DM capture.

Deployment requires the additive `weekly_structure_reports` migration first.
There is no startup auto-DDL for these two tables. Both are RLS-enabled, with all
PUBLIC/anon/authenticated privileges revoked and no browser policies. The existing
trusted backend role receives only SELECT/INSERT. No existing security settings,
member eligibility or tables are expanded. Roll back application code if needed;
leave durable reports/contributions intact, rather than dropping private records.
The migration is idempotent and uses composite primary/foreign keys and scoped
recent-read indexes. Verify production schema, grants and advisors before release;
local SQLite regressions do not prove PostgreSQL RLS enforcement.

## Optional trade feelings

In the existing private trade conversation, GBOP may briefly ask how the member
felt after a real trade action. Answers are optional: ignoring the question does
not divert the next message, and an explicit skip suppresses further feeling
questions for that trade. The first prompt is offered at most once per trade;
a later close prompt is also at most once and is suppressed within 30 minutes of
an earlier prompt/report. It is combined with the optional SELF-grade invitation
when that feature is present and a grade has not been supplied. No scheduled DM,
new transcription/model call, schema, or security-permission change is added.

`record_trade_feeling` appends exact member words to the canonical Trade # journal
metadata. Each report retains its open/add/mid/close stage, member-reported time
(or null when unknown), distinct server logging time, and source. Corrections
append a reference to the earlier report; originals remain intact. The existing
legacy `emotion` field is preserved rather than copied into the history. Limits
are 500 characters per report, 32 reports per trade and the existing journal
metadata budget; reaching a limit rejects the new report without pruning history.

Text report grounding uses the current authenticated utterance; brief bare feeling
answers require this exact conversation's immediately preceding prompt. Other
requests, including watches, retain their ordinary route. Audio-only classification
still relies on the existing model and does not claim an added transcript check.
Trade identity, member access, revocation, cancellation and connection lifetime are
checked by the existing guarded journal transaction. The model must clarify an
uncertain trade/stage and must not infer emotion from a loss, risk or tone.

Private recall/delivery includes full history. Default model-facing previews are
bounded and label omissions. Performance reviews group exact self-reported phrases
by stage, count one outcome per trade/group, show missing outcomes/feelings and
label overlapping groups. These are descriptive associations, not causal claims,
predictions, diagnoses or advice to increase risk. Synthetic tests exercise the
production Discord/browser handlers and in-memory data, never live member records.
## Scheduled private performance reviews

Existing eligible-member daily snapshots remain at 00:15 America/New_York and
weekly reviews at Saturday 00:20, covering Monday–Friday. Their existing retry
windows and per-member delivery keys are unchanged. Personalized Day (09:00–12:00)
and Night (21:00–00:00) shift reviews accompany the existing 13:00/midnight
formation question as one DM, without another scheduled message. Fresh Discord
membership/role and GBOP activation/revocation checks run before collecting private
records and again immediately before sending. This adds no access or eligibility.

Reviews are deterministic and read-only: one canonical outcome per Trade #;
conflicting uncanonicalized history stays unknown; studies/reflections are excluded.
Known R outcomes alone enter win rate and averages. Reported exit time, trade date
(and session for shift reports), or reported entry time take precedence over
logging time. Legacy CLOSED thesis close logs can be included as explicitly labeled
logging activity; undated journal-only imports cannot. Logged executions/risk stay
separate from reported fill times. Missing replies, risk flags, outcomes, feelings,
or self-grades do not establish inactivity, adherence, psychology, or process success.

Optional exact self-reported feelings and supported SELF grades appear alongside
outcomes, with sample/missing counts and no inferred grades. Weekly comparisons
use the prior Monday–Friday sample when there is evidence in both periods, with
uneven coverage caveats. A saved personal plan is shown as a reference, never
proof of adherence or a universal rule. The next adjustment uses recorded risk
flags, explicit off-plan reports, or specific missing evidence.

Market context uses retained broker candles for attributable recorded
asset/date/shift scopes, reports closed-bar counts and full/partial/unverified
coverage, and never equates market movement with a personal fill or profit.
Compact reviews bound retrieval/display and explicitly count omitted windows.
There are no paid model calls, new tables, migrations, or security changes in this
review path. Synthetic tests: `python -m unittest tests.test_personal_reviews`;
aggregate checks: `python -m unittest discover -s tests` and the existing CI compile
command in `.github/workflows/gbop-tests.yml`.


### Range variant explanations

Ordinary shift reviews name each supported completed variant and briefly explain
its actual H1 sequence. During a developing range, shift and current-market facts
carry only evidence-supported candidate paths, their observed reason, and the
remaining close/delivery conditions. Multiple possibilities remain conditional;
no purge, missing evidence, or unresolved source-bar order cannot establish a
unique completion variant. A forming reference is not a valid range.

The established classifier and selected-range transition rules are unchanged.
V4/V5 inside-bar and V6 re-soup structures can be known before full delivery.
Delivery time, structural confirmation, later invalidation, and member execution
remain separate facts. V1/V2/V3 still require the classifier's complete H1 evidence;
partial targets never become completed variants. Compact voice keeps the same
scope, candidate evidence and existing response budgets; exact detail stays
available through the range's detail request. No model, broker, or member writes
are added by this presentation layer.
