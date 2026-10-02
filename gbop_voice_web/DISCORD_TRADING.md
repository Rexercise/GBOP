# Using GBOP during a trading session

Members need their GTOP member role and `/activate agree:true`. Revoked members
cannot use the member tools. Join a regular Discord voice channel and run `/gbop`.
`/voice` remains supported. GBOP needs View Channel, Connect and Speak there.
She waits for the AI session to be ready before reporting success. A connection
delay is reported separately from a working Discord connection.

Once connected, speak naturally. No wake word is required; authorized members'
speech in the channel is treated as addressed to GBOP. Speak over a reply to
interrupt it. Use a dedicated trading-assistant channel if other conversations
would otherwise be picked up. Voice replies are audible to everyone present.

Examples:
- “What's the current NAS100 price?”
- “Review today's gold 9ate8. When was the range purged?”
- “Log a NAS100 short, Blessed Thief, half an R.”
- “Add an entry to my open trade.”
- “I closed at two R. Journal that I followed my plan.”
- “What's my risk profile?” / “Show my daily recap.”

GBOP uses the same registered market, trade, journal, risk, coaching and photo
retrieval tools in Discord voice. Missing required details prompt clarification.
Logging an execution does not place a broker order or read a broker position.
Upload pictures by DM or an @mention with attachments; voice cannot receive images.
For private journal conversations use DMs. Requested photo delivery goes to the
requesting member's DMs, subject to their Discord privacy settings.

## Controls

| Command | Effect |
| --- | --- |
| `/gbop` | Join your channel, or reuse the current connection |
| `/gbop action:room` | Create/reuse your private Discord room, then join it and run `/gbop` |
| `/gbop action:private` | Open your individual browser voice session; other members can open theirs simultaneously |
| `/gbop action:pause` | Stop sending your audio and close your AI session |
| `/gbop action:resume` | Resume your listening in the connected channel |
| `/gbop action:status` | Check your connection and pause state |
| `/gbop action:help` | Show examples and controls privately |
| `/gbop action:leave` | End the channel session; requires being there or owner access |
| `/voicehealth` | Existing detailed voice diagnostics |

Pausing one member does not pause others. Leaving closes that member's AI
session. GBOP disconnects when no human members remain; private rooms persist.
One bot identity serves one voice channel per server. An optional helper identity
provides a second independent room without moving the primary bot. When both slots
are busy, the member gets the browser option. An @mention saying “join me” points
to `/gbop`.

Both routes use the same Supabase database, guild ID and authenticated Discord
member ID. Trades, risk profiles, journals and photos are shared saved records.
Switching interfaces does not create another profile. It does not merge the live
audio conversations or guarantee a shared verbatim transcript. Ask for current
trade state after switching, and use only one voice interface per member at a time.
Different members may use either interface simultaneously.

Private room creation denies View Channel and Connect to everyone, allows the
requesting member and configured bots, and does not grant other member roles.
Server owners/administrators retain Discord's normal override access. A visiting
administrator can speak with the member, but GBOP accepts AI input only from the
room's member. This prevents their speech from being logged as the member's trade.
Room ownership comes from its exclusive member permission overwrite, not its name.
Rooms are never deleted automatically; a restart does not lose their owner ACL.

## Two simultaneous Discord rooms: one-time owner setup

1. Give the primary GBOP role **Manage Channels**, **View Channels**, **Connect**
   and **Speak**. If an existing category overrides these, create rooms at the
   server root (the default here) or fix the category permissions.
2. In the Discord Developer Portal, create a second application/bot such as
   **GBOP Voice 2**. Enable **Server Members Intent**, matching the client's
   configured intents. Invite it to G.T.O.P with the `bot` scope and View Channels,
   Connect and Speak. It does not need Administrator or its own slash commands.
3. Store its distinct bot token as **GBOP_VOICE_HELPER_TOKEN** in the existing
   Render service's Environment settings. Do not put it in chat or GitHub. The
   normal Render restart loads it; no new worker, VM or plan is required by code.
4. Run `/gbop action:status` and confirm two bot identities are ready. Existing
   private rooms can be refreshed with `/gbop action:room` to allow the new helper.
5. Both members create their rooms, join them, and run `/gbop`. Each room gets an
   available bot. The helper runs only voice transport; the main bot continues
   commands, DMs and scheduled alerts so those are not duplicated.

Until a distinct helper is configured and invited, there is one Discord voice
slot. Browser sessions remain independent of that slot. Invalid helper login must
not take down the primary bot. Available resources and API credits still determine
practical capacity; two-member voice performance must be measured live.

This uses the existing Render service and OpenAI voice connection; no new hosted
resource or paid plan is added. Normal OpenAI voice usage still consumes credits.

## Live acceptance check

For the initial two-person private trial, each member uses `/gbop action:private`
and signs in with their own Discord account. This opens the existing browser
voice service, not a Discord DM call. Both should connect at the same time, ask
for a market price, then ask for their own open-trade state. Verify the correct
member's records and that interruptions in one browser do not affect the other.
Pause server voice first if also connected there. Disconnect browser voice when
finished. This adds no concurrency cap and does not revoke other existing members;
the two-person trial measures capacity before expanding use.

After deployment, two authorized members should join the same channel and run
`/gbop`. Verify a timestamped price against MT5, ask a CRT follow-up, interrupt a
spoken reply, pause/resume one member while the other continues, and leave the
channel. Confirm access denial for inactive/revoked members. Test trade writes
only with an explicitly identified test trade. Automated tests cover routing and
session lifecycle, not Discord microphone transport or a real spoken exchange.

With the helper enabled, repeat in separate private rooms. Interrupt and leave
room A while room B continues. Confirm the owner/admin can join room B but is not
treated as its member by the AI. Then switch one member from Discord to browser
and retrieve the same identified test trade. No test should silently create real
executions in a member's journal.
