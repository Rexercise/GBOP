"""Select an existing Discord voice room without changing its access or history.

Discord's persisted channels/ACLs are the source of truth. Call with a fresh
guild.fetch_channels() result under the same guild lock as create and cleanup.
The cleanup registry proves deletion eligibility only, never reuse eligibility.
"""
import discord


class PrivateRoomAccessError(RuntimeError):
    """Static member-facing failure; never includes another room's history."""


def select_private_room(channels, guild, member, bot_members, registry):
    if member.bot or member.guild.id != guild.id:
        raise PrivateRoomAccessError("GBOP could not verify the requesting member's server.")
    allowed_ids = {member.id, *(bot.id for bot in bot_members)}
    candidates, safe = [], []
    for room in channels:
        if (room.guild.id != guild.id or room.type != discord.ChannelType.voice
                or not room.name.startswith('gbop-private-') or room.id in registry.deleted):
            continue
        # overwrites_for uses Discord IDs even when the member cache is empty.
        # private_room_owner alone cannot identify an uncached Object(User).
        owner_acl = room.overwrites_for(member)
        record = registry.rooms.get(room.id)
        tracked = record and record.guild_id == guild.id and record.owner_id == member.id
        if not tracked and not (owner_acl.view_channel is True or owner_acl.connect is True):
            continue
        candidates.append(room)
        base = room.overwrites_for(guild.default_role)
        if (owner_acl.view_channel is not True or owner_acl.connect is not True
                or base.view_channel is not False or base.connect is not False):
            continue
        # Unknown cache targets fail closed too; never assume they are bots.
        if any(target.id not in allowed_ids and
               (acl.view_channel is True or acl.connect is True)
               for target, acl in room.overwrites.items()):
            continue
        if not all(all(getattr(room.permissions_for(person), field, False)
                       for field in ('view_channel', 'connect', 'speak'))
                   for person in [member, *bot_members]):
            continue
        safe.append(room)
    if candidates and not safe:
        raise PrivateRoomAccessError(
            "Your existing GBOP room has shared access or needs private/bot permissions restored. "
            "It was kept unchanged. Ask the server owner to check its permissions; "
            "GBOP has not created another room.")
    current_id = getattr(getattr(member, 'voice', None), 'channel', None)
    current_id = getattr(current_id, 'id', None)
    room = min(safe, key=lambda room: (room.id != current_id, room.id), default=None)
    registry.preserved_rooms[(guild.id, member.id)] = {
        candidate.id for candidate in safe if candidate.id not in registry.rooms}
    return room
