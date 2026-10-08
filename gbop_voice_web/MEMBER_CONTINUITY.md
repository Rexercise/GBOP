# Private member continuity

The shared backend row is keyed by `(guild_id, user_id)`. Private Discord text,
voice slot 1, voice slot 2 and authenticated browser voice use the same record.
Transport sessions, speech IDs, execution operation IDs and audio are not migrated.

## Stored and projected context

- One model-written summary, at most 1,600 characters, explicitly untrusted.
- The latest original member excerpt, at most 600 characters; replacement only.
- The latest transport-confirmed delivered-answer excerpt, at most 500 characters.
- The stable owned thesis ID of the last referenced trade.
- A durable recording-pause flag independent of whether any journal exists.
- Revision, current turn lease and at most sixteen operation hashes for safe retries.

There is no transcript archive, audio storage, automatic trade replay or automatic
execution recovery write. Common credential/token/payment-number forms are
redacted before text storage. This conservative filter cannot recognize arbitrary
secret text, so tool instructions also exclude credentials from summaries.

Unfinished journal facts, correction counts and already-delivered question keys
come from owned `journal_story_drafts` rows. Saved-state receipts come from owned
canonical journals or existing execution rows, checked anew at retrieval time.
Model summaries and assistant excerpts never establish that a trade was saved.
Deleted rows are omitted and never recreated by continuity.
Successful record deletion also clears this member's bounded private cache in
the same transaction and invalidates the old turn lease, preventing late summary
or delivery callbacks from repopulating deleted details. Consent remains intact.

For existing members without shared context, the read-only deployment bridge
checks at most twenty owned `ai_messages` rows and returns one bounded prior user
excerpt. Historical assistant text is not imported because delivery is unverified.
An explicit legacy opt-out or paused journal draft suppresses the bridge. No old
messages are changed or backfilled. A new shared pause survives reconnects even
when no unfinished journal exists; only explicit recording opt-in resumes it.

## Integration contract

1. Call `init_continuity(db)` only from the authorized schema/startup path.
2. Successful schema initialization registers the exact backend callable for the
   process lifetime. All fresh contexts using it require continuity storage;
   `_continuity_required=True` may also explicitly require it. Set
   `continuity_private=False` for public channels and shared voice rooms.
3. `hydrate(context)` reads fresh member authorization and bounded context before
   private startup instructions. It does not acquire a write lease.
4. `start_turn(context, actual_text_or_none, generation=...)` claims the new member
   turn and applies explicit privacy intent. Browser turns use the authenticated
   client-turn identity, allowing subsequent text/backend processing to reuse that
   lease without reclaiming it after another transport has spoken.
5. Inject a server-created `JournalBinding` and actual transport operation ID into
   `continuity_tool`. Model JSON must not supply these capabilities.
6. Append `continuity_prompt(context)` to private instructions. It is at most
   1,100 characters, including complete JSON. `get_member_continuity` returns the
   larger, still bounded projection when details are needed.
7. Call `validate_lease` within the existing serialized member transaction for
   journal/trade writes. Check `storage_paused` before any private-content write.
   A stale session may read context but needs a new member utterance to write.
8. Call `record_delivered` only after actual delivery proof. Call `record_reference`
   only with a successful server tool's verified record identity or Trade #.
   Both hooks fail safely without repeating the original communication or write.

The existing database-member-lock then conversation-lock order is preserved
through commit or rollback. Cross-process advisory locks, optimistic revisions,
per-turn leases, generation checks and operation hashes protect different failure
modes; none is replaced by model instructions.

## Deployment and verification

`SCHEMA_SQL` creates one table with a composite primary key and owned-member
cascade deletion. It enables RLS and revokes all access from `PUBLIC`, `anon` and
`authenticated`. No public policy or security-definer function is created. The
existing privileged backend database connection remains the sole access path.
The schema hook is explicit; read and recall paths do not create tables.

No production migration, deployment or database write was performed while
developing this patch. Before an authorized rollout, exercise the schema and
application in an isolated PostgreSQL environment, inspect RLS/grants using
Supabase advisors, and repeat the two-slot/reconnect/cancellation smoke tests.
SQLite synthetic tests exercise ownership, consent, CAS, stale turn fencing,
idempotency, canonical receipts, deletion, rollback and bounded context; they do
not substitute for live PostgreSQL lock or API-role permission tests.

Run the focused checks with:

    python -m unittest discover -s tests -p 'test_member_continuity.py' -v


## Transport safeguards and acceptance

Discord's primary and helper voice identities share the member/guild key, not
Discord bot IDs, display names, sockets or audio buffers. Private startup and
new-turn instructions read the compact shared snapshot automatically. Useful
spoken context is checkpointed through the existing model's continuity tool;
unfinished journal narration still uses its existing staging tool. This does not
enable another speech/transcription model. Old voice details that were never
journaled or otherwise persisted cannot be reconstructed retroactively.

Public mentions/shared voice never receive prior private conversation or profile
preloads. They obtain only a metadata lease for an explicitly requested owned
write, still subject to shared recording consent. A second listener or changed
room permissions stops the old private session before retained context can play.
Checks use the primary gateway plus the explicit join event so the helper's cache
cannot authorize disclosure while it lags.

Audio waits for fresh member-context admission in a bounded ordered worker queue;
the event receiver remains free to interrupt it. New speech cancels obsolete
preparation/queued audio. Auth or storage failure stops output safely. Delivery
admission marks local clarification/question state synchronously; only durable
receipt merging runs off the event loop. This preserves immediate replies while
avoiding a database lock blocking other members' audio.

Authorized record deletion also clears the derived shared summary/excerpts and
last reference, and fences late callbacks. It does not delete another member's
context. Rollback must retain this table and its consent flags. A release predating
shared consent cannot enforce those flags, so do not resume that old application
without a separately verified privacy-preserving rollback plan.

Release acceptance still needs real Discord text -> primary voice -> helper voice
-> reconnect smoke tests, using a synthetic consenting account. Include a corrected
unfinished narrative, an interrupted confirmed save, recording opt-out, and a
private-room audience change. Do not claim those live checks from unit results.
