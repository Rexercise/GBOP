# Using GBOP during a trading session

Members need their GTOP member role and `/activate agree:true`. Revoked members
cannot use the member tools. Use **`/meet`** for a private meeting with GBOP. It has
no options: it creates or reuses your private room and, if you're already in voice,
moves you there and starts GBOP. If you're only in text chat, open the room from the
private reply and join voice; GBOP joins automatically without a second command.

For a shared conversation, join a regular Discord voice channel and run `/gbop`.
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

Slow Discord voice lookups send at most one brief private text status after four
seconds. Fast lookups stay quiet; blocked DMs do not stop the lookup. Status does
not generate additional model speech or speak over the member. Speaking again,
pausing or leaving cancels pending feedback and replies. An action already sent
to storage or Discord cannot be rolled back by interruption; GBOP retains its
delivery outcome instead of automatically repeating it.

Temporary AI rate limits allow at most two reply retries for the same request,
with bounded backoff and jitter. A private notice describes the pending retry;
it does not promise that capacity will become available. A new spoken request
cancels the old retry. Cooldowns longer than the automatic retry window are not
shortened. Journal/photo reads and deduplicated private deliveries remain
available during recovery; new record changes remain disabled. No model, quota,
subscription or paid resource changes are made by recovery.

## Controls

| Command | Effect |
| --- | --- |
| `/meet` | Create/reuse your private room, move you if already in voice, and start GBOP |
| `/gbop` | Join your channel, or reuse the current connection |
| `/gbop action:room` | Create/reuse your private Discord room; GBOP joins automatically when you join voice |
| `/gbop action:private` | Open your individual browser voice session; other members can open theirs simultaneously |
| `/gbop action:pause` | Stop sending your audio and close your AI session |
| `/gbop action:resume` | Resume your listening in the connected channel |
| `/gbop action:status` | Check your connection and pause state |
| `/gbop action:help` | Show examples and controls privately |
| `/gbop action:leave` | End the channel session; requires being there or owner access |
| `/voicehealth` | Existing detailed voice diagnostics |

Discord's [Modify Guild Member API](https://docs.discord.com/developers/resources/guild#modify-guild-member)
only moves a member who is already connected to voice. GBOP needs **Move Members**
for the automatic move; if Discord rejects it, the private reply still provides
the room link so the member can join directly. Opening a link does not enable the
microphone automatically; the exact Join Voice control depends on the Discord
client. GBOP does not reserve a bot slot or open an AI session for a text-only
member until they join. Repeating `/meet` reuses the room and current connection.
Simultaneous requests from different members use the existing independent bot
slots; a busy slot is never taken from another room. The room setup, move result,
readiness and any error are shown only to the requesting member.

Private-room members can also open their room and join voice directly. No second slash
command is required. Run `/meet` again after a temporary room has cleared. Entering
the room starts listening and streams the owner's speech to OpenAI, as disclosed
in the room response. Auto-join is restricted to the authorized room owner and
rooms whose private permissions remain intact. Admin visits, bot joins and
mute/deafen changes do not start or resume a session. A paused session stays paused
until the member explicitly resumes or leaves and re-enters their private room.

Pausing one member does not pause others. Leaving closes that member's AI
session immediately. In a private room GBOP disconnects when its owner leaves,
even if an administrator is visiting; a shared call disconnects only after its
last human leaves. Receive/playback, model tasks and session state stop before
network cleanup. Other members' rooms and bots are not interrupted.

New GBOP-created rooms are temporary and voice-only (text posting disabled).
After hang-up, an empty room is removed once Discord confirms disconnect and
GBOP verifies empty text history. `/meet` creates the next room. Saved profiles,
journals and photos are unchanged. Rooms with any people, messages, changed
permissions or uncertain history are retained. Existing rooms, including rooms
from before a bot restart, are not inferred to be disposable from their name or
ACL alone. `/gbop action:status` shows a concise retention reason; GBOP does not
send repeated cleanup DMs. Missing delete/history permissions never keep AI work
running. No automatic cleanup erases channel text. Discord has no atomic
"check occupants/history and delete" endpoint, so cleanup rechecks cached gateway
state immediately before deletion; administrators can bypass text restrictions.
Live Discord voice/disconnect and permission behavior should be smoke-tested.
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
The exclusive member permission overwrite identifies the owner for voice routing.
Automatic deletion additionally requires the runtime's record of creating that
room; names and ACLs alone are insufficient. `/meet` prefers a tracked temporary
room and, when only older rooms exist, creates one without changing the old rooms.
Repeated meetings reuse that tracked room. The meeting reply and status identify
preserved older rooms. Without Manage Channels, `/meet` can reuse an already
correct private legacy room and explains that it stays after hang-up; it does not
change legacy permissions. `/gbop action:room` keeps its existing-room behavior.
After a restart, prior rooms become untracked and are preserved; the next `/meet`
creates a new temporary room when permissions allow. Durable provenance across
restarts is not implemented.

## Two simultaneous Discord rooms: one-time owner setup

1. Give the primary GBOP role **Manage Channels**, **View Channels**, **Connect**,
   **Read Message History** (to verify empty text before temporary-room cleanup),
   **Speak** and **Move Members** (for `/meet`'s automatic member move). If an existing category overrides these, create rooms at the
   server root (the default here) or fix the category permissions.
2. In the Discord Developer Portal, create a second application/bot such as
   **GBOP Voice 2**. Enable **Server Members Intent**, matching the client's
   configured intents. Invite it to G.T.O.P with the `bot` scope and View Channels,
   Connect and Speak. It does not need Administrator or its own slash commands.
3. Store its distinct bot token as **GBOP_VOICE_HELPER_TOKEN** in the existing
   Render service's Environment settings. Do not put it in chat or GitHub. The
   normal Render restart loads it; no new worker, VM or plan is required by code.
4. Run `/gbop action:status` and confirm two bot identities are ready. Existing
   private rooms reopen without editing permissions when access is already correct.
   If a room predates the helper, grant the primary bot **Manage Permissions** in
   that channel (Edit Channel → Permissions), then `/gbop action:room` can update
   bot access. Discord uses the `manage_roles` permission for this channel setting.
   Creating a room requires Manage Channels; changing its access requires the
   separate permission. Administrator is not needed.
5. Both members use `/meet`. If not already in voice, each joins the linked private
   room. GBOP joins automatically using an
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

For `/meet`, verify both an already-connected member (automatic move) and a
text-only member (room link, then Join Voice). Repeat the command in the same room,
try it while slots are busy, deny Move Members temporarily in a test server, and
leave during startup. Check that inactive/revoked members cannot create a room or
move themselves through the command, and that a room with shared permissions is
refused. These acceptance checks require an authorized Discord test; local tests
mock Discord transport and cannot confirm microphone access or client-specific taps.
