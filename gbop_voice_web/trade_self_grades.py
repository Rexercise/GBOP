"""Optional, member-reported SELF grades on the existing canonical journal.

No outcome-based psychology, new schema, executions, or risk mutations. The
current bounded assessment lives in journal metadata; existing journal audit
events retain every prior assessment, including corrections and clearing.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math

from gbop_voice_web.journal_context import journal_transaction
from gbop_voice_web.journal_numbers import resolve_journal_selector
from gbop_voice_web.trade_photos import schema, NUM
from gbop_voice_web.unified_journal import ensure_canonical_journal, owned_journal_details

GRADES = ('type1', 'type2', 'type3', 'type4')
ADHERENCE = ('followed_plan', 'off_plan', 'unknown')
OUTCOMES = ('profit', 'loss', 'breakeven', 'unknown')
REASONS = ('impulsive', 'fomo', 'revenge', 'other', 'unknown')
MAX_NOTE = 500

SELF_GRADE_PROMPT = """
SELF grades are optional member self-assessments, not GBOP scores or skill labels.
Use record_trade_self_grade only for the member's explicit assessment of a known
Trade #. Type 1: followed the plan and made a profit. Type 2: followed the plan and
lost at the PREDEFINED stop. Type 3: off-plan and lost. Type 4: off-plan and profited.
Impulsive/FOMO/revenge reasons are member-reported only; off-plan does not itself
establish any particular emotion. Validate the chosen type against explicitly
reported adherence/outcome and, for Type 2, an explicitly predefined stop.
Do not derive adherence, emotion, a stop, or a grade from profit/loss. A profit
alone does not establish skill. Breakeven, unknown outcome, and an in-plan loss
exited manually stay ungraded. Never fill blanks or force one of the four types.
A SELF-grade save replaces the current assessment: include only stated facts,
leave unknowns null, and use clear_grade to clear a mistaken assessment. Preserve
an optional note in the member's own words. If clarification is needed ask only
one short question, without holding up the actual trade journal or close.
Use the single combined optional close check-in; do not ask a second grade prompt.
""".strip()


def _enum(values):
    return {'type': ['string', 'null'], 'enum': [*values, None]}


SELF_GRADE_TOOLS = [schema('record_trade_self_grade',
    'Save or correct an optional member SELF grade on their existing Trade #. '
    'Type grades require CLOSED/JOURNALED; never close a trade merely to grade it. '
    'Full replacement, never auto-grade: type1=followed_plan+profit; '
    'type2=followed_plan+loss at explicitly predefined_stop=true; '
    'type3=off_plan+loss; type4=off_plan+profit. Supply the member\'s grade and '
    'explicit adherence/outcome; unknown/breakeven/in-plan manual-exit losses remain '
    'ungraded. Off-plan reasons and note (max 500 characters) must be member-reported. '
    'Null means unknown, not a default or evidence from the previous grade. '
    'clear_grade=true removes the current assessment while retaining audit history.',
    {'trade_number': NUM, 'grade': _enum(GRADES), 'adherence': _enum(ADHERENCE),
     'outcome': _enum(OUTCOMES), 'predefined_stop': {'type': ['boolean', 'null']},
     'off_plan_reason': _enum(REASONS), 'note': {'type': ['string', 'null'], 'maxLength': MAX_NOTE},
     'clear_grade': {'type': ['boolean', 'null']}})]


def _assessment(args):
    """Validate a complete explicit assessment, never infer the member's grade."""
    grade = args.get('grade')
    adherence = args.get('adherence') or 'unknown'
    outcome = args.get('outcome') or 'unknown'
    stop = args.get('predefined_stop')
    reason = args.get('off_plan_reason')
    note = args.get('note')
    if grade not in (*GRADES, None):
        raise ValueError('SELF grade must be type1, type2, type3, type4, or null.')
    if args.get('adherence') not in (*ADHERENCE, None) or args.get('outcome') not in (*OUTCOMES, None):
        raise ValueError('Use explicit plan adherence and profit/loss/breakeven/unknown outcome.')
    if stop is not None and type(stop) is not bool:
        raise ValueError('predefined_stop must be true, false, or null; do not infer it from a loss.')
    if reason not in (*REASONS, None):
        raise ValueError('Use a member-reported off_plan_reason or leave it unknown.')
    if note is not None and (not isinstance(note, str) or not note.strip() or len(note) > MAX_NOTE):
        raise ValueError('SELF-grade note must be nonblank text of at most 500 characters, or null.')
    if reason not in (None, 'unknown') and adherence != 'off_plan':
        raise ValueError('An off-plan reason needs explicitly reported off-plan adherence.')
    if grade is not None:
        expected = {'type1': ('followed_plan', 'profit'), 'type2': ('followed_plan', 'loss'),
                    'type3': ('off_plan', 'loss'), 'type4': ('off_plan', 'profit')}[grade]
        if (adherence, outcome) != expected:
            raise ValueError('That SELF grade needs matching member-reported adherence and outcome. '
                             'Which plan adherence and outcome did you mean? Leave it ungraded if unsure.')
        if grade == 'type2' and stop is not True:
            raise ValueError('Type 2 requires a loss at the predefined stop. Was that the exit? '
                             'A manual-exit loss or unknown stop stays ungraded.')
    if grade is None and adherence == outcome == 'unknown' and stop is None and reason in (None, 'unknown') and note is None:
        raise ValueError('No SELF assessment was provided. Leave optional blanks unsaved.')
    return {'version': 1, 'type': grade, 'member_reported': True, 'source': 'member_reported',
            'adherence': adherence, 'outcome': outcome, 'predefined_stop': stop,
            'off_plan_reason': reason, 'note': note.strip() if note else None}


