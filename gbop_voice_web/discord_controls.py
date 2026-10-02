"""Member-facing Discord voice controls; no network or credentials."""
import re


VOICE_HELP = (
    "**GBOP during your shift**\n"
    "Join a voice channel, then use `/gbop` (or `/voice`). Speak naturally; "
    "interrupt by speaking. No wake word is required.\n\n"
    "• ‘What's the NAS100 price?’\n"
    "• ‘Review today's gold 9ate8. When was the range purged?’\n"
    "• ‘Log a NAS100 short, Blessed Thief, half an R.’\n"
    "• ‘Add an entry to my open trade.’\n"
    "• ‘I closed at two R. Journal that I followed my plan.’\n"
    "• ‘Show my risk profile / journal / daily recap.’\n\n"
    "`/gbop action:pause` stops **your** audio being sent; resume with "
    "`/gbop action:resume`. `/gbop action:leave` disconnects GBOP for the channel.\n"
    "DM GBOP or @mention her for text questions and photo uploads. "
    "Photos and requested private records can be delivered by DM.\n\n"
    "Voice replies are audible to everyone in the channel. Use DMs for private "
    "journal details. Active voice sends authorized members' speech to OpenAI; "
    "normal API usage charges apply. GBOP records what you report; she does not "
    "place broker orders or automatically see your positions."
)


def summon_requested(text):
    """Only explicit summons, never ordinary trading discussion."""
    return bool(re.fullmatch(
        r"(?:please\s+)?(?:join(?:\s+(?:me|us|my (?:voice )?channel|our (?:voice )?channel))?"
        r"|come (?:here|to (?:my|our) (?:voice )?channel)|start voice)[.!?\s]*",
        text.strip(), re.IGNORECASE,
    ))


def in_voice_channel(member, voice_client):
    channel = getattr(getattr(member, 'voice', None), 'channel', None)
    current = getattr(voice_client, 'channel', None)
    return bool(channel and current and channel.id == current.id)
