"""Optional member-reported feelings on the existing private trade journal.

No new table, DM listener, scheduled outreach, diagnosis, or transcription. Text
reports are grounded in authenticated member words, including a bounded reply
to a delivered clarification; audio-only intent remains model-classified.
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
An explicit Trade # note may be a natural fragment ("Felt calm at entry") or
multiple sentences describing changing confidence. Never require an "I felt"
prefix or paraphrase these words. If stage is unclear, ask one save question
naming the Trade # and proposed stage. An immediate yes confirms that original
note, not a new feeling; call the tool with the original exact words. Keep
unstated time and correction null. Preview/example/quoted reports are not saves.
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


# A finite self-report grammar is intentionally conservative. These are source
# guards, not emotion classification: the stored value always comes from the
# member's exact text, never the model's paraphrase or the assistant's question.
FEELING_WORDS = (r"calm|anxious|nervous|confident|afraid|scared|excited|frustrated|angry|"
    r"bored|uncertain|relaxed|stressed|worried|overwhelmed|hesitant|impatient|focused|"
    r"tired|happy|sad|hopeful|fearful|relieved|uneasy|optimistic|on edge|fomo")
TRADE_NUMBER = r"\btrade\s*#?\s*(\d+)\b"
NOTE_STAGE = r"opening|entry|adding|add[ -]entry|mid[ -]trade|closing"
NOTE_REQUEST = re.compile(r"^\s*(?:please\s+)?(?:note\s+(?:for|on)|"
    r"(?:save|record|log|add)\s+(?:(?:this|my|an?)\s+)?"
    r"(?:(?:as\s+(?:an?\s+)?)?(?:" + NOTE_STAGE + r")\s+)?"
    r"(?:(?:feeling|note)\s+)?(?:for|to|on))"
    r"\s+(?:my\s+)?trade\s*#?\s*(\d+)\s*[:,-]\s*(.+)$", re.I | re.S)
UNSAFE_SOURCE = re.compile(r"\b(?:if|suppose|imagine|pretend|hypothetical|example|preview|draft|"
    r"quote[ds]?|quotation|would|could|might|he|she|they|his|her|their|friend|someone|"
    r"said|says|wrote|told)\b|[\"“”]|(?:^|\s)'[^']+'|^\s*>|"
    r"\b(?:don't|do not|never)\s+(?:save|record|log|add)\b", re.I)


# A factual report may explain its cause using hypothetical or other-person
# context. Such context cannot itself supply the member's feeling or stage.
REPORT_CONTEXT = re.compile(r"\b(?:because|when|about)\b", re.I)
UNSAFE_FRAME = re.compile(r"\b(?:suppose|imagine|pretend|hypothetical|example|preview|draft|"
    r"quote[ds]?|quotation)\b|[\"“”]|(?:^|\s)'[^']+'|^\s*>|"
    r"\b(?:don't|do not|never)\s+(?:save|record|log|add)\b", re.I)


SAVE_OPTOUT = re.compile(r"\b(?:don't|do not|never|won't|will not|prefer not to|would rather not|rather not)\s+"
    r"(?:(?:[\w]+ly|yet|ever|even|now|just)\s+){0,3}(?:save|record|log|add)\b|"
    r"\b(?:avoid|refrain from|hold off(?: on)?|wait before|wait to)\s+"
    r"(?:sav(?:e|ing)|record(?:ing)?|log(?:ging)?|add(?:ing)?)\b", re.I)


def _report_head(text):
    return REPORT_CONTEXT.split(text, maxsplit=1)[0]


def _unsafe_source(text):
    if UNSAFE_FRAME.search(text) or SAVE_OPTOUT.search(text.replace("’", "'")):
        return True
    return any(UNSAFE_SOURCE.search(_report_head(part))
               for _, _, part in _report_clauses(text))


SUBJECT_PREDICATE = (r"felt|feels?|is|was|are|were|seem(?:s|ed)?|look(?:s|ed)?|"
    r"sound(?:s|ed)?|appear(?:s|ed)?|becomes?|became|has|have|had")
POSSESSIVE_FEELING_SUBJECT = (r"(?:[\w’'-]+\s+){0,6}[\w’'-]+['’]s\s+(?:" + FEELING_WORDS + r")\b")


SELF_REPORT_PREFIX = (r"(?:i\s+(?:feel|felt|am feeling|was feeling|(?:started|began) to feel)|i'm\s+feeling|"
    r"i(?:'ve| have)\s+been feeling)")


def _explicit_self_report(text, *, fragments=False):
    value = _normalize(text).strip(' ,:')
    if _unsafe_source(value):
        return False
    value = _report_head(value)
    if re.search(r"\b(?:do|did|can|could|should|would|will|am|was|have|had)\s+i\b", value):
        return False  # A feeling question is not a factual self-report, even without punctuation.
    # 'I feel Bob is nervous' is an opinion about Bob, unlike 'I feel
    # nervous about Bob'. Keep unfamiliar own-feeling vocabulary supported.
    report = re.search(r"\b" + SELF_REPORT_PREFIX + r"\s+(.+)", value)
    if report and not re.match(r"(?:(?:very|really|quite|slightly|not)\s+)*(?:" + FEELING_WORDS + r")\b", report[1]):
        if re.match(r"(?:(?:that|like)\s+)?(?:[\w']+\s+){1,4}(?:" + SUBJECT_PREDICATE + r")\b|" + POSSESSIVE_FEELING_SUBJECT, report[1]):
            return False
    first_person = re.search(r"\b(?:" + SELF_REPORT_PREFIX + r"(?=\s+\S)|"
        r"my\s+(?:feeling|emotion|mood|confidence)(?:s)?\s*(?:is|was|:|began|started)|"
        r"i(?:'m| am| was)\s+(?:" + FEELING_WORDS + r"))\b", value)
    if first_person:
        return True
    if not fragments:
        return False
    modifiers = r"(?:(?:slightly|very|really|quite|a little|still|not(?: at all| really)?|anything but)\s+)*"
    return bool(re.match(r"(?:(?:felt|feeling)\s+)?" + modifiers + r"(?:" + FEELING_WORDS + r")\b|"
        r"(?:my\s+)?(?:confidence|mood|emotions?|feelings?)\s+"
        r"(?:is|was|were|began|started|kept|became|dwindled|dropped|faded|improved|grew)\b", value))


def _affirmative(text):
    return bool(re.fullmatch(r"(?:yes|yeah|yep|correct|that's right|that is right|please do|"
        r"go ahead)(?:[, ]+(?:please|save it|record it|log it|do it))?[.!]*", _normalize(text or '')))


def _fresh(context, item):
    return bool(item and item['owner'] == tuple(context.owner or ())
        and item['session_id'] == context.session_id
        and timedelta(0) <= _now() - _time(item['recorded_at']) <= PROMPT_TTL)


def _report_clauses(text):
    # Split an explicitly changed subject without stripping owned qualifiers
    # such as 'anything but calm' or 'not really confident'.
    boundary = (r"[.!?;\n]+|(?:,\s*|\b(?:and|but|while|whereas)\s+)(?=(?:[\w’'-]+\s+){1,8}(?:"
        + SUBJECT_PREDICATE + r")\b|" + POSSESSIVE_FEELING_SUBJECT + r")")
    start = 0
    for match in re.finditer(boundary, text, re.I):
        if start < match.start():
            yield start, match.start(), text[start:match.start()]
        start = match.end()
    if start < len(text):
        yield start, len(text), text[start:]


def _note(text):
    match = NOTE_REQUEST.match(text or '')
    if not match or '?' in match[2] or _unsafe_source(text):
        return None
    number, feeling = int(match[1]), match[2].strip()
    if not 0 < number or len(feeling) > MAX_FEELING_CHARS:
        return None
    clauses = [part.strip() for _, _, part in _report_clauses(feeling) if part.strip()]
    if not clauses or not all(_explicit_self_report(part, fragments=True) for part in clauses):
        return None
    return {'trade_number': number, 'feeling': feeling}


def begin_feeling_turn(context, text):
    """Retain at most one explicit note across one delivered clarification.

    Called under the conversation lock. No transcript scanning, global member
    cache, model-supplied provenance, automatic saving, or pending-answer route.
    """
    prior = getattr(context, '_feeling_note', None)
    clarification = getattr(context, '_feeling_clarification', None)
    context._feeling_note = None
    context._feeling_clarification = None
    context._feeling_reply = None
    if (_fresh(context, prior) and clarification
            and clarification['generation'] + 1 == context.generation):
        if _affirmative(text):
            context._feeling_note = {**prior, 'generation': context.generation,
                                     'confirmed_stage': clarification['stage']}
            return
        if text and not _unsafe_source(text):
            context._feeling_reply = {**{key: prior[key] for key in
                    ('owner', 'session_id', 'recorded_at', 'trade_number')},
                    **clarification, 'generation': context.generation}
    note = _note(text)
    if note:
        context._feeling_note = {**note, 'owner': tuple(context.owner or ()),
            'session_id': context.session_id, 'generation': context.generation,
            'recorded_at': _now().isoformat()}


def delivered_feeling_clarification(context, text, generation, response_id):
    """A delivered question supplies stage context, never permission by itself.

    Saving still requires the original member-authored save request AND a new
    affirmative member turn, within the same authenticated conversation.
    """
    context._feeling_clarification = None  # Only the latest delivered question can be answered.
    note = getattr(context, '_feeling_note', None)
    if not _fresh(context, note) or note['generation'] != generation or note.get('confirmed_stage'):
        return
    if SAVE_OPTOUT.search(_normalize(text)) or re.search(r"\b(?:preview|draft|example|hypothetical)\b", text, re.I):
        return
    questions = re.findall(r'[^.!?\n]*\?', text)
    matches = []
    for question in questions:
        if re.search(r"\b(?:not|never|no|don't|preview|draft|example|hypothetical|instead|or)\b", question, re.I):
            continue
        if not re.match(r"^\s*(?:(?:ok(?:ay)?|got it|understood),?\s+)?"
                r"(?:(?:should|shall|may|can|could)\s+i|would you like me to|"
                r"do you want me to|is it (?:ok(?:ay)?|all right) (?:for me )?to)"
                r"\s+(?:record|save|log|add)\b", question, re.I):
            continue
        if re.search(r"\b(?:explain|show|how|wait|later|already|before|after|once|until|"
                r"when|whether|if|also)\b|\band\s+(?:i\s+)?(?:save|record|log|add|close|open|delete)\b", question, re.I):
            continue
        numbers = {int(value) for value in re.findall(TRADE_NUMBER, question, re.I)}
        if numbers != {note['trade_number']} or not re.search(r'\b(?:record|save|log|add)\b', question, re.I):
            continue
        if not re.search(r'\b(?:feeling|note)\b', question, re.I):
            continue
        stage_text = re.sub(r'\badd[ -]entry\b', 'adding', question, flags=re.I)
        stages = {stage for stage, pattern in (
            ('open', r'\b(?:open(?:ing)?|entry)\b'), ('add', r'\badding\b|\bas\s+(?:an?\s+)?add\b'),
            ('mid', r'\b(?:mid[ -]trade|during(?: the)? trade)\b'),
            ('close', r'\b(?:close|closing)\b')) if re.search(pattern, stage_text, re.I)}
        if len(stages) == 1:
            matches.append(next(iter(stages)))
    if len(questions) == len(matches) == 1:
        context._feeling_clarification = {'stage': matches[0], 'generation': generation,
                                          'response_id': response_id}


def _source_phrase(text, feeling, *, fragments=False):
    """Return the original source substring, checking every intersected clause."""
    if not isinstance(feeling, str) or not feeling.strip() or _unsafe_source(text):
        return None
    pattern = r'\s+'.join(re.escape(word).replace("'", "['’]") for word in feeling.strip().split())
    for match in re.finditer(pattern, text, re.I):
        if (match.start() and text[match.start()-1].isalnum()
                or match.end() < len(text) and text[match.end()].isalnum()):
            continue
        accepted = True
        checked = False
        for clause_start, clause_end, part in _report_clauses(text):
            if clause_end <= match.start() or clause_start >= match.end():
                continue
            checked = True
            # Interrogative punctuation belongs to this source clause, not to
            # a separate factual report elsewhere in the same utterance.
            if text[clause_end:clause_end+1] == '?':
                accepted = False
                break
            # A phrase solely inside a causal/context tail may describe another
            # person or a hypothetical outcome. It cannot become our report.
            if match.start() >= clause_start + len(_report_head(part)):
                accepted = False
                break
            note_match = NOTE_REQUEST.match(part)
            if note_match:
                part = note_match[2]
            # Do not turn "not calm" or "less confident" into its opposite by
            # extracting only the adjective. Preserve the member's modifiers.
            prefix = text[clause_start:match.start()] if clause_start < match.start() else ''
            if (not _explicit_self_report(part, fragments=fragments)
                    or re.search(r"\b(?:not|never|less|slightly|barely|hardly|scarcely|no longer|anything but|nowhere near|opposite of|reverse of|far from)(?:\s+(?!(?:and|but|yet|however)\b)[\w’'-]+)*\s*$", prefix, re.I)):
                accepted = False
                break
        if accepted and checked and re.search(r'\w', match[0]):
            return match[0]
    return None


def _short_answer(text):
    value = _normalize(text).strip(' .!')
    if len(value) > MAX_FEELING_CHARS or '?' in value or _unsafe_source(value):
        return False
    if re.search(r'\b(?:watch|alert|monitor|notify|remind|send|show|buy|sell|stop|target|price|btc|nas100|journal|chart|recap|review|shift|hello|thanks)\b', value):
        return False
    return bool(_source_phrase(text, text, fragments=True))


def _stated_stages(text):
    """Only plainly named stages; multiple stages need the member's choice."""
    return {stage for stage, pattern in (
        ('open', r'\b(?:at|on|with|during|before)\s+(?:[\w]+\s+){0,4}(?:entry|opening)\b|\bas i opened\b'),
        ('add', r'\b(?:at|on|during|while)\s+(?:[\w]+\s+){0,4}(?:add|adding)\b|\bas i added\b'),
        ('mid', r'\b(?:mid[ -]trade|during (?:the|my) trade)\b|\bafter\s+(?:[\w]+\s+){0,4}(?:entry|add|adding)\b'),
        ('close', r'\b(?:at|on|during|after)\s+(?:the |my )?(?:close|closing|exit)\b|\bas i closed\b'))
        if re.search(pattern, text, re.I)}