def self_grade_summary(metadata, result_r=None):
    """Bounded read API; malformed/untrusted legacy objects cannot count as grades.

    Returns None when no supported member assessment is stored. The returned
    ``type`` is None for an explicitly ungraded assessment. Does not use R,
    emotions, legacy labels, or free prose to classify an outcome or adherence.
    """
    value = metadata.get('self_grade') if isinstance(metadata, dict) else None
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('member_reported') is not True or value.get('source') != 'member_reported':
        return None
    try:
        result = _assessment({**value, 'grade': value.get('type')})
    except (ValueError, TypeError):
        return None
    recorded = value.get('recorded_at')
    if isinstance(recorded, str) and len(recorded) <= 64:
        result['recorded_at'] = recorded
    conflict = _conflicting_facts(metadata, {'result_r': result_r}, result)
    if conflict and result['type'] is not None:
        result.update(recorded_type=result['type'], type=None, needs_clarification=True, clarification=conflict)
    return result


def self_grade_counts(rows):
    """Aggregate already member-scoped, deduplicated trade rows (no DB access)."""
    counts = {grade: 0 for grade in GRADES}
    assessed = 0
    for row in rows:
        assessment = self_grade_summary(row.get('metadata'), row.get('result_r'))
        if assessment is not None:
            assessed += 1
            if assessment['type'] is not None:
                counts[assessment['type']] += 1
    graded = sum(counts.values())
    return {'entries': len(rows), 'member_assessed': assessed, 'graded': graded,
            'ungraded': len(rows) - graded, 'counts': counts,
            'basis': 'Explicit member SELF assessments only; no inferred grade or skill score.'}


def _conflicting_facts(metadata, journal, assessment):
    """Use existing explicit facts only to flag conflict, never to complete blanks."""
    outcome = assessment['outcome']
    reported = metadata.get('reported_outcome')
    recorded = {'win': 'profit', 'loss': 'loss', 'stopped_out': 'loss', 'breakeven': 'breakeven'}.get(reported)
    if outcome != 'unknown' and recorded and recorded != outcome:
        return 'The journal outcome and SELF assessment disagree. Which outcome should be corrected?'
    result = journal['result_r'] if journal else None
    if outcome != 'unknown' and result is not None:
        try:
            number = float(result)
        except (ValueError, TypeError):
            number = math.nan
        if math.isfinite(number):
            recorded = 'profit' if number > 0 else 'loss' if number < 0 else 'breakeven'
            if recorded != outcome:
                return 'The saved R result and SELF outcome disagree. Which should be corrected?'
    if outcome not in ('unknown',) and reported == 'open':
        return 'The journal still reports an open outcome. Is this the final result or is the trade still open?'
    adherence = metadata.get('adherence')
    if adherence == 'partial' and assessment['adherence'] == 'followed_plan':
        return 'The journal reports partial adherence. Did you follow the full plan for this assessment?'
    expected = {'yes': 'followed_plan', 'no': 'off_plan'}.get(adherence)
    if assessment['adherence'] != 'unknown' and expected and assessment['adherence'] != expected:
        return 'The journal adherence and SELF assessment disagree. Which adherence should be corrected?'
    return None


