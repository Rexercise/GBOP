"""Member-facing Discord voice controls; no network or credentials."""
import re


VOICE_HELP = (
    "**GBOP during your shift**\n"
    "Join a voice channel, then use `/gbop` (or `/voice`). Speak naturally; "
    "interrupt by speaking. No wake word is required.\n\n"
    "For simultaneous individual browser conversations use `/gbop action:private`. "
    "Each member signs in with their own Discord account.\n\n"
    "For a private room inside Discord use `/gbop action:room`, join it, then `/gbop`. "
    "Both routes use your same saved member records.\n\n"
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


def private_room_owner(channel):
    """Identify our private rooms by their prefix and exclusive member ACL."""
    if not getattr(channel, 'name', '').startswith('gbop-private-'):
        return None
    owners = [target.id for target, permissions in channel.overwrites.items()
              if hasattr(target, 'bot') and not target.bot
              and permissions.view_channel is True and permissions.connect is True]
    return owners[0] if len(owners) == 1 else None


def pick_voice_guild(clients, guild_id, channel_id):
    """Reuse the requested room, otherwise select an idle bot identity."""
    available = []
    for bot in clients:
        if not bot.is_ready():
            continue
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        vc = guild.voice_client
        if vc is not None and vc.channel.id == channel_id:
            return guild
        if vc is None:
            available.append(guild)
    return available[0] if available else None


def voice_readiness(clients, guild_id):
    """Credential-free gateway and server readiness for operations logs."""
    slots = []
    for number, bot in enumerate(clients, 1):
        guild = bot.get_guild(guild_id)
        ready = bool(bot.is_ready() and guild and not getattr(guild, 'unavailable', False))
        permissions = getattr(getattr(guild, 'me', None), 'guild_permissions', None)
        slots.append({
            'slot': number,
            'ready_here': ready,
            'in_server': guild is not None,
            'voice_permissions': all(bool(getattr(permissions, name, False))
                                     for name in ('view_channel', 'connect', 'speak')),
            'manage_channels': bool(getattr(permissions, 'manage_channels', False)),
        })
    return {'configured': len(slots), 'ready_here': sum(slot['ready_here'] for slot in slots),
            'slots': slots}
