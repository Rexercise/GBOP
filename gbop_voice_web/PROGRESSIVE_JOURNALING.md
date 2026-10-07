# Progressive private journaling

Narration is captured in the existing conversation as a member-owned unfinished
journal before clarification. No separate member command or form is required.
The first typed journaling passage is checkpointed before model work; voice uses
`stage_journal_story` first, with a faithful current-passage transcription. Audio
is not background-recorded, and no audio buffers or credentials are stored.

## Storage and lifecycle

- `journal_story_drafts` stores the raw passages, field provenance, corrections,
  creation/update times, stable identity, optimistic revision, and delivered
  questions. It is separate from journal/execution/performance tables.
- `get_journal_story` resumes the same member-owned unfinished record after a
  pause/reconnect. Multiple unfinished records require selection by title.
  Raw narration remains accessible in bounded, lossless `raw_offset` pages.
- Staging never invents fills, risk, outcomes, or performance samples. Context
  aliases and supported facts get a brief readback and can be corrected.
- `save_journal_story` finalizes only on an explicit save/finish/finalize request.
  Voice supplies the actual spoken `confirmation_text`. Finalization and its
  durable status update commit atomically. Existing matching trades require
  reconciliation, and retrying an already committed finalization is read-only.
- Unknowns remain unknown. Approximate/undated entry and exit wording is kept
  separately from ISO timestamps. Conflicting merged times remain unfinished
  until corrected. No historical dates are backfilled from logging time.
- Explicit preview/do-not-record requests suspend persistence. The pause survives
  reconnect for an existing draft. Loading an older record never clears a new
  session's stricter opt-out; explicit journaling/resume authorization is needed.

## Execution identity and receipts

Each transport tool call gets a server-owned execution operation capability.
Distinct calls can therefore save identical entry model/tier/risk facts without
collapsing. The operation receipt and execution row commit in one transaction.
Same-operation retries read the receipt and reject changed arguments. Responses
report a database-verified execution count and total recorded risk. Browser HTTP
request replays do not re-plan the same turn with fresh model call IDs.

Narrative entries remain separate from actual execution rows. Receipt internals
are omitted from human journal history. Interrupted voice saves expose sanitized
commit status and verified counts, never raw narrative or secrets.

## Privacy, migration and rollback

The prepared additive migration is
`supabase/migrations/20261007191500_progressive_journal_drafts.sql`.
The application initialization uses the same table/index/RLS definition. Browser
roles have no policies or grants. Every read/write is authenticated and scoped by
guild and member; member deletion and linked trade/journal deletion cascade to
saved narration. Linked drafts are included in deletion fingerprints/disclosures.

No migration was applied while preparing this candidate. No production journals,
member profiles, risk allocations, canon, or credentials were changed. Application
rollback must retain the table so unfinished narration is not lost.

## Validation

Use the pinned GitHub Actions dependencies and run:

    python -m py_compile bot.py gbop_voice_web/*.py
    python -m unittest discover -s tests
    node tests/test_browser_market_context.js

Synthetic tests cover reconnects, raw-only interrupted passages and pagination,
privacy pauses, account isolation/deletion, exact/approximate/cross-midnight timing,
explicit finalization, optimistic corrections, transaction rollback, transport
replays, duplicate identical entries, count receipts and interruption fences.
Live voice/device and PostgreSQL deployment verification remain release checks.
