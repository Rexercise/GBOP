# Owner + authorized test member live acceptance

**Status: NOT RUN.** Offline tests and a healthy web endpoint do not complete
these checks. Use the deployed commit under review and the two participants'
explicitly agreed test session. This checklist does not contact or enroll anyone.
Do not widen member access, create paid resources or intentionally exhaust API
credits to run it. Normal voice use consumes the existing account's credits.

## Record before starting

- Release commit and deployment identifier:
- Date/time with timezone; Discord clients/devices:
- Owner and the authorized test member confirmed separately signed in and already authorized:
- Primary/helper identities ready; collector last received time:
- Test result: NOT RUN / PASS / FAIL / BLOCKED for each section, with timestamps:

Keep screenshots, Discord message links and account/member identifiers in the
authorized private test record, not in this repository. Don't record audio or
upload personal journals without participants' agreement. Use clearly labeled,
non-sensitive study/test content for any new write; never count it as a real fill
or trade result. Do not modify live permissions merely to simulate failures.

## 1. Private entry and two-person isolation

- [ ] Owner uses `/meet` while already in voice. The private reply distinguishes
  room creation/move, Discord connection and AI readiness; one meeting starts.
- [ ] The authorized test member uses `/meet` from text, opens the privately returned room and joins
  voice. GBOP joins without a second slash command. Each participant sees only
  their own room, subject to Discord's disclosed administrator override.
- [ ] Repeat `/meet` during startup and after ready. It reuses the participant's
  tracked meeting instead of duplicating sessions or moving the other person's bot.
- [ ] If the primary and helper cannot both serve separate rooms, record the
  actual capacity blocker and accurate browser fallback. Do not mark two-room
  acceptance passed merely because a browser session works.

## 2. Real spoken exchange and market truth

- [ ] Each person asks a short question and hears a complete reply. Interrupt one
  reply, ask a follow-up, then pause/resume one session while the other continues.
  Record any missed speech, tail audio, duplicate reply or unexplained silence.
- [ ] Ask for Friday, October 2, 2026 NAS100 day-shift review. It distinguishes
  failed 9ate8 from the later selected 9 AM range's midpoint and opposing-liquidity
  delivery; see the [retained replay facts](../tests/fixtures/market_replays/README.md).
- [ ] Ask about that day's gold/silver SMT. Gold's boneless delivery uses gold's
  own target/time; the initiating wick-only silver event is not falsely called a
  proven body-purge/inherited Model 1. Later local failures remain separate.
- [ ] Ask about NAS 10:00 and 10:10 M5 Model 1s: clean versus unclean structure,
  local CRT invalidity, later local function and parent delivery remain distinct.
  Ask about silver 10:50 M5: clean formation is not automatically a successful CRT.
- [ ] Ask about NAS Blessed Thief 10 AM and subsequent 11 AM opens. It treats
  opening prices and delayed revisit evidence separately from actual execution.
  Candle names use opening time; invalidation says the closure of that candle.
- [ ] On a currently tradable instrument, compare a timestamped price and source
  status with the broker terminal. A closed/stale instrument is labeled honestly;
  a fresh upload alone must not be reported as a fresh tick.

Check semantic facts, not identical wording. For any disagreement, record asset,
broker symbol, date, anchor, timeframe, through-time and the relevant source
candles. Do not patch the expected answer just to match a voice reply.

## 3. Journals, photos and cross-interface continuity

- [ ] Each person asks for their own latest journal and an explicitly chosen photo.
  Confirm only that person's records are returned and the image reaches that
  person's DM. Report blocked DMs accurately; don't send it to a shared channel.
- [ ] If testing writes, explicitly request one labeled study/test journal with a
  non-sensitive test image sent by DM. Confirm the saved entry and image once,
  including unknown outcomes remaining unknown. A journal request creates no
  broker order and no invented execution.
- [ ] Disconnect one person's Discord voice and switch that same account to
  browser voice. Retrieve the same identified test journal/photo. Other person's
  session and records remain unaffected. No shared verbatim transcript is promised.
- [ ] Any test-record deletion is a separate participant-authorized step. Keep
  personal content out of issue bodies, repository fixtures and public logs.

## 4. Hang-up, cleanup and recovery

- [ ] Owner hangs up using the released leave control or exits their temporary
  room. Their AI/audio ends, while the authorized test member continues speaking and receiving replies.
- [ ] A tracked temporary room is cleaned up only when eligible under the released
  cleanup policy. An occupied room, a room containing chat/photos, or an older or
  untracked room is preserved. The private reply/status explains preserved rooms.
  Do not populate an actual private room just to test destructive behavior.
- [ ] Leave during startup; reconnect and repeat the command. No late join,
  orphan listening session or cross-room move should occur. If a reconnect or
  rate-limit recovery happens naturally, verify a clear notice, no tool/write
  replay, no stale spoken response and a working next turn. Otherwise mark that
  live recovery scenario NOT EXERCISED and retain its automated coverage separately.

## 5. One actual watch event and cancellation

- [ ] During a **verified tradable shift**, a participant explicitly asks for one
  bounded NAS100 watch and private DM notification (for example a closed-candle
  body-soup event). Confirm the returned watch ID, asset, shift/date, range scope
  and expiry. The runtime must report ready; a closed session stays scheduled.
- [ ] Wait for a naturally qualifying closed candle in that watch's active window.
  Compare its event time and candle evidence to the broker. Confirm one DM to the
  subscriber, none to the other participant, and no unsolicited voice playback.
  Note the observed delay; polling is about 30 seconds plus processing time.
- [ ] Ask to list watches and inspect delivery state. A send accepted by Discord
  is not a substitute for the participant verifying the DM arrived.
- [ ] Ask to stop the test watch. Confirm its cancelled state and no subsequent
  alerts for that watch; cancellation does not affect the other member's watches.

If no qualifying event occurs before expiry, mark **INCONCLUSIVE: no event**, not
PASS or FAIL. Do not inject artificial candles into production, replay old events
as live alerts, keep extending the watch without permission, or force rate limits.
Use an approved later session if end-to-end event delivery remains unproven.

## Completion record

- Passed checks and evidence timestamps:
- Failed/blocked/inconclusive checks and smallest next action:
- Test watches cancelled or naturally expired:
- Test data retained/deleted per participant request:
- Owner sign-off:
- Authorized test member sign-off:

Two-person acceptance is complete only after the actual spoken, member-isolation,
journal/photo, hang-up and qualifying-watch-event checks have supporting evidence.
Unexercised recovery conditions remain disclosed; simulated unit tests cannot be
reported as a completed human session.