def _requested_note_stage(text):
    # Inspect raw source before clause splitting can remove its separator.
    note = NOTE_REQUEST.match(text)
    wrapper = re.search(r'\b(' + NOTE_STAGE + r')\s+(?:feeling|note)\b',
                        text[:note.start(2)], re.I) if note else None
    if wrapper:
        label = wrapper[1].lower().replace(' ', '-')
        return {'opening': 'open', 'entry': 'open', 'adding': 'add',
                'add-entry': 'add', 'mid-trade': 'mid', 'closing': 'close'}[label]
    return None


def _ground_fragment_fields(args, text, *, confirmed_stage=None, affirmative=False):
    owned_text = ' '.join(_report_head(part) for _, _, part in _report_clauses(text))
    stages = _stated_stages(owned_text)
    requested_stage = _requested_note_stage(text)
    if requested_stage:
        stages.add(requested_stage)
    expected = (confirmed_stage if affirmative else next(iter(stages)) if len(stages) == 1
                else confirmed_stage if not stages else None)
    if expected is None or args.get('stage') != expected:
        raise ValueError('Which stage should this note use: opening, adding, mid-trade or closing? '
                         'Ask one save question naming the Trade # and proposed stage; keep the original words.')
    if args.get('reported_at') is not None:
        _time(args['reported_at'])
        if args['reported_at'] not in owned_text:
            raise ValueError('Keep the feeling time unknown unless the member stated that exact date and timezone.')
    if args.get('correction_of') is not None:
        raise ValueError('A feeling note does not authorize correcting an earlier report. Clarify the correction separately.')


