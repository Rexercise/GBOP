"""Conservative deletion of GBOP-created, content-free temporary voice rooms.

The in-memory registry proves creation by this process. Channel names/ACLs alone
never prove ownership; after a restart old rooms are retained. No DB/member
records are touched. A missing history permission must NOT be treated as empty
history (Discord can return an empty list in that case).
"""
from dataclasses import dataclass

from gbop_voice_web.discord_controls import private_room_autojoin_allowed


@dataclass
class TemporaryRoom:
    guild_id: int
    owner_id: int
    generation: int = 0
    pending_entry: bool = True
    saw_message: bool = False


class PrivateRoomCleanup:
    def __init__(self):
        self.rooms = {}
        self.notices = {}
        self.preserved_rooms = {}
        self.deleted = set()

    def created(self, room, member):
        self.rooms[room.id] = TemporaryRoom(member.guild.id, member.id)
        self.notices.pop((member.guild.id, member.id), None)

    def reserve(self, room, member):
        record = self.rooms.get(room.id)
        if record is not None:
            record.generation += 1
            current = getattr(getattr(member, 'voice', None), 'channel', None)
            record.pending_entry = current is None or current.id != room.id

    def entered(self, room, member):
        record = self.rooms.get(room.id)
        if record is not None and record.owner_id == member.id:
            record.generation += 1
            record.pending_entry = False

    def message_seen(self, channel_id):
        record = self.rooms.get(channel_id)
        if record is not None:
            record.saw_message = True

    def token(self, channel_id):
        record = self.rooms.get(channel_id)
        return (record, record.generation) if record else None

    def notice(self, guild_id, owner_id):
        key = (guild_id, owner_id)
        parts = [self.notices.get(key, '')]
        preserved = self.preserved_rooms.get(key)
        if preserved:
            rooms = ', '.join(f'<#{channel_id}>' for channel_id in sorted(preserved))
            parts.append(f'Earlier room(s) {rooms} are kept to protect their message history.')
        return ' '.join(part for part in parts if part)

    def keep(self, record, reason):
        self.notices[(record.guild_id, record.owner_id)] = 'Room kept: ' + reason
        return False

    async def remove_if_empty(self, guild, channel_id, token, connected):
        """Called under the same guild lock as /meet and voice connection setup.

        ``connected`` reads current bot clients. Recheck after every network wait;
        gateway events can update occupants even while the guild lock is held.
        """
        if token is None:
            return False
        record, generation = token
        def unchanged_and_empty():
            channel = guild.get_channel(channel_id)
            if (self.rooms.get(channel_id) is not record or record.generation != generation
                    or record.pending_entry or channel is None or channel.members
                    or connected(channel_id)):
                return False
            return True

        if record.guild_id != guild.id:
            return False
        current = guild.get_channel(channel_id)
        if current and any(not person.bot and person.id != record.owner_id for person in current.members):
            return self.keep(record, 'someone is still in the room.')
        if not unchanged_and_empty():
            return False
        if record.saw_message:
            return self.keep(record, 'it contains channel messages; deletion needs your review.')
        # Read the latest ACL and last-message marker from Discord, not just cache.
        room = await guild.fetch_channel(channel_id)
        if not unchanged_and_empty():
            return False
        owner = guild.get_member(record.owner_id)
        if owner is None or not private_room_autojoin_allowed(owner, room):
            return self.keep(record, 'its owner or private permissions changed.')
        # Only rooms whose text posting remains disabled can be temporary.
        for overwrite in room.overwrites.values():
            if getattr(overwrite, 'send_messages', None) is not False:
                return self.keep(record, 'its text permissions changed.')
        permissions = room.permissions_for(guild.me)
        if not all(getattr(permissions, field, False) for field in
                   ('view_channel', 'connect', 'read_message_history', 'manage_channels')):
            return self.keep(record, 'GBOP cannot safely verify history or remove this room.')
        if getattr(room, 'last_message_id', None) is not None:
            return self.keep(record, 'it contains channel messages; deletion needs your review.')
        async for _ in room.history(limit=1):
            record.saw_message = True
            return self.keep(record, 'it contains channel messages; deletion needs your review.')
        if not unchanged_and_empty():
            return False
        cached = guild.get_channel(channel_id)
        if (not private_room_autojoin_allowed(owner, cached)
                or any(getattr(acl, 'send_messages', None) is not False
                       for acl in cached.overwrites.values())):
            return self.keep(record, 'its permissions changed during cleanup.')
        if record.saw_message or getattr(cached, 'last_message_id', None) is not None:
            return self.keep(record, 'channel activity appeared during cleanup.')
        # No API can atomically test occupants/history and delete. No grace timer:
        # this final check immediately precedes the DELETE request. Normal users
        # cannot post here; admin-created content also fails closed when observed.
        await room.delete(reason='GBOP temporary meeting ended; verified empty voice and text')
        self.rooms.pop(channel_id, None)
        self.notices.pop((record.guild_id, record.owner_id), None)
        # Discord's channel-delete gateway event may trail the HTTP response.
        # Never reuse a stale cached room in that interval.
        self.deleted.add(channel_id)
        return True
