"""Neutral no-trade reflections and explicit, durable optional reason replies.

The original check-in is retained verbatim. A reason is appended to that same
row; the existing delivery ledger binds its prompt to the member and shift.
Nothing here creates a trade, changes a grade, or infers plan adherence.
"""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from gbop_voice_web.checkin_routing import _REQUEST, _eligible, _prompt_key


REASON_MARKER = '\nNo-trade reason: '
_EVENT_PREFIX = 'checkin_no_trade_reason:'
_NO_TRADE = re.compile(
    r"^(?:i\s+)?(?:did(?:n't|nt| not)\s+trade|"
    r"(?:have(?:n't|nt| not)|haven't)\s+traded|"
    r"have(?:n't|nt| not)\s+(?:taken|placed|made|entered|executed)\s+(?:(?:any|a)\s+)?trades?|"
    r"did(?:n't|nt| not)\s+(?:take|place|make|enter|execute)\s+(?:(?:any|a)\s+)?trades?|"
    r"(?:took|placed|made)\s+(?:no|zero|0)\s+trades?|"
    r"(?:no|zero|0)\s+trades?|sat\s+out)\b", re.I)
_SCOPE = re.compile(r'^(?:\s+(?:at all|during this shift|during the shift|this (?:day |night )?shift|the (?:day |night )?shift|today|tonight|this session|the session))*', re.I)
# A deliberately conservative guard: don't turn a mixed trade account into a
# zero-trade claim. A member can still submit it as an ordinary check-in.
_EXECUTION = re.compile(
    r"\b(?:i\s+)?(?:traded|entered|bought|sold|executed|closed|exited|shorted|"
    r"did trade|went (?:long|short))\b|"
    r"\b(?:made|opened|took|placed|had) (?:[\w+-]+ ){0,3}"
    r"(?:trades?|scalps?|longs?|shorts?|positions?|entries|entry|profits?|loss|losses|win|wins)\b|"
    r"\b(?:except|apart from|other than)\b|"
    r"\b(?:one|two|three|four|five|[1-9][0-9]*) (?:[\w+-]+ ){0,2}"
    r"(?:trades?|scalps?|longs?|shorts?|positions?|entries)\b", re.I)
_SKIP = re.compile(r"^(?:skip|pass|cancel|stop|no thanks|rather not say|prefer not to say|"
                   r"unknown|not sure|i don(?:'t|t| not) know|unsure)[.!]*$", re.I)


def _normalized(text):
    return re.sub(r'\s+', ' ', (text or '').replace('’', "'").strip()).lower()


def reason_kind(text):
    """Classify only the member's explicit reason; never a coaching score."""
    value = _normalized(text)
    if re.search(r"\b(?:not|wasn't|wasnt) (?:because|due to)\b", value):
        return 'other'
    if _SKIP.fullmatch(value):
        return 'unknown'
    if re.search(r"\b(?:no|none|nothing|didn't|didnt|did not|couldn't|couldnt|could not)\b.{0,55}\b"
                 r"(?:setup|setups|qualif|criteria|a\+|opportunit)", value) or re.search(
            r"\b(?:setup|setups)\b.{0,40}\b(?:didn't|didnt|did not)\s+(?:meet|qualify)", value):
        return 'no_qualifying_setup'
    if re.search(r"\b(?:was |wasn't |not )?(?:away|unavailable|asleep|working|busy)\b|"
                 r"\bat work\b|\boff (?:the )?(?:desk|screens?)\b|\bwasn't (?:at|on)\b", value):
        # Negated reasons are kept as other rather than guessed.
        if not re.search(r"\b(?:not|wasn't|wasnt) (?:away|unavailable|asleep|working|busy|at work|off (?:the )?(?:desk|screens?))\b", value):
            return 'away'
    return 'other'


