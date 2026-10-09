"""Confirmed, recoverable removal of an owned unfinished journal from active use.

No canonical journal, trade, execution or schema is changed. Confirmation is a
server-owned receipt of a delivered preview and a later, immediate member turn.
"""
import re
import time
import unicodedata

from gbop_voice_web import journal_drafts
from gbop_voice_web.journal_context import JournalBinding, journal_transaction
from gbop_voice_web.trade_photos import schema, STR

DISCARD_NAMES = {'prepare_journal_discard', 'discard_journal_story', 'restore_journal_story'}
PREVIEW_TTL = 300
DISCARD_PROMPT = """
UNFINISHED DRAFT DISCARD
For delete/discard/remove an unfinished draft, use prepare_journal_discard with
its exact draft_id. If several drafts could be meant, list titles and ask which.
YOU say the returned confirmation_prompt and wait for the member's next reply.
Natural assent or a clear discard request confirms that selected draft; never
require a magic phrase, repeated question, or spoken draft ID. Only then call
discard_journal_story. For voice quote their actual reply
in confirmation_text; typed requests are checked directly by the server. Never
stage deletion commands as narration, finalize, or use delete_journal/delete_trade
for this request. Cancel/no/unrelated replies leave the draft unchanged. Discard
archives only this unlinked unfinished draft, retaining narration for recovery.
Archived drafts are excluded from normal history, prompts and resumption. To undo,
get_journal_story view=discarded lists titles/revisions; restore_journal_story only
on an explicit restore request. Do not recreate an archived draft from chat memory.
"""


def _context(args, guild, user):
    from gbop_voice_web.journal_story import _context
    return _context(args, guild, user)


def _source(context, args):
    # Model text can classify an audio-only reply, never replace actual typed text.
    value = getattr(context, '_client_text', None)
    if value is None:
        value = args.get('confirmation_text')
    return value if isinstance(value, str) and len(value) <= 2500 else ''


def _normalized(text):
    return ' '.join(str(text or '').casefold().replace('’', "'").split()).strip(' .!')


# Whole-utterance grammar, not a magic phrase or a model-supplied approval flag.
# Every reference below means the one server-bound, delivered draft preview.
_DISCARD_TARGET = (r"(?:it|this|that|(?:this|that|the|my)(?: selected)?"
    r"(?: unfinished)? draft|(?:this|that|the|my) unfinished journal(?: draft)?"
    r"|(?:this|that|the)(?: unfinished)? one|(?:unfinished )?draft|unfinished journal(?: draft)?)")
_DISCARD_ACTION = r"(?:discard|delete|remove|archive|trash|scrap|bin|toss|throw away|get rid of) " + _DISCARD_TARGET
_DISCARD_POLITE = r"(?: please| thanks| thank you| for me| now| then){0,3}"
_DISCARD_INTENT = (r"(?:(?:i want|i would like|i'd like) (?:you )?to |you can |let's )?")
_DISCARD_COMMAND = (r"(?:please )?(?:just )?(?:(?:go ahead|proceed) and )?"
    + _DISCARD_INTENT + r"(?:please )?(?:just )?" + _DISCARD_ACTION + _DISCARD_POLITE)
_DISCARD_PASSIVE = (r"(?:i want|i would like|i'd like) " + _DISCARD_TARGET
    + r" (?:discarded|deleted|removed|archived|gone)" + _DISCARD_POLITE)
_DISCARD_ASSENT = (r"(?:please )?(?:yes|yeah|yep|yup|sure(?: thing)?|okay|ok|alright|all right|fine"
    r"|absolutely|definitely|of course|sounds good|all good|that's (?:fine|right)|that is (?:fine|right)"
    r"|confirm|confirmed|i confirm|please do|do it|go ahead|proceed)" + _DISCARD_POLITE)
_DISCARD_ACTION_CLAUSE = r"(?:" + _DISCARD_COMMAND + r"|" + _DISCARD_PASSIVE + r")"
_DISCARD_ASSENT_CHAIN = _DISCARD_ASSENT + r"(?: (?:and |so )?" + _DISCARD_ASSENT + r"){0,2}"
# At most one action clause: 'remove this and delete that' may name two targets.
_DISCARD_CONFIRMATION = re.compile(r"(?:um |uh |well )?(?:"
    + _DISCARD_ASSENT_CHAIN + r"|(?:" + _DISCARD_ASSENT_CHAIN + r" (?:and |so )?)?"
    + _DISCARD_ACTION_CLAUSE + r"(?: (?:and |so )?" + _DISCARD_ASSENT_CHAIN + r")?)")


