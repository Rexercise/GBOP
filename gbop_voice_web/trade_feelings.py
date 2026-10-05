"""Optional member-reported feelings on the existing private trade journal.

No new table, DM listener, scheduled outreach, diagnosis, or transcription. Text
reports are grounded in the current authenticated utterance; audio-only intent
remains model-classified, just like the existing trade tools.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re

from gbop_voice_web.journal_context import binding, journal_transaction
from gbop_voice_web.journal_numbers import resolve_journal_selector
from gbop_voice_web.unified_journal import canonical_journal_id, ensure_canonical_journal, owned_journal_details

STAGES = ('open', 'add', 'mid', 'close')
MAX_REPORTS = 32
MAX_FEELING_CHARS = 500
COOLDOWN = timedelta(minutes=30)
PROMPT_TTL = timedelta(minutes=15)
FEELING_PROMPT = """
Feelings are an OPTIONAL self-reported journal variable, never inferred from a
win, loss, tone, position size or market move. After saving a real trade action,
use optional_feeling_prompt only if returned by the server, at most one brief
question in the response, and only if the member has not already shared a feeling.
Never delay or block trade saving or a close. Do not repeatedly ask on every add.
The member may ignore or decline; handle unrelated requests normally. There is
no pending-answer mode and no new proactive DM. Don't volunteer private feelings
in shared voice; offer the optional question only in the existing private exchange.
Use record_trade_feeling for an explicit member report, preserving their words.
Resolve the displayed Trade #; ask which trade if unclear, never pick the newest.
Use stage open/add/mid/close only as reported or clear from their immediate reply
to that stage's question; clarify uncertain stage. reported_at is a member-stated
ISO time with date/timezone, otherwise null; logging time does not prove event time.
Corrections append with correction_of from the saved feeling id; don't overwrite
history. An explicit decline uses skip=true for that trade, no feeling value.
Keep existing legacy emotion metadata readable; use the history tool for new
conversational feelings instead of copying the report into emotion or study_note.
When reflecting, use saved feeling history with its stage, sample size, unknowns
and actual trade outcomes. Describe associations, never causes, predictions,
diagnoses or a reason to increase risk. Missing feelings stay unknown.
"""


FEELING_VOICE_PROMPT = ("Feelings OPTIONAL: ask returned optional_feeling_prompt privately once; skip/ignore continues normally, no new DM. "
    "record_trade_feeling saves member words only, resolved Trade #, stage/stated time (unknown=null); append corrections. "
    "No inference, diagnosis or blocked trades. Review associations with sample sizes/missingness, never causes.")


def _now():
    return datetime.now(timezone.utc)


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Reported feeling time must include a date and timezone, or be null.')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Reported feeling time needs an explicit timezone; do not use logging time.')
    return parsed


def _normalize(text):
    return ' '.join(str(text).casefold().replace('’', "'").split())


def _explicit_self_report(text):
    return bool(re.search(r"\b(?:i\s+(?:feel|felt|am feeling|was feeling)|i'm\s+feeling|i(?:'ve| have)\s+been feeling|my\s+(?:feeling|emotion|mood)(?:s)?\s*(?:is|was|:)|i(?:'m| am| was)\s+(?:calm|anxious|nervous|confident|afraid|scared|excited|frustrated|angry|bored|uncertain|relaxed|stressed|worried|overwhelmed|hesitant|impatient|focused|tired|happy|sad|hopeful|fearful|relieved|uneasy|optimistic))\b", _normalize(text)))


def _report_clauses(text):
    # Compare clause content without its sentence-ending punctuation. The exact
    # original member text is still checked separately and saved unchanged.
    # A new subject cannot borrow the member's preceding first-person report.
    return [_normalize(part).strip(' .!;') for part in re.split(
        r'(?<=[.!?;])\s*|\n+|(?:,\s*|\b(?:and|but|while|whereas)\s+)'
        r'(?=(?:my|your|his|her|their|our|he|she|they|you|we)\b)',
        str(text).casefold().replace('’', "'")) if part.strip(' .!;\n')]


def _short_answer(text):
    """A short reply to this exact conversation's immediately preceding prompt.

    This is a write-tool guard only. It never consumes or reroutes a message.
    """
    value = _normalize(text).strip(' .!')
    if len(value) > 160 or '?' in value:
        return False
    # This fallback is only for bare answers such as "a little nervous". A
    # failed explicit report, hypothetical, or another person's words must not
    # become writable merely because a feeling question preceded the message.
    if (len(_report_clauses(value)) != 1 or _explicit_self_report(value)
            or re.search(r"\b(?:i|my|you|your|he|his|she|her|they|their|we|our|friend|if|suppose|imagine|would|could|should|said|says|feels|felt)\b", value)):
        return False
    if re.search(r'\b(?:watch|alert|monitor|notify|remind|send|show|buy|sell|stop|target|price|btc|nas100|journal|trade|chart|recap|review|shift|hello|thanks)\b', value):
        return False
    return bool(re.search(r'\b(?:calm|anxious|nervous|confident|afraid|scared|excited|frustrated|angry|bored|uncertain|relaxed|stressed|worried|overwhelmed|hesitant|impatient|focused|tired|happy|sad|hopeful|fearful|relieved|uneasy|optimistic|on edge|fomo)\b', value))


def _ground_report(args, guild, user):
    capability = binding(args)
    if capability is None:
        raise ValueError('Feelings must come from the current authenticated conversation.')
    # The transaction holds this capability through commit; this early check
    # also prevents a stale context from supplying grounding before DB work.
    with capability.guard(guild, user):
        context = capability.context
        text = getattr(context, '_client_text', None)
        if text is None:
            return  # Existing audio model classification; no fabricated ASR.
        if args.get('skip'):
            if not re.search(r"\b(?:skip|pass|prefer not|rather not|don't (?:ask|want)|do not (?:ask|want)|no thanks)\b", _normalize(text)):
                raise ValueError('Only skip a feeling check when the member explicitly declines.')
            return
        feeling = args.get('feeling')
        if not isinstance(feeling, str) or not _normalize(feeling) or _normalize(feeling) not in _normalize(text):
            raise ValueError('Preserve an exact feeling statement from this member’s current message; do not infer one.')
        # Tie the saved phrase to this member's own statement, not another
        # sentence about someone else or a hypothetical feeling question.
        clauses = _report_clauses(text)
        phrase = _normalize(feeling).strip(' .!;\n')
        if phrase and any(phrase in clause and '?' not in clause and _explicit_self_report(clause)
                and not re.search(r'\b(?:if|suppose|imagine|what if)\s+(?:that\s+)?i\b', clause)
                for clause in clauses):
            return
        prompt = getattr(context, '_feeling_prompt', None)
        if (prompt and prompt['trade_number'] == args.get('trade_number')
                and prompt['stage'] == args.get('stage')
                and prompt['generation'] + 1 == capability.generation
                and _now() - _time(prompt['recorded_at']) <= PROMPT_TTL
                and _short_answer(text)):
            return
        raise ValueError('No explicit self-reported feeling for this trade was identified. Keep it unknown and handle the member’s request normally.')


def _selected(conn, guild, user, number):
    if type(number) is not int or number < 1:
        raise ValueError('Which Trade # is this feeling about? Use its displayed number.')
    selected = resolve_journal_selector(conn, guild, user, trade_number=number)
    if not selected.get('ok'):
        raise ValueError(selected.get('error') or 'Trade not found in your account.')
    thesis_id = selected.get('thesis_id')
    if not thesis_id:
        raise ValueError('Choose the Trade #, not an unlinked legacy journal.')
    trade = conn.execute('SELECT * FROM theses WHERE id=? AND guild_id=? AND user_id=?', (thesis_id, guild, user)).fetchone()
    journal_id = canonical_journal_id(conn, guild, user, thesis_id)
    details = owned_journal_details(conn, guild, user, journal_id) if journal_id else None
    meta = json.loads(details['metadata'] or '{}') if details else {}
    if not trade or trade['status'] == 'IDEA' or meta.get('kind') in ('study', 'reflection'):
        raise ValueError('This is a study or idea, not a reported trade. Keep reflections in its existing journal notes.')
    return thesis_id, meta, dict(trade)


def record_feeling(db, guild, user, args):
    """Append one explicit report/correction, or remember an optional decline."""
    if any(key in args for key in ('journal_id', 'thesis_id', 'user_id', 'guild_id', 'feeling_history')):
        raise ValueError('Use the authenticated member and displayed Trade # only.')
    if args.get('skip') is not None and type(args['skip']) is not bool:
        raise ValueError('skip must be true or false.')
    _ground_report(args, guild, user)
    stage, feeling = args.get('stage'), args.get('feeling')
    if not args.get('skip'):
        if stage not in STAGES:
            raise ValueError('When did you feel that: opening, adding, during, or closing the trade?')
        if not isinstance(feeling, str) or not feeling.strip() or len(feeling) > MAX_FEELING_CHARS:
            raise ValueError('Keep the member’s feeling statement between 1 and 500 characters.')
        if args.get('reported_at') is not None:
            _time(args['reported_at'])
    elif feeling is not None or args.get('correction_of') is not None or args.get('reported_at') is not None:
        raise ValueError('A skipped question must not save a feeling, timestamp or correction.')
    with journal_transaction(db, args, guild, user, serialize=True) as conn:
        thesis_id, meta, trade = _selected(conn, guild, user, args.get('trade_number'))
        if args.get('skip'):
            state = deepcopy(meta.get('feeling_prompt_state') or {})
            state['declined'] = True
            ensure_canonical_journal(conn, guild, user, thesis_id, metadata={'feeling_prompt_state': state})
            return {'ok': True, 'skipped': True, 'trade_number': args['trade_number'], 'feeling_recorded': False}
        history = deepcopy(meta.get('feeling_history') or [])
        correction = args.get('correction_of')
        if correction is not None and (type(correction) is not int or not any(row.get('id') == correction for row in history)):
            raise ValueError('The feeling to correct was not found in this trade. Read its history first.')
        # Same session+turn+arguments is already deduplicated by the journal
        # writer. Exact repeated historical reports are also safe after reconnect.
        payload = {'stage': stage, 'feeling': feeling.strip(), 'reported_at': args.get('reported_at'), 'correction_of': correction}
        if payload['reported_at'] is not None:
            old = next((row for row in reversed(history) if all(row.get(k) == v for k, v in payload.items())), None)
            if old:
                return {'ok': True, 'saved': True, 'deduplicated': True, 'trade_number': args['trade_number'], 'feeling': old}
        if len(history) >= MAX_REPORTS:
            raise ValueError('This trade already has 32 feeling reports. Its full history was preserved; no new feeling was saved.')
        report = {'id': len(history) + 1, **payload, 'recorded_at': _now().isoformat(), 'source': 'member_reported'}
        history.append(report)
        updated = {**meta, 'feeling_history': history}
        # Same global editable-metadata budget as other journal writes; no
        # eviction, pruning or overwrite when the history is full.
        budget = deepcopy(updated)
        budget.pop('legacy_audit', None)
        if isinstance(budget.get('provenance'), dict):
            budget['provenance'].pop('corrections', None)
        if len(json.dumps(budget)) > 48000:
            raise ValueError('This journal is too large for another feeling report. Existing history was preserved.')
        ensure_canonical_journal(conn, guild, user, thesis_id, metadata={'feeling_history': history})
    return {'ok': True, 'saved': True, 'trade_number': args['trade_number'], 'feeling': report,
            'history_count': len(history), 'limits': 'Member self-report only; no inferred emotion or causation.'}


def optional_prompt(db, guild, user, name, args, result):
    """Claim at most an initial and a later close question, never a reply route.

    Called after a successful save. Failure here must never relabel that save as
    failed. Persistent markers prevent another prompt after reconnect/retry.
    """
    stages = {'open_trade': 'open', 'add_entry': 'add', 'record_trade_event': 'mid',
              'close_trade': 'close', 'save_journal_entry': 'mid'}
    if name not in stages or not result.get('ok'):
        return None
    number = result.get('trade_number') or result.get('trade_id')
    if not number:
        return None
    capability = binding(args)
    if capability is None:
        return None
    text = getattr(capability.context, '_client_text', None)
    if text and _explicit_self_report(text):
        return None  # The report is already present; the normal feeling tool saves it.
    with journal_transaction(db, args, guild, user, serialize=True) as conn:
        thesis_id, meta, trade = _selected(conn, guild, user, number)
        stage = stages[name]
        if name == 'save_journal_entry' and result.get('updated'):
            return None  # A correction should not become a fresh interview.
        state = deepcopy(meta.get('feeling_prompt_state') or {})
        if state.get('declined'):
            return None
        bucket = 'close' if stage == 'close' else 'initial'
        history = meta.get('feeling_history') or []
        if state.get(bucket) or (history and (bucket == 'initial' or any(r.get('stage') == 'close' for r in history))):
            return None
        now = _now()
        previous_times = [v for k, v in state.items() if k in ('initial', 'close')]
        previous_times += [r['recorded_at'] for r in history if r.get('recorded_at')]
        if previous_times and now - max(_time(v) for v in previous_times) < COOLDOWN:
            return None
        state[bucket] = now.isoformat()
        ensure_canonical_journal(conn, guild, user, thesis_id, metadata={'feeling_prompt_state': state})
        question = {'open': 'as you opened', 'add': 'as you added to', 'mid': 'during', 'close': 'as you closed'}[stage]
        wording = f'How did you feel {question} Trade #{number}? You can skip this.'
        if stage == 'close' and not meta.get('self_grade'):
            wording = f'How did the close feel for Trade #{number}, and would you like to add a SELF grade? Both are optional.'
        prompt = {'trade_number': number, 'stage': stage, 'recorded_at': now.isoformat(),
                  'question': wording,
                  'optional': True}
        capability.context._feeling_prompt = {**prompt, 'generation': capability.generation}
    return prompt


def effective_history(metadata):
    history = metadata.get('feeling_history') or []
    corrected = {row.get('correction_of') for row in history if row.get('correction_of') is not None}
    return [row for row in history if row.get('id') not in corrected]


def reflection_summary(rows, summarize):
    """Exact self-reported phrases, separate stages, one outcome per trade/group."""
    groups = {}
    reported = 0
    unknown_times = 0
    for row in rows:
        history = effective_history(row.get('metadata') or {})
        reported += bool(history)
        unknown_times += sum(item.get('reported_at') is None for item in history)
        seen = set()
        for item in history:
            key = (item['stage'], item['feeling'])
            if key not in seen:
                groups.setdefault(key, []).append(row)
                seen.add(key)
    return {'trades_with_feelings': reported, 'trades_without_feelings': len(rows) - reported,
            'reports_with_unknown_reported_time': unknown_times,
            'groups': [{'stage': stage, 'feeling': feeling, **summarize(values)}
                       for (stage, feeling), values in list(groups.items())[:20]],
            'group_count': len(groups), 'groups_omitted': len(groups) > 20,
            'limits': 'Exact self-reported phrases grouped by stage; corrections replace earlier reports only in this view. '
                'Groups overlap and must not be added together. Missing feelings are unknown. Descriptive associations only; '
                'small samples, selective reporting and timing can affect results. No causation, diagnosis or prediction.'}
