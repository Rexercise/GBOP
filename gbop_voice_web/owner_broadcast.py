"""Immutable owner-message previews; no storage, credentials, or transport.

Only the explicitly previewed IDs may be delivered to. Discord/GBOP eligibility
is resolved by the caller before building a draft and again before each send.
"""
from dataclasses import dataclass
import re


AUDIENCE_LABELS = {
    'all': 'All currently eligible members',
    'selected': 'Selected members only',
    'all_except': 'All eligible members except selected members',
}
PREVIEW_SECONDS = 600
RECIPIENTS_PER_PAGE = 10
MESSAGE_HEADER = '📣 **GBOP Message from the Owner**\n\n'


def parse_selection(audience, raw):
    """Accept exact user mentions/IDs, never ambiguous names or role mentions."""
    if audience not in AUDIENCE_LABELS:
        raise ValueError('Choose all, selected, or all_except for the audience.')
    raw = (raw or '').strip()
    if audience == 'all':
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


def owner_message(text):
    text = text.strip()
    if not text:
        raise ValueError('Please provide a message to preview.')
    if len(text) > 1800:
        raise ValueError('Keep the message within 1,800 characters. Nothing has been shortened or sent.')
    return MESSAGE_HEADER + text


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

    @property
    def page_count(self):
        return (len(self.recipients) + RECIPIENTS_PER_PAGE - 1) // RECIPIENTS_PER_PAGE

    def page(self, index):
        if not 0 <= index < self.page_count:
            raise ValueError('Unknown recipient page.')
        start = index * RECIPIENTS_PER_PAGE
        return self.recipients[start:start + RECIPIENTS_PER_PAGE]


def build_draft(owner_id, guild_id, text, audience, selected_ids, eligible):
    """Resolve the audience once. Missing selections fail the whole preview."""
    if audience not in AUDIENCE_LABELS:
        raise ValueError('Unknown audience.')
    available = {int(member.id): member for member in eligible}
    selected = set(selected_ids)
    if (audience == 'all' and selected) or (audience != 'all' and not selected):
        raise ValueError('The audience and member selection do not match.')
    missing = selected - available.keys()
    if missing:
        labels = ', '.join(str(value) for value in sorted(missing)[:20])
        if len(missing) > 20:
            labels += f' (and {len(missing) - 20} more)'
        raise ValueError('These selected IDs are not currently eligible or could not be verified: '
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
    return BroadcastDraft(int(owner_id), int(guild_id), audience, owner_message(text),
                          recipients, len(selected) if audience == 'all_except' else 0)