def _confirm(text):
    """Accept natural assent only within the separate exact-receipt guard.

    Full-string composition permits ordinary politeness and first-person intent,
    without extracting a stray 'yes' from a refusal, quotation or hypothetical.
    Unknown targets, third-person claims and wider deletion scope fail closed.
    """
    value = _normalized(text)
    if len(value) > 600 or re.search(r'[?"“”]', value):
        return False
    value = ' '.join(re.sub(r"[,;.!–—]+", " ", value).split())
    return bool(_DISCARD_CONFIRMATION.fullmatch(value))


def _cancel_discard(text):
    value = ' '.join(re.sub(r"[,;.!–—]+", " ", _normalized(text)).split())
    cancel = (r"(?:cancel(?: it| that)?|keep " + _DISCARD_TARGET
        + r"|wait|stop|never mind|nevermind|not now|not yet|leave " + _DISCARD_TARGET
        + r" (?:alone|unchanged)|(?:do not|don't|dont|never) (?:discard|delete|remove|archive|trash|get rid of) "
        + _DISCARD_TARGET + r")")
    clause = r"(?:no(?: thanks| thank you)?|(?:please )?" + cancel + r")(?: please)?"
    return bool(re.fullmatch(clause + r"(?: (?:and )?" + clause + r"){0,2}", value))


def _prompt_words(text):
    # Audio transcripts can omit typographic quotes/parentheses or change their
    # style. Compare the same complete words, never a generated approval flag.
    return ' '.join(re.findall(r'\w+', unicodedata.normalize('NFKC', str(text or '')).casefold()))


def _restore_request(text, draft_id=None):
    value = _normalized(text)
    if re.search(r"[?\"“”]|\b(?:not|don't|never|unless|if|maybe|example|pretend|hypothetical|instead|except)\b", value):
        return False
    target = bool(re.search(r'\b(?:draft|journal)\b',value) or isinstance(draft_id,str) and re.fullmatch(r'[a-f0-9]{32}',draft_id) and draft_id in value)
    return bool(target and re.fullmatch(r"(?:please )?(?:restore|recover|bring back) (?:[^\n]{1,300})|(?:please )?undo (?:the )?(?:discard|deletion)(?: of (?:this|that|the) (?:draft|journal))?", value))


def is_discard_request(text, context=None):
    """Keep lifecycle requests and receipt-bound assent out of narration."""
    if not text:
        return False
    cancellation = getattr(context, '_journal_discard_cancel_reply', None)
    if context is not None and cancellation == (context.generation, _normalized(text)):
        return True
    item = getattr(context, '_journal_discard_preview', None)
    pending_reply = False
    if context is not None and item:
        try:
            _pending(context, item['draft_id'])
            pending_reply = True
        except ValueError:
            pass
    affirmative = _confirm(text)
    lifecycle = bool(re.search(r'\b(?:discard|delete|remove|archive|restore|recover)\b.*\b(?:draft|unfinished journal)\b|\b(?:draft|unfinished journal)\b.*\b(?:discard|delete|remove|archive)\b|\b(?:discard|delete|remove|archive) it\b|\bundo\b.*\bdiscard', text, re.I))
    natural_action = affirmative and bool(re.search(r'\b(?:discard|delete|remove|archive|trash|scrap|bin|toss|throw away|get rid of|discarded|deleted|removed|archived|gone)\b', text, re.I))
    guarded = bool(lifecycle or natural_action or pending_reply and (affirmative or _cancel_discard(text)))
    if guarded and pending_reply and not affirmative:
        # Any refused lifecycle source (negative, quoted, hypothetical, etc.)
        # consumes the receipt, so a model retry cannot substitute a later yes.
        with context._lock:
            if getattr(context, '_journal_discard_preview', None) is item:
                context._journal_discard_preview = None
                context._journal_discard_cancel_reply = (context.generation, _normalized(text))
    return guarded


