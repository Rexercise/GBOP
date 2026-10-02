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
| `/gbop action:pause` | Stop sending your audio and close your AI session |
| `/gbop action:resume` | Resume your listening in the connected channel |
| `/gbop action:status` | Check your connection and pause state |
| `/gbop action:help` | Show examples and controls privately |
| `/gbop action:leave` | End the channel session; requires being there or owner access |
| `/voicehealth` | Existing detailed voice diagnostics |

Pausing one member does not pause others. Leaving closes that member's AI
session. GBOP disconnects when no human members remain. One bot connection serves
one voice channel per server; summoning from another channel does not move her
away from an active session. An @mention saying “join me” points to `/gbop`.

This uses the existing Render service and OpenAI voice connection; no new hosted
resource or paid plan is added. Normal OpenAI voice usage still consumes credits.

## Live acceptance check

After deployment, two authorized members should join the same channel and run
`/gbop`. Verify a timestamped price against MT5, ask a CRT follow-up, interrupt a
spoken reply, pause/resume one member while the other continues, and leave the
channel. Confirm access denial for inactive/revoked members. Test trade writes
only with an explicitly identified test trade. Automated tests cover routing and
session lifecycle, not Discord microphone transport or a real spoken exchange.
