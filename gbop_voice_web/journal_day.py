"""One reviewed daily story, separate setup journals, and explicit save receipts.

The existing private draft table holds the recap. No execution rows or new
database permissions. Entries, objectives and finalization have separate states.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import re
from uuid import uuid4, uuid5, NAMESPACE_URL

from gbop_voice_web import journal_drafts
from gbop_voice_web.journal_context import journal_transaction
from gbop_voice_web.trade_photos import schema, STR, NUM

DAY_NAMES = {'stage_journal_day', 'get_journal_day', 'save_journal_day'}
DAY_PROMPT = """
DAILY JOURNAL — one conversation, separate setups, one save.
When the member journals their day or several setups, use stage_journal_day FIRST
instead of squeezing them into stage_journal_story. One trade_key per setup and
stable entry_index per entry; keep both when they return to 'the morning one'.
Read get_journal_day to resume by date. Other unfinished single-trade drafts do
not block capturing this day. Use existing Trade numbers only after owned lookup.
Resolve 'today' using the current New York market date, never an older draft's
date. With an active recap, new facts update it unless a different day is named.
Keep the entire current passage in raw_story; each setup's narration is its exact
excerpt. Apply 'scratch that' to the right setup/entry with clear_fields, keeping
the original words in history. If two setups fit a reference, capture the words
and ask just one question before assigning the detail. Never guess the target.
Use reflection for feelings about the day; use context_notes or entry notes for
feelings/reasoning tied to a setup/entry. Preserve the member's words, no diagnosis.
Order pending, position open, objective pending, and journal saved are different.
Two setups with one plus two filled entries means two trades, three entries.
An objective reaching its level does not prove the member exited or made money.
range_reference identifies each setup's named range. Resolve it with
resolve_market_range and restage the SAME trade_key to attach verified context.
Market candle times/levels orient the story; they never become exact fill times.
Missing feed/R/fill time never blocks journaling. Do not ask for times the member
already oriented with candle evidence just to fill a form; include them if useful.
Let the member finish. Give one short corrected readback, not a question after
every detail. No mandatory completion checklist. 'Save it' saves this reviewed
day, including its reflections and separate journals, with one verified receipt.
Only ask about save scope when genuinely ambiguous. Do not finalize unrelated
old drafts. Mock/example-only conversations stay unsaved. After a save, later
updates keep the same setup journals and do not duplicate entries or total R.
Infer known play/entry fields from the member's actual words: 'Super Soup on the
988' supplies play=9ate8 and entry_model=Super Soup; do not ask those again.
It does not supply a fill price/time, position status, direction, R or an outcome.
For photos in a daily recap, or a new photo-backed narration, use photo_sources
on the relevant setup (one setup is allowed). Keep visible transcription separate
from the member's words; mark [unclear], never infer dates from upload time.
Use uploaded photo IDs from the trusted turn. A page section has one stable
entry_index; several fills in that section still belong to that one setup.
A photo can support a new journal or an explicitly selected existing Trade #.
If the link is ambiguous, analyze the photo and ask once before linking. Keep
existing fills/results until the member corrects them. Save the reviewed story
and its photos together with save_journal_day, not separate silent finalizations.
A casual trade discussion can invite journaling once; it does not authorize an
automatic journal or alert. Do not offer alerts that are not actually available.
"""

DAY_TOOLS = [
    schema('stage_journal_day', 'Capture/correct one daily narration before questions. day_json: trade_date (YYYY-MM-DD), reflection (member wording), trades array. Each trade: stable trade_key, story (same fields as stage_journal_story), narration (exact excerpt of raw_story), optional range_reference (member named range), trade_number (verified existing journal) or separate_trade (explicit new trade). Entries retain stable entry_index. Optional photo_sources per setup: [{photo_id, entry_index (page section, not fill), transcription}]. Uploaded owned images only; supported visible facts in story, uncertain text marked unclear. Null preserves; story.clear_fields retracts mistaken facts. No fabricated fill times. Other drafts do not block this recap.',
           {'draft_id':STR, 'raw_story':STR, 'day_json':{'type':'string'}}),
    schema('get_journal_day', 'Read this member’s daily recap, its separate setups, filled/open/closed counts and verified save status. Resume by trade_date without asking about unrelated drafts. raw_offset pages the full original narration.',
           {'draft_id':STR, 'trade_date':STR, 'raw_offset':NUM}),
    schema('save_journal_day', 'Finalize the reviewed daily recap and its setup journals in one atomic save on the member’s explicit request. Keeps open positions/objectives unchanged and deduplicates retries. confirmation_text quotes the actual voice request; null for typed requests. Never finalize a mock.',
           {'draft_id':{'type':'string'}, 'confirmation_text':STR}),
]


def is_day(draft):
    return isinstance(draft, dict) and draft.get('kind') == 'journal_day'


def _selected_journal(conn, guild, user, journal_id):
    return conn.execute('''SELECT j.description,j.result_r,d.metadata FROM journals j
        LEFT JOIN journal_details d ON d.journal_id=j.id AND d.guild_id=j.guild_id AND d.user_id=j.user_id
        WHERE j.id=? AND j.guild_id=? AND j.user_id=?''', (journal_id, guild, user)).fetchone()


def _journal_fingerprint(row):
    if row is None:
        return None
    values = [row[k] for k in ('description', 'result_r', 'metadata')]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def _load(conn, context, args, guild, user):
    current = getattr(context, '_journal_story', None)
    requested = args.get('draft_id') or (current['id'] if is_day(current) else None)
    if requested:
        if (is_day(current) and current['id'] == requested and not current.get('persisted')
                and current.get('owner') == (*context.owner[:2], context.session_id)):
            if current.get('storage_revision') is not None:
                rows=journal_drafts.read(conn,guild,user,requested)
                if not rows or rows[0]['storage_revision']!=current['storage_revision']:
                    raise ValueError('This recap changed or was discarded. Read it before continuing.')
            return deepcopy(current)
        rows = journal_drafts.read(conn, guild, user, requested)
        if not rows or not is_day(rows[0]):
            raise ValueError('That daily journal is unavailable in this account.')
        if (is_day(current) and current['id'] == requested and not args.get('_refresh')
                and current.get('storage_revision') != rows[0]['storage_revision']):
            raise ValueError('This daily journal changed in another conversation. Read it before applying corrections.')
        return rows[0]
    return None


def _bind(context, day, generation):
    from gbop_voice_web.journal_story import _bind_loaded
    _bind_loaded(context, day, generation=generation)
    with context._lock:
        if context.current(generation):
            context._journal_day_id = day['id']
            context._journal_capture_error = None


def public(day, raw_offset=0):
    from gbop_voice_web.journal_story import _raw_page
    from gbop_voice_web.journal_range import summary as range_summary
    trades = []
    counts = dict(trades=0, filled_entries=0, open_entries=0, closed_entries=0,
                  pending_orders=0, unknown_entries=0)
    for key, trade in day.get('day_trades', {}).items():
        values = trade['values']
        counts['trades'] += 1
        for entry in values.get('entries', []):
            status = entry.get('status')
            if status in ('open', 'closed', 'partially_closed'):
                counts['filled_entries'] += 1
                counts['closed_entries' if status == 'closed' else 'open_entries'] += 1
            elif status == 'pending_order':
                counts['pending_orders'] += 1
            elif status != 'cancelled_order':
                counts['unknown_entries'] += 1
        trades.append(dict(trade_key=key, story=deepcopy(values),
                           trade_number=trade.get('trade_number'), revision=trade['revision'],
                           saved=trade.get('saved_revision') == trade['revision'],
                           range_reference=trade.get('range_reference'),
                           range_status=(trade.get('journal_range') or {}).get('status'),
                           journal_range=range_summary(trade.get('journal_range')),
                           photo_sources=deepcopy(trade.get('photo_sources', [])),
                           verified_candle=deepcopy((trade.get('journal_range') or {}).get('market_review',{}).get('selected_candle'))))
    saved = bool(trades) and all(t['saved'] for t in trades) and day.get('saved_revision') == day['revision']
    return dict(ok=True, kind='journal_day', draft_id=day['id'], revision=day['revision'],
                persisted=day.get('persisted', False), saved=saved,
                draft_status=day.get('draft_status'),
                status='saved' if saved else 'draft', trade_date=day['values'].get('trade_date'),
                reflection=day.get('day_reflection'), trades=trades, counts=counts,
                **_raw_page(day, raw_offset),
                instruction='One daily recap, separate trade journals. Read back the corrected story briefly; '
                'objective pending is not an unfilled order. Save this reviewed recap once on explicit request. '
                'No exact fill time, risk, P/L or emotion may be inferred. Original narration is retained underneath.')


def prompt_context(day):
    value = public(day)
    value.pop('raw_story_text', None)
    # Keep source identities in every turn, but retrieve long transcriptions
    # through get_journal_day only when needed instead of repeating image text.
    for trade in value['trades']:
        for source in trade['photo_sources']:
            source['transcription_chars'] = len(source.pop('transcription', ''))
    return '\nCURRENT PRIVATE DAILY JOURNAL: ' + json.dumps(value, ensure_ascii=False)


def _merge(values, patch):
    from gbop_voice_web.journal_story import FIELDS, ENTRY_FIELDS, _patch
    _patch(patch)
    value = deepcopy(values)
    for key in FIELDS:
        if patch.get(key) is not None:
            value[key] = patch[key]
    entries = {e['entry_index']: deepcopy(e) for e in value.get('entries', [])}
    for entry in patch.get('entries', []):
        entries.setdefault(entry['entry_index'], {'entry_index': entry['entry_index']}).update(
            {k:v for k,v in entry.items() if v is not None})
    for key in patch.get('clear_fields', []):
        if key in FIELDS:
            value.pop(key, None)
        else:
            match = re.fullmatch(r'entries\.(\d+)\.([a-z_]+)', key)
            if not match or match[2] not in ENTRY_FIELDS - {'entry_index'}:
                raise ValueError('Unsupported field correction.')
            entries.get(int(match[1]), {}).pop(match[2], None)
    value['entries'] = [entries[k] for k in sorted(entries)]
    _patch(value)
    return value


def _range(context, trade, reference):
    """Attach only exact server-verified range context, never model-supplied bars."""
    from gbop_voice_web.range_reference import resolve
    if not isinstance(reference, str) or not reference.strip() or len(reference)>2500:
        raise ValueError('Use the member’s bounded range description.')
    values=trade['values']
    base={key:values[source] for key,source in
          (('asset','asset'),('date_ny','trade_date'),('shift','session')) if values.get(source)}
    wanted=resolve(reference,base,context._turn_now)
    selected=(context.selected or {}).get('range_reference') or {}
    prior=trade.get('journal_range')
    state=dict(status='reported_unverified',description=reference,selectors=wanted.get('selectors',{}))
    if (wanted.get('ok') and context.selected == context.requested and context._journal_review
            and selected.get('selectors') == wanted['selectors']):
        state.update(status='verified',reference=deepcopy(selected),market_review=deepcopy(context._journal_review))
    elif prior and prior.get('status')=='verified' and prior.get('selectors')==state['selectors']:
        state=prior
    if prior and prior.get('selectors')!=state['selectors']:
        state['history']=[*prior.get('history',[]),dict(before=deepcopy(prior),at=journal_drafts.stamp())]
        state['explicit_correction']=True
    trade.update(range_reference=reference,journal_range=state)


def _decode_change(args):
    change=json.loads(args['day_json'])
    if (not isinstance(change,dict) or set(change)-{'trade_date','reflection','trades'}
            or len(json.dumps(change))>48000):
        raise ValueError('Use a bounded daily story with trade_date, reflection and trades.')
    if change.get('trade_date'):date.fromisoformat(change['trade_date'])
    updates=change.get('trades',[])
    if not isinstance(updates,list) or len(updates)>12:
        raise ValueError('Use at most twelve setups in one daily recap.')
    from gbop_voice_web.journal_story import _patch
    for item in updates:
        if not isinstance(item,dict):raise ValueError('Each setup must be an object.')
        _patch(item.get('story',{}))
    return change


def stage_day(db,guild,user,args):
    try:
        _decode_change(args)
    except (ValueError,TypeError,KeyError) as exc:
        # Retain a faithful passage even if structured extraction is malformed.
        # Storage/authentication failures themselves must never be retried here.
        fallback={}
        try:
            proposed=json.loads(args['day_json']).get('trade_date')
            if proposed:date.fromisoformat(proposed);fallback['trade_date']=proposed
        except (ValueError,TypeError,AttributeError,KeyError):pass
        result=_stage_day(db,guild,user,{**args,'day_json':json.dumps(fallback),'_extraction_error':str(exc)})
        return {**result,'ok':False,'status':'narration_retained_needs_correction',
                'error':str(exc),'instruction':'The original passage is retained; correct its structured extraction before reading back or saving the day.'}
    return _stage_day(db,guild,user,args)


def _stage_day(db, guild, user, args):
    from gbop_voice_web.journal_story import _context, _save_optout, _journaling_request
    from gbop_voice_web.journal_coach import init_coach
    capability, context = _context(args, guild, user)
    if getattr(context,'_journal_blocked_draft_id',None) and not args.get('draft_id'):
        raise ValueError('The previous draft is no longer active. Read or explicitly restore it before continuing.')
    change = _decode_change(args)
    updates = change.get('trades', [])
    if not isinstance(updates, list) or len(updates) > 12:
        raise ValueError('Use at most twelve setups in one daily recap.')
    raw = getattr(context, '_client_text', None)
    if raw is None:
        raw = args.get('raw_story')
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 16000:
        raise ValueError('Preserve the member’s actual journaling passage before organizing the day.')
    from gbop_voice_web.journal_discard import is_discard_request
    if is_discard_request(raw,context):
        raise ValueError('Use the confirmed draft-discard flow; do not record that command as narration.')
    init_coach(db)
    with journal_transaction(db, args, guild, user, serialize=True) as conn:
        day = _load(conn, context, args, guild, user)
        if (day and not args.get('draft_id') and change.get('trade_date')
                and day['values'].get('trade_date') not in (None,change['trade_date'])):
            day=None
        if day is None and change.get('trade_date'):
            matches=_days(conn,guild,user,change['trade_date'])
            if len(matches)>1:
                raise ValueError('More than one recap matches that day. Read get_journal_day to select it.')
            if matches:day=matches[0]
        if day is None:
            day = dict(id=uuid4().hex, values={'entries': []}, asked=[], revision=0,
                       raw_story=[], provenance={}, corrections=[], created_at=journal_drafts.stamp())
            day.update(kind='journal_day', day_trades={}, values={'entries':[]})
        expected = day.get('storage_revision')
        before = deepcopy(day)
        if args.get('_extraction_error'):
            day['validation_issue']=args['_extraction_error']
        elif updates:
            day.pop('validation_issue',None)
        if change.get('trade_date'):
            if day['values'].get('trade_date') not in (None, change['trade_date']) and day['day_trades']:
                raise ValueError('This recap belongs to another trading day. Start a separate daily recap.')
            day['values']['trade_date'] = change['trade_date']
        if 'reflection' in change and change['reflection'] is not None:
            if not isinstance(change['reflection'], str) or len(change['reflection']) > 6000:
                raise ValueError('Keep the day’s reflection in bounded member wording.')
            day['day_reflection'] = change['reflection']
        keys = set()
        for item in updates:
            if not isinstance(item, dict) or set(item)-{'trade_key','story','narration','trade_number','separate_trade','range_reference','photo_sources'}:
                raise ValueError('Each setup needs trade_key, story and optional narration or existing trade_number.')
            key = item.get('trade_key')
            if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9_-]{1,48}', key) or key in keys:
                raise ValueError('Use one stable, distinct trade_key for each setup.')
            keys.add(key)
            excerpt = item.get('narration')
            if excerpt is not None and (not isinstance(excerpt, str) or excerpt not in raw):
                raise ValueError('A trade’s narration must be an exact excerpt of the member’s passage.')
            trade = day['day_trades'].setdefault(key, dict(values={'entries': []}, revision=0, passages=[], corrections=[]))
            old_trade=deepcopy(trade)
            if item.get('photo_sources') is not None:
                from gbop_voice_web.journal_media import merge_sources
                trade['photo_sources'] = merge_sources(conn, guild, user, trade.get('photo_sources'), item['photo_sources'])
            old = deepcopy(trade['values'])
            clears=item.get('story',{}).get('clear_fields',[])
            r_only=all(k=='result_r' or re.fullmatch(r'entries\.(?:[1-9]|1[0-2])\.risk_r',k) for k in clears)
            if clears and not r_only and (trade.get('saved_revision') is not None or trade.get('selected_key') or item.get('trade_number')):
                raise ValueError('For saved facts, give the corrected replacement; only unknown R may be cleared directly.')
            trade['values'] = _merge(old, item.get('story', {}))
            if not trade['values'].get('trade_date') and day['values'].get('trade_date'):
                trade['values']['trade_date'] = day['values']['trade_date']
            if day['values'].get('trade_date') and trade['values'].get('trade_date')!=day['values']['trade_date']:
                raise ValueError('A setup belongs to another trading day. Keep it in that day’s recap.')
            if item.get('range_reference') is not None:
                _range(context,trade,item['range_reference'])
            number = item.get('trade_number')
            if number is not None:
                if type(number) is not int or number < 1 or trade.get('trade_number') not in (None, number):
                    raise ValueError('An existing trade link must retain its owned Trade number.')
                from gbop_voice_web.journal_numbers import resolve_journal_selector
                selected = resolve_journal_selector(conn, guild, user, trade_number=number)
                if not selected.get('ok'):
                    return selected
                identity=(selected.get('record_id'),selected.get('thesis_id'))
                if trade.get('selected_key') and tuple(trade['selected_key']) != identity:
                    raise ValueError('That Trade number now names a different record. Read history before proceeding.')
                if any(k!=key and t.get('trade_number')==number for k,t in day['day_trades'].items()):
                    raise ValueError('Two setups cannot overwrite the same journal. Clarify their grouping first.')
                if not trade.get('selected_key'):
                    # Adding a picture to a saved trade must not replace its story
                    # with an otherwise empty new recap or erase its earlier photos.
                    from gbop_voice_web.journal_story import _story_value, FIELDS
                    detail = _selected_journal(conn, guild, user, identity[0])
                    trade['selected_fingerprint'] = _journal_fingerprint(detail)
                    metadata = json.loads(detail['metadata'] or '{}') if detail else {}
                    saved_values = _story_value(detail['metadata']) if detail else None
                    base = {k:deepcopy(v) for k,v in (saved_values or metadata).items() if k in FIELDS or k == 'entries'}
                    if not saved_values and detail:
                        if detail['description']:
                            base.setdefault('context_notes', detail['description'])
                        if detail['result_r'] is not None:
                            base.setdefault('result_r', detail['result_r'])
                    if (base.get('trade_date') and day['values'].get('trade_date')
                            and base['trade_date'] != day['values']['trade_date']):
                        raise ValueError('That saved trade belongs to another day; keep its photo with the correct recap.')
                    trade['values'] = _merge(_merge(base, old), item.get('story', {}))
                    if not trade['values'].get('trade_date') and day['values'].get('trade_date'):
                        trade['values']['trade_date'] = day['values']['trade_date']
                    if day['values'].get('trade_date') and trade['values'].get('trade_date') != day['values']['trade_date']:
                        raise ValueError('That saved trade belongs to another day; keep its photo with the correct recap.')
                    if metadata.get('journal_photo_sources'):
                        from gbop_voice_web.journal_media import merge_sources
                        trade['photo_sources'] = merge_sources(conn, guild, user, metadata['journal_photo_sources'], trade.get('photo_sources', []))
                trade['trade_number'] = number
                trade['selected_key'] = identity
            if item.get('separate_trade') is True:
                from gbop_voice_web.journal_story import _new_trade_request
                if not _new_trade_request(raw):
                    raise ValueError('Only an explicit separate-trade statement may bypass a matching saved trade.')
                trade['new_trade'] = True
            clears=item.get('story',{}).get('clear_fields',[])
            suppressed=set(trade.get('suppressed_fields',[]))
            suppressed.difference_update(k for k,v in item.get('story',{}).items() if v is not None)
            suppressed.update(clears)
            trade['suppressed_fields']=sorted(suppressed)
            if excerpt and excerpt not in [p['text'] for p in trade['passages']]:
                trade['passages'].append(dict(text=excerpt, source='member_narration', recorded_at=journal_drafts.stamp()))
            if trade != old_trade:
                trade['revision'] += 1
                if trade['values'] != old and old != {'entries': []}:
                    trade['corrections'].append(dict(before=old, after=deepcopy(trade['values']), at=journal_drafts.stamp()))
        photo_owners = {}
        for key, trade in day['day_trades'].items():
            for source in trade.get('photo_sources', []):
                photo_key = (source['photo_id'], source['entry_index'])
                if photo_key in photo_owners and photo_owners[photo_key] != key:
                    raise ValueError('That photo page entry belongs to another setup in this recap; clarify the intended setup.')
                photo_owners[photo_key] = key
        if len(day['day_trades']) > 12:
            raise ValueError('This daily recap already contains twelve setups. No existing story was truncated.')
        if raw not in [p['text'] for p in day['raw_story']]:
            day['raw_story'].append(dict(text=raw, source='member_narration', source_key=f'{context.session_id}:{context.generation}', recorded_at=journal_drafts.stamp()))
        if day != before:
            day['revision'] += 1
        example = bool(day.get('example_only') or re.search(r'\b(?:hypothetical|mock|pretend|example)\b',raw,re.I))
        if _journaling_request(raw):
            context._journal_recording_paused = False
        if _save_optout(raw):context._journal_recording_paused=True
        day['example_only']=example
        day['recording_paused']=bool(getattr(context,'_journal_recording_paused',False))
        preview = example or day['recording_paused']
        if preview:
            day.update(persisted=False,draft_status='session_only')
        else:
            day = journal_drafts.write(conn, guild, user, day, expected_revision=expected,
                                       finalized=public(day)['saved'])
    _bind(context, day, capability.generation)
    if day.get('persisted'):
        from gbop_voice_web.voice_runtime import journal_write_committed
        journal_write_committed(guild,user,public(day))
    return public(day)


def _days(conn,guild,user,trade_date=None):
    if trade_date:date.fromisoformat(trade_date)
    # One bounded projection; never an unbounded N+1 download of every draft.
    predicates=['guild_id=?','user_id=?','payload LIKE ?']
    params=[guild,user,'%"kind":"journal_day"%']
    if trade_date:
        predicates.append('payload LIKE ?');params.append('%"trade_date":"'+trade_date+'"%')
    rows=conn.execute('SELECT * FROM journal_story_drafts WHERE '+' AND '.join(predicates)+' ORDER BY updated_at DESC LIMIT 201',params).fetchall()
    if len(rows)>200:
        raise ValueError('This account has more history than this quick lookup covers. Select the recap by its draft ID; no new recap was created.')
    values=[journal_drafts._project_row(row,guild,user) for row in rows]
    return [d for d in values if is_day(d) and d.get('draft_status')!='discarded'
            and (trade_date is None or d['values'].get('trade_date')==trade_date)]


def _saved_receipts(conn,guild,user,day):
    from gbop_voice_web.journal_story import _saved_story_row, _story_value
    from gbop_voice_web.journal_numbers import journal_display
    display={r['id']:r for r in journal_display(conn,guild,user)}
    receipts=[]
    for key,trade in day['day_trades'].items():
        child_id=uuid5(NAMESPACE_URL,f"gbop-day:{day['id']}:{key}").hex
        row=_saved_story_row(conn,guild,user,child_id)
        expected={'draft_id':child_id,'revision':trade['revision'],**trade['values']}
        if not row or _story_value(row['metadata'])!=expected or row['id'] not in display:
            return None
        from gbop_voice_web.journal_media import sources_saved
        if not sources_saved(conn, guild, user, row['id'], trade.get('photo_sources', [])):
            return None
        shown=display[row['id']]
        receipts.append(dict(trade_key=key,trade_number=shown['trade_number'],
                             journal_number=shown['journal_number'],ok=True,saved=True))
    return receipts


def get_day(db, guild, user, args):
    from gbop_voice_web.journal_story import _context
    capability, context = _context(args, guild, user)
    with journal_transaction(db, args, guild, user) as conn:
        day = _load(conn, context, {**args, '_refresh': True}, guild, user)
        if day and args.get('trade_date') and day['values'].get('trade_date')!=args['trade_date']:
            if args.get('draft_id'):raise ValueError('That recap has a different date.')
            day=None
        if day is None:
            days = _days(conn,guild,user,args.get('trade_date'))
            if len(days) != 1:
                return dict(ok=True, status='day_selection_required' if days else 'no_daily_journal',
                            days=[dict(draft_id=d['id'], trade_date=d['values'].get('trade_date'), counts=public(d)['counts']) for d in days[:20]])
            day = days[0]
        if public(day)['saved'] and _saved_receipts(conn,guild,user,day) is None:
            return dict(ok=False,saved=False,status='saved_day_changed',
                        error='A linked journal changed or is unavailable. Read current trade history before resuming this recap.')
    _bind(context, day, capability.generation)
    return public(day, args.get('raw_offset') or 0)


def save_day(db, guild, user, args):
    from gbop_voice_web.journal_story import _context, _finalize_request
    from gbop_voice_web.voice_runtime import _journal_write_sink, journal_write_committed
    capability, context = _context(args, guild, user)
    source = getattr(context, '_client_text', None)
    if source is None:source=args.get('confirmation_text')
    if not _finalize_request(source):
        raise ValueError('Finalize this reviewed day only on the member’s explicit save request.')
    original=deepcopy(getattr(context,'_journal_story',None))
    receipts=[]
    # Existing single-trade writers reuse ONE connection. Their inner context
    # managers must not commit; only the outer member transaction can do so.
    # Likewise suppress intermediate receipts until the entire day commits.
    sink=_journal_write_sink.set(None)
    try:
        with journal_transaction(db,args,guild,user,serialize=True) as conn:
            day=_load(conn,context,args,guild,user)
            if not day or not day['day_trades']:raise ValueError('Read the daily recap before saving it.')
            if day.get('validation_issue'):
                raise ValueError('The original narration is retained, but its last correction still needs organizing before finalizing.')
            if day.get('example_only') or not day.get('persisted'):
                raise ValueError('A mock or session-only preview cannot be finalized as actual trades.')
            original=deepcopy(day)
            revision=day['storage_revision']
            if public(day)['saved']:
                receipts=_saved_receipts(conn,guild,user,day)
                if receipts is None:raise ValueError('A saved setup changed or was deleted. Read history; it will not be recreated.')
            else:
                @contextmanager
                def shared_db():
                    yield conn
                for key,trade in day['day_trades'].items():
                    child_id=uuid5(NAMESPACE_URL,f"gbop-day:{day['id']}:{key}").hex
                    _save_setup(shared_db,conn,guild,user,args,context,capability,day,key,trade,child_id,receipts)
                day['saved_revision']=day['revision']
                day['save_receipts']=receipts
                day=journal_drafts.write(conn,guild,user,day,expected_revision=revision,finalized=True)
    except Exception:
        if original:_bind(context,original,capability.generation)
        raise
    finally:
        _journal_write_sink.reset(sink)
    _bind(context,day,capability.generation)
    result={**public(day),'receipts':receipts,'execution_records_changed':False,
            'instruction':'Give one short verified save receipt for this daily recap, with trade and entry counts. '
                          'Open positions and pending objectives remain open/pending; no execution rows were created.'}
    journal_write_committed(guild,user,result)
    return result


def _save_setup(db,conn,guild,user,args,context,capability,day,key,trade,child_id,receipts):
    from gbop_voice_web.journal_story import save_story, _bind_loaded
    if trade.get('saved_revision') is None and trade.get('selected_fingerprint'):
        current = _selected_journal(conn, guild, user, trade['selected_key'][0])
        if _journal_fingerprint(current) != trade['selected_fingerprint']:
            raise ValueError('That saved journal changed after this recap was prepared. Read its current story before adding the photo or correction.')
    existing=journal_drafts.read(conn,guild,user,child_id)
    child=existing[0] if existing else dict(id=child_id,values={'entries':[]},asked=[],revision=0,
        raw_story=[],provenance={},corrections=[],new_trade=trade.get('new_trade',False),
        save_authorized=False,example_only=False,created_at=day['created_at'])
    if child.get('day_id') not in (None,day['id']):raise ValueError('The setup’s saved identity is inconsistent.')
    # Never overwrite edits performed through an individual Trade journal.
    if existing and child.get('saved_revision') is not None:
        expected=trade.get('saved_revision')
        if expected is not None and child['revision']!=expected:
            raise ValueError('This setup was edited separately. Read its current journal before updating the day.')
    child.update(values=deepcopy(trade['values']),revision=trade['revision'],day_id=day['id'],
                 day_trade_key=key,raw_story=deepcopy(trade['passages']),corrections=deepcopy(trade['corrections']),
                 suppressed_fields=deepcopy(trade.get('suppressed_fields',[])))
    if trade.get('journal_range'):child['journal_range']=deepcopy(trade['journal_range'])
    if trade.get('selected_key'):
        child.update(selected_key=tuple(trade['selected_key']),trade_number=trade['trade_number'])
    child['new_trade']=trade.get('new_trade',False) or _distinct_setup(conn,guild,user,trade,day)
    child=journal_drafts.write(conn,guild,user,child,expected_revision=child.get('storage_revision'),
                               finalized=child.get('saved_revision')==child['revision'])
    _bind_loaded(context,child,generation=capability.generation)
    result=save_story(db,guild,user,{**args,'draft_id':child_id})
    if not result.get('ok'):
        raise ValueError('The day was not finalized; no setup from this save committed. '
                         +str(result.get('error') or 'Read the existing trade before retrying.'))
    from gbop_voice_web.journal_story import _saved_story_row
    from gbop_voice_web.journal_media import attach_sources
    if trade.get('photo_sources'):
        saved_row = _saved_story_row(conn, guild, user, child_id)
        attach_sources(conn, guild, user, saved_row['id'], trade['photo_sources'])
    trade.update(saved_revision=trade['revision'],trade_number=result['trade_number'])
    stored=journal_drafts.read(conn,guild,user,child_id)[0]
    trade['selected_key']=stored['selected_key']
    receipts.append(dict(trade_key=key,ok=True,saved=True,trade_number=result['trade_number'],
                         journal_number=result['journal_number']))


def _distinct_setup(conn,guild,user,trade,day):
    """Do not ask about sibling setups or trades proven to be on other dates.

    Unknown dates and indistinguishable same-day setups still require an owned
    selection. This is not a blanket new-trade bypass from a model-supplied key.
    """
    from gbop_voice_web.journal_story import story_trade_candidates
    from gbop_voice_web.journal_numbers import resolve_journal_selector
    values=trade['values']
    if not values.get('trade_date'):return False
    siblings={t.get('trade_number') for t in day['day_trades'].values() if t is not trade}
    for candidate in story_trade_candidates(conn,guild,user,values):
        if candidate['trade_number'] in siblings:continue
        selected=resolve_journal_selector(conn,guild,user,trade_number=candidate['trade_number'])
        row=conn.execute('SELECT metadata FROM journal_details WHERE journal_id=? AND guild_id=? AND user_id=?',
                         (selected.get('record_id'),guild,user)).fetchone()
        metadata=json.loads(row['metadata'] or '{}') if row else {}
        if not metadata.get('trade_date') or metadata['trade_date']==values['trade_date']:return False
    return True


def prepare_day_finalization(db,guild,user,args):
    """Read-only receipt recovery after a disconnected atomic daily save."""
    from gbop_voice_web.journal_story import _context
    from gbop_voice_web.journal_context import JournalBinding
    from gbop_voice_web.journal_finalization import _bounded_connection, _digest
    capability,context=_context(args,guild,user)
    def facts(day):
        return {'date':day['values'].get('trade_date'),'reflection':day.get('day_reflection'),
                'trades':{k:{'values':t['values'],'revision':t['revision'],'photo_sources':t.get('photo_sources',[])} for k,t in day['day_trades'].items()},
                'raw_story':day['raw_story']}
    with journal_transaction(lambda:_bounded_connection(db),args,guild,user,serialize=True) as conn:
        day=_load(conn,context,args,guild,user)
        if not day or not day.get('persisted'):return None
        identity,revision,storage=day['id'],day['revision'],day['storage_revision']
        digest=_digest(facts(day))
        original_status=day['draft_status']
        session,auth=context.session_id,context._auth_revision
    def reconcile():
        with context._lock:
            if (context.closed or context.session_id!=session or context._auth_revision!=auth
                    or tuple(context.owner[:2])!=(guild,user)):return None
            current=JournalBinding(context,context.generation)
        with journal_transaction(lambda:_bounded_connection(db),{'_journal_binding':current},guild,user,serialize=True) as conn:
            rows=journal_drafts.read(conn,guild,user,identity)
            if len(rows)!=1:return None
            stored=rows[0]
            if not is_day(stored) or stored['revision']!=revision or _digest(facts(stored))!=digest:return None
            if (original_status==stored['draft_status']=='unfinished' and stored['storage_revision']==storage):
                return dict(ok=False,saved=False,status='journal_not_saved',draft_id=identity,
                            reconciliation_verified=True,error='The daily save did not commit. Its unfinished recap remains intact.')
            if stored['draft_status']!='finalized' or not public(stored)['saved']:return None
            receipts=_saved_receipts(conn,guild,user,stored)
            if receipts is None:return None
            return dict(ok=True,saved=True,status='saved',persisted=True,draft_id=identity,
                        revision=revision,draft_status='finalized',counts=public(stored)['counts'],
                        receipts=receipts,reconciliation_verified=True,execution_records_changed=False)
    return reconcile