def _summary(draft):
    values = draft.get('values') or {}
    return {'draft_id': draft['id'], 'title': str(values.get('title') or 'Unfinished journal')[:160],
            'asset': str(values.get('asset') or '')[:32] or None, 'trade_date': str(values.get('trade_date') or '')[:10] or None,
            'created_at': draft.get('created_at'), 'storage_revision': draft['storage_revision'],
            'draft_status': draft['draft_status']}


def discarded_stories(conn, guild, user, draft_id=None, offset=0):
    if type(offset) is not int or offset < 0:
        raise ValueError('Use a nonnegative whole-number offset for discarded drafts.')
    if draft_id is not None:
        rows = [d for d in journal_drafts.read(conn, guild, user, draft_id, include_discarded=True)
                if d['draft_status'] == 'discarded']
        next_offset, has_more, capped = None, False, False
    else:
        page = journal_drafts.read_discarded_page(conn, guild, user, offset=offset, limit=10)
        rows, next_offset, has_more = page['drafts'], page['next_offset'], page['has_more']
        capped = page.get('pagination_limit_reached',False)
    return {'ok': True, 'status': 'discarded_drafts', 'drafts': [_summary(d) for d in rows],
            'next_offset': next_offset, 'has_more': has_more, 'pagination_limit_reached': capped,
            'instruction': 'At the paging limit, use an exact draft_id; never restart the listing. Otherwise these archived drafts are not active or finalized. Page with next_offset if has_more. Restore only the explicitly selected draft on a current member request.'}


def _pending(context, draft_id, *, require_reply=True):
    item = getattr(context, '_journal_discard_preview', None)
    if (not item or item['draft_id'] != draft_id
            or item['owner'] != (*context.owner[:2], context.session_id)
            or item['expires_at'] <= time.monotonic()
            or require_reply and (not item['delivered'] or context.generation != item['generation'] + 1)):
        raise ValueError('Preview this unfinished draft, deliver the confirmation question, and confirm in the next member turn before discarding it.')
    return item


def begin_discard_turn(context, text):
    with context._lock:
        item = getattr(context, '_journal_discard_preview', None)
        context._journal_discard_cancel_reply = None
        if (item and item['delivered'] and context.generation == item['generation'] + 1
                and text is not None and _cancel_discard(text)):
            context._journal_discard_cancel_reply = (context.generation, _normalized(text))
        if item and (item['expires_at'] <= time.monotonic()
                or context.generation > item['generation'] + 1
                or context.generation == item['generation'] + 1 and text is not None and not _confirm(text)):
            context._journal_discard_preview = None


def delivered_discard_preview(context, text, generation, response_id):
    with context._lock:
        item = getattr(context, '_journal_discard_preview', None)
        if (context.current(generation) and item and item['generation'] == generation
                and item['owner'] == (*context.owner[:2], context.session_id)
                and item['expires_at'] > time.monotonic()
                and ' '+_prompt_words(item['prompt'])+' ' in ' '+_prompt_words(text)+' '):
            item['delivered'] = True
            item['response_id'] = response_id


def _clear_active(context, draft_id):
    with context._lock:
        active_id = (getattr(context, '_journal_story', None) or {}).get('id')
        if active_id == draft_id:
            context._journal_story = None
            context._journal_audio_intent = {}
            context._journal_capture_error = None
        context._journal_results = {}
        context._recall_binding = None
        if active_id is not None and active_id != draft_id:return
        context._journal_blocked_draft_id = draft_id
        context._journal_discard_notice = ('The selected unfinished draft was discarded and archived. '
            'Do not recall, resume, finalize, or recreate its old narration. Restore only on an explicit member request.')


