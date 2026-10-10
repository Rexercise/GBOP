"""Immutable owner-message previews and bounded Discord image uploads.

Only the explicitly previewed IDs may be delivered to. The caller verifies
current human guild membership for selected/all_server_members recipients and
GBOP eligibility for all/all_except, before the draft and again before each send.
"""
from dataclasses import dataclass
import asyncio
import io
import re


AUDIENCE_LABELS = {
    'all': 'All currently eligible members',
    'selected': 'Selected server members only',
    'all_server_members': 'All current human server members (with or without GBOP access)',
    'all_except': 'All eligible members except selected members',
}
PREVIEW_SECONDS = 600
RECIPIENTS_PER_PAGE = 10
MESSAGE_HEADER = '📣 **GBOP Message**\n\n'
MAX_BROADCAST_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class BroadcastImage:
    """Snapshot the upload once so preview and every recipient get identical bytes."""
    data: bytes
    filename: str
    spoiler: bool = False

    def to_file(self):
        # discord.File is single-use. Give every preview/send its own stream.
        import discord
        return discord.File(io.BytesIO(self.data), filename=self.filename, spoiler=self.spoiler)


async def read_broadcast_image(attachment):
    if not 0 < attachment.size <= MAX_BROADCAST_IMAGE_BYTES:
        raise ValueError('Choose an image up to 10 MiB. Nothing was sent.')
    try:
        data = await asyncio.wait_for(attachment.read(), timeout=20)
    except Exception:
        raise ValueError('The image could not be downloaded. Upload it again; nothing was sent.') from None
    if not 0 < len(data) <= MAX_BROADCAST_IMAGE_BYTES:
        raise ValueError('Choose an image up to 10 MiB. Nothing was sent.')
    # Do not trust a renamed file or its declared MIME type. Preserve the bytes;
    # no re-encoding, arbitrary URL fetching, or permanent storage.
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        extension = 'png'
    elif data.startswith(b'\xff\xd8\xff'):
        extension = 'jpg'
    elif data.startswith((b'GIF87a', b'GIF89a')):
        extension = 'gif'
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        extension = 'webp'
    else:
        raise ValueError('Upload a PNG, JPEG, GIF, or WebP image. Nothing was sent.')
    spoiler = attachment.is_spoiler()
    filename = ('SPOILER_' if spoiler else '') + f'gbop-image.{extension}'
    return BroadcastImage(data, filename, spoiler)


def parse_selection(audience, raw):
    """Accept exact user mentions/IDs, never ambiguous names or role mentions."""
    if audience not in AUDIENCE_LABELS:
        raise ValueError('Choose all, selected, all_server_members, or all_except for the audience.')
    raw = (raw or '').strip()
    if audience in ('all', 'all_server_members'):
        if raw:
            raise ValueError('Use selected or all_except when providing members.')
        return ()
    if not raw:
        raise ValueError('Provide the members as @user mentions or user IDs, separated by spaces or commas.')
    ids = []
    for token in re.split(r'[\s,]+', raw):
        match = re.fullmatch(r'(?:<@!?(\d{1,20})>|(\d{1,20}))', token)
        if not match:
            raise ValueError('Use exact @user mentions or user IDs. Names and role mentions are not supported.')
        user_id = int(match.group(1) or match.group(2))
        if not 0 < user_id < 2**64:
            raise ValueError('Each selected member needs a valid Discord user ID.')
        if user_id not in ids:
            ids.append(user_id)
    return tuple(ids)


def owner_message(text, image=None):
    text = text.strip()
    if not text and image is None:
        raise ValueError('Please provide a message or image to preview.')
    if len(text) > 1800:
        raise ValueError('Keep the message within 1,800 characters. Nothing has been shortened or sent.')
    return MESSAGE_HEADER + text if text else MESSAGE_HEADER.rstrip()


@dataclass(frozen=True)
class BroadcastRecipient:
    user_id: int
    name: str


@dataclass(frozen=True)
class BroadcastDraft:
    owner_id: int
    guild_id: int
    audience: str
    message: str
    recipients: tuple[BroadcastRecipient, ...]
    excluded_count: int = 0
    image: BroadcastImage | None = None

    @property
    def page_count(self):
        return (len(self.recipients) + RECIPIENTS_PER_PAGE - 1) // RECIPIENTS_PER_PAGE

    def page(self, index):
        if not 0 <= index < self.page_count:
            raise ValueError('Unknown recipient page.')
        start = index * RECIPIENTS_PER_PAGE
        return self.recipients[start:start + RECIPIENTS_PER_PAGE]


def build_draft(owner_id, guild_id, text, audience, selected_ids, eligible, image=None):
    """Resolve the audience once. Missing selections fail the whole preview."""
    if audience not in AUDIENCE_LABELS:
        raise ValueError('Unknown audience.')
    available = {int(member.id): member for member in eligible}
    selected = set(selected_ids)
    if (audience in ('all', 'all_server_members') and selected
            or audience in ('selected', 'all_except') and not selected):
        raise ValueError('The audience and member selection do not match.')
    missing = selected - available.keys()
    if missing:
        labels = ', '.join(str(value) for value in sorted(missing)[:20])
        if len(missing) > 20:
            labels += f' (and {len(missing) - 20} more)'
        requirement = ('not verified current human members of G.T.O.P' if audience == 'selected'
                       else 'not currently eligible or could not be verified')
        raise ValueError(f'These selected IDs are {requirement}: '
                         + labels + '. Update the selection and preview again.')
    if audience == 'selected':
        recipient_ids = selected
    elif audience == 'all_except':
        recipient_ids = available.keys() - selected
    else:
        recipient_ids = available.keys()
    recipients = tuple(BroadcastRecipient(user_id, str(available[user_id].display_name))
                       for user_id in sorted(recipient_ids))
    if not recipients:
        raise ValueError('No eligible recipients remain in this audience. No messages were sent.')
    return BroadcastDraft(int(owner_id), int(guild_id), audience, owner_message(text, image),
                          recipients, len(selected) if audience == 'all_except' else 0, image)