def record_self_grade(db, guild, user, args):
    allowed = {'trade_number', 'grade', 'adherence', 'outcome', 'predefined_stop',
               'off_plan_reason', 'note', 'clear_grade', '_journal_binding', 'market_reference'}
    if set(args) - allowed:
        raise ValueError('Use the displayed Trade # and documented SELF-grade fields only.')
    if args.get('clear_grade') is not None and type(args['clear_grade']) is not bool:
        raise ValueError('clear_grade must be true, false, or null.')
    clear = args.get('clear_grade') is True
    if clear and any(args.get(key) is not None for key in ('grade', 'adherence', 'outcome', 'predefined_stop', 'off_plan_reason', 'note')):
        raise ValueError('Clear the assessment or supply a replacement; do not do both.')
    try:
        assessment = None if clear else _assessment(args)
    except (ValueError, TypeError) as exc:
        return {'ok': False, 'saved': False, 'status': 'self_grade_clarification', 'error': str(exc)}
    from gbop_voice_web.journal_coach import init_coach
    init_coach(db)
    with journal_transaction(db, args, guild, user, serialize=True) as conn:
        target = resolve_journal_selector(conn, guild, user, trade_number=args.get('trade_number'))
        if not target['ok']:
            return target
        thesis_id = target['thesis_id']
        thesis = conn.execute('SELECT * FROM theses WHERE id=? AND guild_id=? AND user_id=?',
                              (thesis_id, guild, user)).fetchone()
        journal = conn.execute('SELECT * FROM journals WHERE id=? AND guild_id=? AND user_id=?',
                               (target['record_id'], guild, user)).fetchone() if target['record_id'] else None
        if journal is None and conn.execute(
                'SELECT id FROM journals WHERE thesis_id=? AND guild_id=? AND user_id=? LIMIT 1',
                (thesis_id, guild, user)).fetchone():
            return {'ok': False, 'saved': False, 'status': 'legacy_history_preserved',
                    'error': 'This trade has unresolved historical journals. Correct its unified journal before adding a SELF grade.'}
        details = owned_journal_details(conn, guild, user, journal['id']) if journal else None
        metadata = json.loads(details['metadata'] or '{}') if details else {}
        if not isinstance(metadata, dict):
            raise ValueError('The journal metadata needs review before a SELF grade can be saved.')
        if thesis['status'] == 'IDEA' or metadata.get('kind') in ('study', 'reflection'):
            return {'ok': False, 'saved': False, 'error': 'SELF grades apply only to real member trades, not studies or reflections.'}
        if assessment is not None and assessment['type'] is not None and thesis['status'] not in ('CLOSED', 'JOURNALED'):
            return {'ok': False, 'saved': False, 'status': 'self_grade_trade_not_closed',
                    'error': 'This trade is still open. Save the actual close before adding an end-result SELF grade; an ungraded note is optional.'}
        if assessment is not None:
            conflict = _conflicting_facts(metadata, journal, assessment)
            if conflict:
                return {'ok': False, 'saved': False, 'status': 'self_grade_clarification', 'error': conflict}
        previous = metadata.get('self_grade')
        comparable = {k: v for k, v in previous.items() if k != 'recorded_at'} if isinstance(previous, dict) else previous
        changed = comparable != assessment
        if changed:
            if assessment is not None:
                assessment['recorded_at'] = datetime.now(timezone.utc).isoformat()
            ensure_canonical_journal(conn, guild, user, thesis_id, metadata={'self_grade': assessment})
        else:
            assessment = deepcopy(previous)
        return {'ok': True, 'saved': True, 'updated': changed, 'trade_number': target['trade_number'],
                'journal_number': target['journal_number'], 'self_grade': assessment,
                'status': 'self_grade_cleared' if clear else 'self_grade_recorded' if assessment['type'] else 'self_grade_ungraded',
                'history': 'Earlier assessments and corrections remain in the private journal audit.'}