def prepare_discard(db, guild, user, args):
    capability, context = _context(args, guild, user)
    with journal_transaction(db, args, guild, user, serialize=True) as conn:
        requested = args.get('draft_id') or (getattr(context, '_journal_story', None) or {}).get('id')
        rows = journal_drafts.read(conn, guild, user, requested)
        if not rows:
            raise ValueError('No active saved unfinished draft was found. Nothing was discarded.')
        if len(rows) > 1:
            return {'ok': True, 'status': 'draft_selection_required', 'drafts': [_summary(d) for d in rows[:10]],
                    'has_more': len(rows)>10, 'next_offset': 10 if len(rows)>10 else None,
                    'instruction': 'Ask which exact unfinished draft to discard; do not pick the newest. More titles: get_journal_story view=unfinished with next_offset as offset.'}
        draft = rows[0]
        # A read-only eligibility check must match the storage mutation boundary.
        journal_drafts.ensure_discardable(conn, guild, user, draft)
        title = ' '.join(str(_summary(draft)['title']).split())[:100]
        prompt = (f'Discard unfinished draft "{title}"? '
                  'It will be removed from active journals and can be restored. Saved journals and trades will stay unchanged.')
        previous = getattr(context, '_journal_discard_preview', None)
        if (previous and previous['generation'] == capability.generation
                and (previous['draft_id'] != draft['id']
                     or previous['revision'] != draft['storage_revision'])):
            # A spoken title need not be unique. Never let an earlier question's
            # delivery authorize a replacement target/revision in the same turn.
            raise ValueError('A different draft or revision was already previewed in this turn. Clarify the intended draft, then prepare it in a new member turn. Nothing changed.')
        if (previous and previous['draft_id']==draft['id']
                and previous['revision']==draft['storage_revision']
                and previous['owner']==(guild,user,context.session_id)
                and previous['expires_at']>time.monotonic()
                and (capability.generation == previous['generation']
                     or previous['delivered'] and capability.generation == previous['generation']+1)):
            # A model retry or a redundant prepare on the confirmation turn must
            # not erase the delivered receipt or turn the same yes into a new ask.
            if previous['delivered']:
                return {'ok': True, 'status': 'draft_discard_confirmation_already_asked',
                        'requires_confirmation': True, 'confirmation_already_delivered': True,
                        'draft': _summary(draft),
                        'instruction': 'This exact unchanged draft was already previewed. Do not ask again. If the current member reply explicitly confirms, call discard_journal_story with the actual reply; otherwise leave it unchanged.'}
            return {'ok': True, 'status': 'draft_discard_confirmation_required', 'requires_confirmation': True,
                    'draft': _summary(draft), 'confirmation_prompt': previous['prompt'],
                    'instruction': 'The existing preview is unchanged. Deliver this question once, then wait for the member reply. Nothing changed.'}
        context._journal_discard_preview = {'draft_id': draft['id'], 'revision': draft['storage_revision'],
            'owner': (guild, user, context.session_id), 'generation': capability.generation,
            'expires_at': time.monotonic() + PREVIEW_TTL, 'delivered': False, 'prompt': prompt}
        return {'ok': True, 'status': 'draft_discard_confirmation_required', 'requires_confirmation': True,
                'draft': _summary(draft), 'confirmation_prompt': prompt,
                'instruction': 'YOU say confirmation_prompt, then wait for one natural confirmation. The member need not repeat any phrase or draft ID. Use the same draft_id and quote their actual reply. Nothing changed.'}


def _result(action, draft):
    discarded = action == 'discard'
    return {'ok': True, 'status': 'draft_discarded' if discarded else 'draft_restored',
            'discarded' if discarded else 'restored': True, 'persisted': True,
            'draft_id': draft['id'], 'draft_status': 'discarded' if discarded else 'unfinished',
            'revision': draft['revision'], 'restorable': discarded,
            'journal_records_changed': False, 'execution_records_changed': False,
            'instruction': ('Draft discarded and archived. It can be restored on request. Do not resume it or recreate it from memory.'
                if discarded else 'Unfinished draft restored with original narration. Read get_journal_story before continuing; restoration does not finalize or resume recording.')}


def _recovery_reader(db, guild, user, context, draft_id, expected, action):
    """Read only the exact transition after its original writer has settled."""
    session, auth = context.session_id, context._auth_revision
    def reconcile():
        from gbop_voice_web.journal_finalization import _bounded_connection
        with context._lock:
            if context.closed or context.session_id != session or context._auth_revision != auth:
                return None
            current = JournalBinding(context, context.generation)
        with journal_transaction(lambda: _bounded_connection(db), {'_journal_binding': current}, guild, user, serialize=True) as conn:
            if context.session_id != session or context._auth_revision != auth:
                return None
            rows = journal_drafts.read(conn, guild, user, draft_id, include_discarded=True)
            if len(rows) != 1:return None
            draft = rows[0]
            last = (draft.get('_discard_history') or [{}])[-1]
            if (draft['storage_revision'] == expected + 1 and last.get('action') == action
                    and last.get('from_revision') == expected and last.get('to_revision') == expected + 1
                    and draft['draft_status'] == ('discarded' if action == 'discard' else 'unfinished')):
                return {**_result(action, draft), 'reconciliation_verified': True}
            if (draft['storage_revision'] == expected
                    and draft['draft_status'] == ('unfinished' if action == 'discard' else 'discarded')):
                return {'ok': False, 'saved': False, 'status': 'journal_not_saved',
                        'draft_id': draft_id, 'draft_status': draft['draft_status'],
                        'reconciliation_verified': True, 'error': 'The draft state change did not commit. Read its current state before a new request.'}
            return None
    from gbop_voice_web.voice_runtime import journal_write_recovery_ready
    journal_write_recovery_ready(guild, user, reconcile)
    return reconcile


