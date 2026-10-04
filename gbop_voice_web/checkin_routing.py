"""Explicit, scoped post-shift check-in replies; ordinary DMs stay conversational."""
import re
from datetime import date, datetime, timedelta, timezone


MAX_REPLY_AGE = timedelta(hours=24)
_PREFIX = re.compile(r"^check[ -]?in\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL)
_ACTIONS = (
    r"watch|monitor|notify|alert|send|show|list|cancel|stop|start|open|close|"
    r"delete|edit|update|save|record|add|log|journal|review|analy[sz]e|get|give|tell"
)
_REQUEST = re.compile(
    r"(?:^|[.!?;,\n]\s*|\b(?:and|also|then|but)\s+)"
    r"(?:(?:hey|gbop|please|can you|could you|would you|will you)\s+)*"
    rf"(?:{_ACTIONS})\b|"
    rf"\b(?:please|you to)\s+(?:{_ACTIONS})\b|"
    r"\b(?:watch|monitor|notify|alert|cancel)\b|"
    r"\b(?:let me know|notify me|alert me|dm me|remind me|ping me|keep an eye on)\b|"
    r"(?:^|[.!?;,\n]\s*)(?:what|why|how|when|where|who|which|is|are|was|were|do|does|did|"
    r"can|could|should|would|will|have|has)\b",
    re.IGNORECASE,
)


def _prompt_key(row):
    try:
        day = date.fromisoformat(row['shift_date'])
        if row['shift'] == 'day':
            return f'{day.isoformat()}:day_formation'
        if row['shift'] == 'night':
            return f'{(day + timedelta(days=1)).isoformat()}:night_formation'
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _eligible(row, now):
    try:
        sent = datetime.fromisoformat(row['prompt_sent_at'].replace('Z', '+00:00'))
        return sent.tzinfo is not None and timedelta(0) <= now - sent <= MAX_REPLY_AGE
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def save_checkin_reply(db, guild_id, user_id, content, reply_message_id=None, now_utc=None):
    """Save only an explicit answer after the caller has verified member access.

    A Discord reply must target this member's recorded formation prompt. Plain
    messages require the advertised ``Check-in:`` prefix. Neither can absorb an
    operational request or question. No prompt or observation is created here.
    """
    text = (content or '').strip()
    prefix = _PREFIX.match(text)
    response = (prefix.group(1) if prefix else text).strip()
    if not response or '?' in response or _REQUEST.search(response):
        return None
    if not prefix and reply_message_id is None:
        return None
    if reply_message_id is not None and (
            isinstance(reply_message_id, bool) or not str(reply_message_id).isdecimal()
            or int(str(reply_message_id)) <= 0):
        return None
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return None
    scope = (int(guild_id), int(user_id))
    with db() as conn:
        candidates = conn.execute('''SELECT id,shift_date,shift,prompt_sent_at
            FROM post_shift_checkins WHERE guild_id=? AND user_id=? AND response IS NULL
            ORDER BY id DESC LIMIT 50''', scope).fetchall()
        candidates = sorted((row for row in candidates if _eligible(row, now)),
            key=lambda row: (datetime.fromisoformat(row['prompt_sent_at'].replace('Z', '+00:00')),
                             row['id']), reverse=True)
        for row in candidates:
            key = _prompt_key(row)
            if key is None:
                continue
            # A supplied reply target is authoritative even with a prefix: do
            # not redirect an answer to an unrelated, newer pending prompt.
            if reply_message_id is not None:
                delivered = conn.execute('''SELECT discord_message_id
                    FROM gbop_shift_deliveries WHERE event_key=? AND guild_id=?
                    AND user_id=? AND discord_message_id=?''',
                    (key,) + scope + (str(reply_message_id),)).fetchone()
                if delivered is None:
                    continue
            saved = conn.execute('''UPDATE post_shift_checkins
                SET response=?,responded_at=? WHERE id=? AND guild_id=? AND user_id=?
                AND response IS NULL AND prompt_sent_at=? RETURNING id''',
                (response, now.astimezone(timezone.utc).isoformat(), row['id']) + scope
                + (row['prompt_sent_at'],)).fetchone()
            if saved is not None:
                return {'id': saved['id'], 'response': response}
            return None
    return None