def no_trade_report(text, shift=None):
    """Recognize an unambiguous first-person zero-trade report, or return None."""
    original, marker, appended = (text or '').partition(REASON_MARKER)
    value = _normalized(original)
    match = _NO_TRADE.match(value)
    if not match:
        return None
    if ((shift == 'day' and re.search(r'\b(?:tonight|night shift)\b', value))
            or (shift == 'night' and re.search(r'\bday shift\b', value))):
        return None
    tail = value[match.end():]
    scope = _SCOPE.match(tail)
    tail = tail[scope.end():]
    if tail and not re.match(r'^(?:\s*[,.;!]|\s+(?:because|as|since|due to)\b)', tail):
        return None  # e.g. "I didn't trade gold" or "no trades yesterday"
    if '?' in value or _EXECUTION.search(value[match.end():] + ' ' + _normalized(appended)):
        return None
    # Explicit dates/other periods are not silently treated as the target shift.
    if re.search(r'\b(?:yesterday|tomorrow|last|previous|next)\b|\b\d{4}-\d{2}-\d{2}\b', value):
        return None
    reason = appended.strip() if marker else tail.strip(' ,.;!')
    reason = re.sub(r'^(?:because|as|since|due to)\s+', '', reason, flags=re.I)
    return {'reason': reason_kind(reason) if reason else None,
            'reason_text': appended.strip() if marker else reason,
            'original': original}


def _message_id(value):
    return (not isinstance(value, bool) and str(value).isdecimal()
            and int(str(value)) > 0)


def _recorded_activity(db, guild, user, row):
    """Read existing review date semantics, including reported imported trades.

    Logs can conflict with a reflection without proving an actual fill. Keep the
    distinction in the acknowledgment instead of silently changing either.
    """
    from gbop_voice_web.snapshots import _review_records, _selected_date, _in_window, shift_period
    period = shift_period(row['shift_date'], row['shift'], ZoneInfo('America/New_York'))
    with db() as conn:
        theses, records = _review_records(conn, guild, user)
        trade_ids = {r['thesis_id'] for r in records if r['kind'] == 'trade'}
        if any(r['kind'] == 'trade' and _selected_date(r, period)[0] for r in records):
            return True
        if any(r['id'] in trade_ids and r.get('status') not in {'IDEA', 'JOURNALED'}
               and _in_window(r.get('created_at'), period) for r in theses):
            return True
        logs = conn.execute('''SELECT thesis_id,created_at FROM thesis_executions
            WHERE guild_id=? AND user_id=?''', (guild, user)).fetchall()
        return any(r['thesis_id'] in trade_ids and _in_window(r['created_at'], period) for r in logs)


def no_trade_acknowledgment(db, guild, user, saved):
    report = no_trade_report(saved['response'], saved.get('shift'))
    if report is None:
        return None
    try:
        activity = _recorded_activity(db, guild, user, saved)
    except Exception:
        # A saved self-report is still usable when review evidence is unavailable.
        return {'text': "Recorded your report of no trades this shift. I couldn't check the existing trade records right now.",
                'ask_reason': False}
    if activity:
        return {'text': 'Saved your no-trade report. There are also trade or execution records for this shift. '
                        'Those records are unchanged; please clarify which account is correct.',
                'ask_reason': False}
    text = 'Recorded: no trades this shift.'
    if report['reason'] is not None:
        return {'text': text + ' Your reason is saved with this shift’s check-in.', 'ask_reason': False}
    return {'text': text + ' Was that because no setup met your criteria, or were you away? '
                    'You can reply to this message with either, another reason, or skip. It’s optional.',
            'ask_reason': True}


def record_reason_prompt(db, guild, user, saved, message_id, now_utc=None):
    """Bind an actually sent acknowledgment; no guessed or replaced message IDs."""
    if not _message_id(message_id):
        return False
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return False
    with db() as conn:
        row = conn.execute('''SELECT * FROM post_shift_checkins
            WHERE id=? AND guild_id=? AND user_id=?''', (saved['id'], guild, user)).fetchone()
        if not row or row['response'] != saved['response'] or not _eligible(row, now):
            return False
        report = no_trade_report(row['response'], row['shift'])
        if not report or report['reason'] is not None:
            return False
        result = conn.execute('''INSERT INTO gbop_shift_deliveries
            (event_key,guild_id,user_id,discord_message_id,delivered_at) VALUES (?,?,?,?,?)
            ON CONFLICT(event_key,guild_id,user_id) DO NOTHING RETURNING event_key''',
            (f"{_EVENT_PREFIX}{saved['id']}:{row['shift_date']}:{row['shift']}",
             guild, user, str(message_id), now.isoformat())).fetchone()
        return result is not None