def _publish(context, capability, guild, user, result, action):
    from gbop_voice_web.voice_runtime import journal_write_committed
    journal_write_committed(guild, user, result)
    with context._lock:
        if context.current(capability.generation):
            if action == 'discard':
                _clear_active(context, result['draft_id'])
            else:
                context._journal_discard_preview = None
                context._journal_discard_notice = None
                context._journal_blocked_draft_id = None
                context._recall_binding = None
                context._journal_results = {}
    return result


def discard_story(db, guild, user, args):
    capability, context = _context(args, guild, user)
    draft_id = args.get('draft_id')
    reader = None
    try:
        with journal_transaction(db, args, guild, user, serialize=True) as conn:
            pending = _pending(context, draft_id)
            source = _source(context, args)
            if not _confirm(source):
                # A later tool retry cannot replace a rejected current reply
                # with a model-generated yes, including audio-only cancellation.
                with context._lock:
                    if getattr(context, '_journal_discard_preview', None) is pending:
                        context._journal_discard_preview = None
                        context._journal_discard_cancel_reply = (context.generation, _normalized(source))
                raise ValueError('No clear confirmation of the selected draft discard was verified. Nothing changed. Clarify intent naturally; never require a special phrase or spoken draft ID.')
            reader = _recovery_reader(db, guild, user, context, draft_id, pending['revision'], 'discard')
            draft = journal_drafts.discard(conn, guild, user, draft_id, expected_revision=pending['revision'])
        result = _result('discard', draft)
    except Exception:
        result = reader() if reader else None
        if result is None:raise
        if not result.get('ok'):return result
    return _publish(context, capability, guild, user, result, 'discard')


def restore_story(db, guild, user, args):
    capability, context = _context(args, guild, user)
    if not _restore_request(_source(context, args),args.get('draft_id')):
        raise ValueError('Restore only the selected archived draft on the member’s explicit current restore request.')
    draft_id, expected = args.get('draft_id'), args.get('storage_revision')
    if type(expected) is not int or expected < 1:
        raise ValueError('Read the archived draft and use its exact storage_revision before restoring it.')
    reader = None
    try:
        with journal_transaction(db, args, guild, user, serialize=True) as conn:
            reader = _recovery_reader(db, guild, user, context, draft_id, expected, 'restore')
            draft = journal_drafts.restore(conn, guild, user, draft_id, expected_revision=expected)
        result = _result('restore', draft)
    except Exception:
        result = reader() if reader else None
        if result is None:raise
        if not result.get('ok'):return result
    return _publish(context, capability, guild, user, result, 'restore')


DISCARD_TOOLS = [
    schema('prepare_journal_discard', 'Preview one exact member-owned unlinked unfinished draft for recoverable discard. YOU say confirmation_prompt and wait for one natural confirmation; never require a member phrase or spoken ID. Never use finalized journal/trade deletion for an unfinished draft.', {'draft_id': STR}),
    schema('discard_journal_story', 'Discard ONLY after the delivered preview and clear natural assent in the immediate next member turn. No magic phrase or spoken ID. Soft archive; no journal/trade/execution deletion. For voice confirmation_text quotes the actual member reply; null for typed requests.', {'draft_id': {'type':'string'}, 'confirmation_text': STR}),
    schema('restore_journal_story', 'Restore one explicitly selected archived draft from get_journal_story view=discarded using its storage_revision, only when the member asks to restore. Voice confirmation_text quotes the actual request; null for typed.', {'draft_id': {'type':'string'}, 'storage_revision': {'type':'integer'}, 'confirmation_text': STR}),
]