def _ground_report(args, guild, user):
    capability = binding(args)
    if capability is None:
        raise ValueError('Feelings must come from the current authenticated conversation.')
    with capability.guard(guild, user):
        context = capability.context
        text = getattr(context, '_client_text', None)
        if text is None:
            return  # Existing audio model classification; no fabricated ASR.
        if args.get('skip'):
            if not re.search(r"\b(?:skip|pass|prefer not|rather not|don't (?:ask|want)|do not (?:ask|want)|no thanks)\b", _normalize(text)):
                raise ValueError('Only skip a feeling check when the member explicitly declines.')
            return
        note = getattr(context, '_feeling_note', None)
        if (_fresh(context, note) and note['generation'] == capability.generation
                and note.get('confirmed_stage') and _affirmative(text)):
            if (args.get('trade_number') != note['trade_number'] or args.get('stage') != note['confirmed_stage']
                    or _normalize(args.get('feeling')) != _normalize(note['feeling'])):
                raise ValueError('Save only the exact original note for the confirmed Trade # and stage; keep unstated time and correction unknown.')
            _ground_fragment_fields(args, note['feeling'], confirmed_stage=note['confirmed_stage'], affirmative=True)
            args['feeling'] = note['feeling']
            return
        numbers = {int(value) for value in re.findall(TRADE_NUMBER, text, re.I)}
        if numbers and numbers != {args.get('trade_number')}:
            raise ValueError('Use only the Trade # identified by this member, or clarify which trade.')
        prompt = getattr(context, '_feeling_prompt', None)
        prompted = bool(prompt and prompt['trade_number'] == args.get('trade_number')
            and prompt['generation'] + 1 == capability.generation
            and timedelta(0) <= _now() - _time(prompt['recorded_at']) <= PROMPT_TTL)
        reply = getattr(context, '_feeling_reply', None)
        clarified = bool(_fresh(context, reply) and reply['generation'] == capability.generation
            and reply['trade_number'] == args.get('trade_number'))
        explicit_note = _note(text)
        fragments = bool(explicit_note or (prompted or clarified) and _short_answer(text))
        phrase = _source_phrase(text, args.get('feeling'), fragments=fragments)
        if phrase is not None:
            direct = _source_phrase(text, args.get('feeling'), fragments=False)
            if explicit_note or direct is None:
                full_report = explicit_note['feeling'] if explicit_note else text.strip()
                if _normalize(phrase) != _normalize(full_report):
                    raise ValueError('Preserve the full original feeling note, including its qualifiers and changing confidence; do not extract an adjective.')
                # A stage-qualified save request supplies its own stage, but
                # never overrides a conflicting stage in the member's report.
                _ground_fragment_fields(args, text if explicit_note else full_report,
                    confirmed_stage=prompt['stage'] if prompted else reply['stage'] if clarified else None)
                phrase = full_report
            args['feeling'] = phrase
            return
        raise ValueError('No member-reported feeling was identified for this trade. Preserve their words, including natural fragments; clarify only the missing trade or stage. Do not infer or save quoted, hypothetical or preview text.')


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


def _consume_note(args):
    capability = binding(args)
    with capability.context._lock:
        if capability.context.current(capability.generation):
            capability.context._feeling_note = None
            capability.context._feeling_clarification = None
            capability.context._feeling_reply = None


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
                _consume_note(args)
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
    _consume_note(args)
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
    if text and not _unsafe_source(text) and any(_explicit_self_report(part, fragments=True)
            and text[end:end+1] != '?' for _, end, part in _report_clauses(text)):
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