def save_no_trade_reason(db, guild, user, content, reply_message_id=None, now_utc=None, source_message_id=None):
    """Accept only a direct reply to this member's still-current reason prompt.

    Unrelated commands/questions pass through. The atomic response comparison
    prevents repeats or concurrent replies from appending more than one reason.
    The caller must recheck current member access before invoking this helper.
    """
    if not _message_id(reply_message_id):
        return None
    if source_message_id is not None and not _message_id(source_message_id):
        return None
    text = (content or '').strip()
    if not text or len(text) > 1900:
        return None
    normalized = _normalized(text)
    # Common subject-less answers are declarations, not questions beginning
    # with "was" or "could". Keep the original member wording in storage.
    route_text = text
    if re.match(r"^(?:was (?:away|at work|asleep|busy|unavailable)|could(?:n't|nt| not) find)\b", normalized):
        route_text = 'I ' + text
    skip = bool(_SKIP.fullmatch(normalized))
    # Even an explicit reply can change the subject. Only recognizable answers
    # or a member-framed explanation enter the optional reason slot.
    candidate = reason_kind(text) != 'other' or re.match(
        r"^(?:because\b|due to\b|other\b|my\b|i (?:was|wasn't|wasnt|am|had|have|did|didn't|didnt|"
        r"could|couldn't|couldnt|felt|needed|chose|decided|stayed|made|opened|took)\b)", normalized)
    if not candidate:
        return None
    if not skip and ('?' in text or _REQUEST.search(route_text)
            or re.search(r"\bi(?: want| need| would like|'d like)\b", normalized)
            or _EXECUTION.search(normalized)):
        return None
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return None
    with db() as conn:
        delivery = conn.execute('''SELECT event_key,delivered_at FROM gbop_shift_deliveries
            WHERE guild_id=? AND user_id=? AND discord_message_id=?
            AND event_key LIKE ?''', (guild, user, str(reply_message_id), _EVENT_PREFIX + '%')).fetchone()
        if delivery is None:
            return None
        parts = delivery['event_key'][len(_EVENT_PREFIX):].split(':')
        if len(parts) != 3 or not parts[0].isdecimal():
            return None
        row = conn.execute('''SELECT * FROM post_shift_checkins
            WHERE id=? AND guild_id=? AND user_id=?''', (int(parts[0]), guild, user)).fetchone()
        if (not row or row['shift_date'] != parts[1] or row['shift'] != parts[2]
                or _prompt_key(row) is None or not _eligible(row, now)
                or not _eligible({'prompt_sent_at': delivery['delivered_at']}, now)):
            return None
        # A newly delivered formation ends the old optional conversation. An
        # explicit old reply is never redirected into the new check-in.
        newer = conn.execute('''SELECT id FROM post_shift_checkins WHERE guild_id=? AND user_id=?
            AND prompt_sent_at>? LIMIT 1''', (guild, user, row['prompt_sent_at'])).fetchone()
        if newer:
            return None
        report = no_trade_report(row['response'], row['shift'])
        if not report:
            return None
        if report['reason'] is not None:
            return {'id': row['id'], 'status': 'already_answered'}
        response = row['response'] + REASON_MARKER + text
        saved = conn.execute('''UPDATE post_shift_checkins SET response=?
            WHERE id=? AND guild_id=? AND user_id=? AND response=? AND prompt_sent_at=?
            RETURNING id''', (response, row['id'], guild, user, row['response'], row['prompt_sent_at'])).fetchone()
        if saved is None:
            return {'id': row['id'], 'status': 'already_answered'}
        if source_message_id is not None:
            conn.execute('''INSERT INTO gbop_shift_deliveries
                (event_key,guild_id,user_id,discord_message_id,delivered_at) VALUES (?,?,?,?,?)
                ON CONFLICT(event_key,guild_id,user_id) DO NOTHING''',
                (f"checkin_no_trade_reason_reply:{row['id']}:{row['shift_date']}:{row['shift']}",
                 guild, user, str(source_message_id), now.astimezone(timezone.utc).isoformat()))
        return {'id': row['id'], 'status': 'saved', 'response': response,
                'reason': reason_kind(text), 'skipped': skip}
