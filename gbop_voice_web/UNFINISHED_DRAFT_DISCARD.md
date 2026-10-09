# Recoverable unfinished-draft discard

Prepared against public main `9acd5f6e9cff0f16d42eef1954c1ebe3db94ac80` (PR 75).
No production mutation, schema deployment, publication or release is part of this
candidate. All executable fixtures are synthetic.

## Member flow

In the existing private text or voice conversation, ask to discard/delete an
unfinished draft. `prepare_journal_discard` identifies one exact saved unfinished
draft and returns its title, short identity and confirmation question. Multiple
possible drafts require selection. The assistant delivers that exact question;
the immediate next affirmative member reply permits `discard_journal_story`.
A tool's generated confirmation flag cannot replace delivered-preview evidence.
Typed member text overrides model arguments; audio supplies the actual spoken
reply. Cancellation, partial playback, an unrelated reply, a different response
receipt, a reconnect, changed revision or five-minute expiry fails closed.

The result says **discarded and archived**, not permanently deleted. Only this
member's unlinked unfinished draft is removed from active recall/resumption. Its
raw narration and corrections are retained. `get_journal_story(view="discarded")`
shows at most ten bounded archived titles and storage revisions per page; an explicit restore request uses
`restore_journal_story` for the selected identity/revision. Restoration does not
finalize, add performance/execution rows, clear a recording opt-out, or reuse an
old authorization to finalize.

## Storage and concurrency

The existing `unfinished` / `finalized` database constraint remains unchanged.
A storage-owned leading `_discarded` payload marker represents the archived
state; active reads exclude it before transferring its narrative payload, and a
Python guard also recognizes legacy/noncompact markers. The bounded archive list
uses the new leading marker; malformed/noncompact legacy markers remain hidden
from active reads and can be inspected by exact identity for support. `include_discarded=True`
is limited to explicit recovery/listing/backend state checks. A bounded history
retains the latest 32 lifecycle transitions; narrative and correction history
are never truncated to make room. Storage and substantive recency are unchanged.

Discard and restore use the existing member transaction lock, authenticated
conversation guard, ownership checks and optimistic storage revision. Exact
same-transition retries are read-only. A later edit, restore, re-discard or
finalization invalidates an older operation. Finalization's transactional reread
refuses an archived draft. Finalized drafts, linked trade/journal drafts and
unfinished corrections to saved narratives are refused rather than hidden or
deleted. Canonical rows and foreign-key deletion cascades are never modified.

Cached persisted and paused session-only continuations recheck durable liveness
before use. Prompt status reads happen outside the conversation lock. A verified
archive/missing row suppresses stale state; a temporary read failure suppresses
its use while retaining any unsaved local continuation. Discarding a different
draft does not block the active one. Fresh narration cannot silently recreate an
archived draft; the member must explicitly restore or start a new draft.

Voice interruptions retain sanitized lifecycle receipts. Exact read-only recovery
can verify the single discard/restore transition or unchanged prior revision
after a writer settles. It never retries a write. Existing finalization recovery,
market cache and post-shift paths are unchanged.

## Bounds and release checks

- This flow applies to durably saved, unlinked unfinished drafts. Session-only
  previews and drafts linked to saved trades/journals are intentionally refused.
- Removal is recoverable archiving. Permanent erasure remains outside this flow.
- No migration, dependency-file or credential change is required.
- Rollback must retain archive-aware read/write filtering. An older application
  without that filtering could display archived payloads as unfinished again.
- Live PostgreSQL and actual browser/Discord microphone/playback acceptance have
  not been performed. They remain release checks after publication approval.
- Keep the existing 51,000-character base voice instruction guard unchanged.

Run the repository's CI dependencies, `python -m unittest discover -s tests`,
`node tests/test_browser_market_context.js`, compilation and `git diff --check`.


## Ordinary voice confirmation transition

The Discord speech-start path cancels older tool/audio work before beginning the
new member turn. It carries a discard preview across that fence only when the
preview has already completed delivery, is fresh, belongs to the same owner and
session, and matches the exact pre-cancel generation. Its generation is rebased
once; the subsequent turn still must be the immediate confirmation reply.
Other cancellation, failure, reconnect and teardown paths continue to clear it.

The delivery comparison tolerates punctuation-only transcript changes while
requiring all preview words, including the recovery disclosure. Common clear
affirmatives such as “Yes, go ahead” and “Yes, I confirm” are accepted; negation,
questions, unrelated wording and broader deletion requests remain rejected.
A redundant prepare call for the same unchanged draft preserves its original
receipt and expiry. Once the preview was delivered it instructs the assistant
to use the current explicit reply, rather than ask the same question again.
New draft facts or any storage revision change still need a fresh preview.
