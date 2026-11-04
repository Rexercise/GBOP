# Temporary owner-only maintenance

## Controlled rollout

`GBOP_ACCESS_MODE=owner` keeps the owner available while other members are paused.
`GBOP_ACCESS_MODE=pilot` also allows the numeric Discord IDs explicitly listed in
`GBOP_PILOT_USER_IDS`, separated by commas. The pilot list is an additional
restriction: Discord role, activation, leadership acknowledgement, revocation and
per-record ownership checks still apply. No member record or role is changed.
An empty or malformed list admits no pilot members. An unknown mode denies all.
The default `existing` preserves the previous entitlement rules; it is the broad
rollback mode, not the safe emergency stop. For an emergency pause use `owner`.

The older `GBOP_OWNER_ONLY` flag remains an emergency override. Any enabled or
unrecognized value forces owner-only behavior even if pilot mode is configured.
Production member IDs belong in runtime configuration, never in repository code.

Scheduled shift/review/watch authorization shares the database gate. Scheduled
news messages now also refresh entitlement before reading personal reminders and
again immediately before sending. This does not create a new notification or
change any member's delivery preferences. It is not an exactly-once delivery fix.
The direct Discord member and voice gates also require the leadership privacy
acknowledgement, matching the browser and shared tool gate. The existing voice
entitlement cache lasts 15 seconds; the patch does not promise instantaneous
revocation of ongoing audio. Backend tools recheck persistent revocation.

Set the reviewed source revision and approved runtime mode together during a
coordinated release. Verify the actual deployed revision, startup, bot identities,
fresh market captures and all protected transports. Keep the Discord suspension
in place until the participant's existing membership, role and private-channel
overwrites have been checked. Never restore broad roles just to begin a pilot.

Local tests cover the gate and synthetic transport handlers. They do not establish
live member isolation, consented saves/recall, Discord visibility, external audio
termination, or deployment configuration. Complete the human live-acceptance
checklist before calling the pilot ready; participant test records must be labeled
and must not be counted as actual executions.

`GBOP_OWNER_ONLY=1` blocks nonowners from commands, new browser logins,
protected browser requests, DMs, voice authorization and shared tool execution.
The identity is `GTOP_OWNER_USER_ID`, never a display name or request argument.
The default is `0`; setting `0` clears this override and leaves GBOP_ACCESS_MODE
in control. Only mode=existing restores normal entitlement. No mode changes roles,
activation, revocation, paid entitlement or journals.
Invalid enabled values deny nonowners; an invalid owner ID denies everyone.

Deploy this code and set the environment variable only after approval for the
specific release/configuration. A Render configuration change restarts the bot
and can interrupt voice. This code does not change production configuration.

Discord commands return a generic ephemeral maintenance notice. Nonowner DMs
are ignored. Protected browser calls invalidate the nonowner session and return
a generic 403 before cached role refresh. Cached Discord voice allow decisions
cannot bypass the gate. In-flight journal tool dispatches also recheck the gate.
Existing member-facing modal saves retain their normal member gate.
The shared trade/entry save functions recheck this gate before storage, including
custom forms opened before consent, revocation or rollout eligibility changed.

Scope limit: browser audio is a direct OpenAI WebRTC connection. Blocking the
backend prevents new connections and journal operations, but does not prove
that an already-issued external audio connection has terminated. Confirm no
nonowner browser voice connections remain, or separately close them through a
supported authorized lifecycle, before claiming complete live owner-only audio.
The local tests do not exercise live Discord, OAuth or OpenAI sessions.

Emergency rollback preserves this access gate: change to owner mode and restart,
then verify the actual running revision and denied nonowner sessions. Rolling
source back to a revision without this patch removes these environment controls;
the old code ignores them. Such a rollback requires a separately verified means
of stopping nonowner browser/DM/voice access first. Discord channel visibility
alone does not stop those transports. Do not treat a successful old deployment
as proof that the pilot remains paused.
