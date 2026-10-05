"""Member-scoped journal recall and explicit private delivery, without AI writes."""
import os
import json
import re
from hashlib import sha256
from textwrap import indent
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from gbop_voice_web.trade_photos import schema
from gbop_voice_web.photo_recall import result_text
from gbop_voice_web.delivery_receipts import DELIVERY_ACTION


def _metadata(value):
    try:
        parsed = value if isinstance(value, dict) else json.loads(value or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _trade_timelines(conn, guild_id, user_id, owned_theses, journals):
    """Readable facts from existing owner events, never nested audit snapshots."""
    timelines = {tid: [] for tid in owned_theses}
    available = True
    own_journals = {r['id']: r for r in journals}
    for table in ('thesis_executions', 'thesis_events'):
        if not conn.execute('PRAGMA table_info(' + table + ')').fetchall():
            continue
        try:
            rows = conn.execute('SELECT * FROM ' + table + ' WHERE guild_id=? AND user_id=? ORDER BY id',
                                (guild_id, user_id)).fetchall()
        except Exception:
            available = False
            continue
        for stored in rows:
            row = dict(stored)
            tid = row.get('thesis_id')
            if tid not in timelines:
                continue
            item = {'created_at': row.get('created_at')}
            if table == 'thesis_executions':
                item.update(kind='execution', **{k: row.get(k) for k in
                    ('entry_model', 'tier', 'risk_r', 'entry_invalidation', 'note')})
            elif row.get('event') in ('journal_canonical_v1', 'journal_source_v1'):
                continue
            elif row.get('event') == 'journal_audit_v1':
                audit = _metadata(row.get('details'))
                journal = own_journals.get(audit.get('journal_id'))
                if not journal or journal.get('thesis_id') != tid:
                    continue
                before, after = audit.get('before') or {}, audit.get('after') or {}
                if not isinstance(before, dict) or not isinstance(after, dict):
                    continue
                old, new = before.get('journal') or {}, after.get('journal') or {}
                changes = {key: {'before': old.get(key), 'after': new.get(key)}
                    for key in ('description', 'rule_adherence', 'result_r', 'study_note')
                    if old.get(key) != new.get(key)}
                old_meta = _metadata((before.get('details') or {}).get('metadata'))
                new_meta = _metadata((after.get('details') or {}).get('metadata'))
                # Preserve member prose/facts in full, while immutable market
                # snapshots and internal attachment markers stay out of replies.
                fields = ('transcription', 'uncertainties', 'asset', 'direction', 'play',
                    'entry_model', 'tier', 'session', 'trade_date', 'entry_price', 'exit_price',
                    'stop_price', 'target_price', 'pnl', 'risk', 'exit_reason', 'emotion',
                    'labels', 'kind', 'adherence', 'reported_entry_at', 'reported_exit_at', 'reported_outcome', 'self_grade', 'feeling_history')
                for key in fields:
                    if old_meta.get(key) != new_meta.get(key):
                        changes['metadata.' + key] = {'before': old_meta.get(key), 'after': new_meta.get(key)}
                if not changes:
                    continue
                item.update(kind='journal_update', changes=changes)
            else:
                item.update(kind='trade_event', event=row.get('event'),
                            details=row.get('details'), result_r=row.get('result_r'))
            timelines[tid].append(item)
    for items in timelines.values():
        items.sort(key=lambda item:str(item.get('created_at') or ''))
    return timelines, available


def history(db, guild_id, user_id, args, *, include_photo_bytes=False):
    """One read-only member view per owned trade, plus explicitly legacy rows.

    Older duplicate journals are grouped without rewriting any stored row. Until
    a canonical journal exists, only uncontested fields are projected; all of the
    original text and metadata stays available in legacy_history.
    """
    from gbop_voice_web.journal_numbers import journal_display
    view = args.get('view') or 'detail'
    if view not in ('index','detail'):
        return {'ok':False,'error':'Choose index or detail view.'}
    index_view = view == 'index'
    if index_view and any(args.get(key) is not None for key in
            ('latest','trade_number','journal_number','legacy_journal_number','detail_offset','date_basis')):
        return {'ok':False,'status':'index_selectors_conflict',
                'error':'Index lists all record numbers with limit/offset only. Use view=detail for exact or latest records and date selectors.'}
    limit = max(1, min(int(args.get('limit') or 5), 20))
    offset = max(0, int(args.get('offset') or 0))
    latest = args.get('latest')
    basis = args.get('date_basis') or ('trade' if latest == 'trade' else 'saved')
    if latest not in (None, 'trade', 'journal') or basis not in ('saved', 'trade'):
        return {'ok': False, 'error': 'Choose latest trade or journal, and saved or trade date.'}
    if latest and (offset or any(args.get(k) is not None for k in ('trade_number','journal_number','legacy_journal_number'))):
        return {'ok': False, 'error': 'Use either an exact record number or latest, without a record offset.'}
    with db() as conn:
        selection = None
        if any(args.get(k) is not None for k in ('trade_number','journal_number','legacy_journal_number')):
            from gbop_voice_web.journal_numbers import resolve_journal_selector
            selection = resolve_journal_selector(conn,guild_id,user_id,
                trade_number=args.get('trade_number'),journal_number=args.get('journal_number'),
                legacy_journal_number=args.get('legacy_journal_number'))
            if not selection.get('ok'):
                return selection
        trades = conn.execute('SELECT * FROM theses WHERE guild_id=? AND user_id=? ORDER BY id', (guild_id, user_id)).fetchall()
        rows = [dict(r) for r in conn.execute('SELECT * FROM journals WHERE guild_id=? AND user_id=? ORDER BY id',
                                             (guild_id, user_id)).fetchall()]
        if args.get('_record_key'):
            kind, _, ident = str(args['_record_key']).partition(':')
            source = trades if kind == 'trade' else rows if kind == 'legacy_journal' else []
            number = next((n for n, r in enumerate(source, 1) if str(r['id']) == ident), None)
            if number is None:
                return {'ok': False, 'error': 'The selected record is no longer available in your account.'}
            from gbop_voice_web.journal_numbers import resolve_journal_selector
            selection = resolve_journal_selector(conn, guild_id, user_id,
                **{('trade_number' if kind == 'trade' else 'legacy_journal_number'): number})
            if not selection.get('ok'):
                return selection
        trade_numbers = {r['id']: n for n, r in enumerate(trades, 1)}
        timelines, timeline_available = ({tid:[] for tid in trade_numbers},True) if index_view else _trade_timelines(conn,guild_id,user_id,trade_numbers,rows)
        # Resolve all identities in bulk: remote DB round trips must not grow
        # linearly with every historical trade in a member's journal.
        canonical = {tid: None for tid in trade_numbers}
        for item in journal_display(conn,guild_id,user_id):
            if item['canonical']:
                canonical[item['thesis_id']] = item['id']
    metadata_by_id = {}
    details_saved_at = {}
    metadata_available = True
    try:
        with db() as conn:
            details = conn.execute('''SELECT d.* FROM journal_details d
                JOIN journals j ON j.id=d.journal_id AND j.guild_id=d.guild_id AND j.user_id=d.user_id
                WHERE d.guild_id=? AND d.user_id=?''', (guild_id, user_id)).fetchall()
        metadata_by_id = {r['journal_id']: _metadata(r['metadata']) for r in details}
        details_saved_at = {r['journal_id']: dict(r).get('updated_at') for r in details}
    except Exception:
        metadata_available = False
    legacy_numbers = {r['id']: n for n,r in enumerate(rows,1)}

    def record(row, *, legacy=False):
        return dict(journal_id=row['id'], journal_number=None if legacy else trade_numbers.get(row.get('thesis_id')),
            trade_id=trade_numbers.get(row.get('thesis_id')), trade_number=trade_numbers.get(row.get('thesis_id')),
            legacy_journal_number=legacy_numbers[row['id']] if legacy else None,
            is_legacy=legacy, result_r=row.get('result_r'), rule_adherence=row.get('rule_adherence'),
            summary=row.get('description'), study_note=row.get('study_note'), created_at=row.get('created_at'),
            metadata=metadata_by_id.get(row['id'], {}),
            saved_at=details_saved_at.get(row['id']) or row.get('created_at'),
            _thesis_id=row.get('thesis_id') if row.get('thesis_id') in trade_numbers else None,
            _journal_ids=[row['id']])

    grouped, journals = {}, []
    for row in rows:
        tid = row.get('thesis_id')
        if tid in trade_numbers:
            grouped.setdefault(tid, []).append(row)
        else:
            view = record(row, legacy=True)
            view.update(record_kind='legacy_journal', legacy_history=[], legacy_history_count=0,
                        canonical_record_exists=False, _sort_id=row['id'])
            journals.append(view)
    preserved_count = 0
    for trade in trades:
        tid = trade['id']
        entries = grouped.get(tid, [])
        trade = dict(trade)
        facts = {k: trade.get(k) for k in ('asset','direction','play','session','objective',
            'thesis_invalidation','status','max_r','created_at','closed_at','final_result_r','close_note')
            if trade.get(k) is not None}
        if not entries:
            number = trade_numbers[tid]
            view = dict(journal_id=None, journal_number=number, trade_id=number, trade_number=number,
                legacy_journal_number=None, is_legacy=False, record_kind='trade_journal',
                canonical_record_exists=False, virtual_trade_record=True,
                result_r=trade.get('final_result_r'), rule_adherence=None, study_note=None,
                summary=trade.get('close_note') or ' · '.join(str(trade[k]) for k in ('asset','direction','play') if trade.get(k)) or 'Existing trade record',
                metadata={}, created_at=trade.get('created_at'), legacy_history=[],
                legacy_history_count=0, conflicting_legacy_fields=[], needs_clarification=False,
                trade_facts=facts, updates=timelines[tid], update_count=len(timelines[tid]), _sort_id=tid,
                _thesis_id=tid, _journal_ids=[])
            journals.append(view)
            continue
        canonical_id = canonical[tid]
        saved = next((r for r in entries if r['id'] == canonical_id), None)
        prior = [r for r in entries if r['id'] != canonical_id]
        if saved:
            view = record(saved)
            conflicts = []
        else:
            # A read must not select one contradictory legacy outcome as true.
            projection = dict(entries[0])
            conflicts = []
            for field in ('description','rule_adherence','result_r','study_note'):
                if any(r.get(field) != projection.get(field) for r in entries):
                    projection[field] = None if field == 'result_r' else ''
                    conflicts.append(field)
            view = record(projection)
            view.update(journal_id=None, metadata={}, created_at=None)
        history_rows = [record(r, legacy=True) for r in prior]
        preserved_count += len(history_rows)
        view.update(record_kind='trade_journal', canonical_record_exists=saved is not None,
            legacy_history=history_rows, legacy_history_count=len(history_rows),
            conflicting_legacy_fields=conflicts, needs_clarification=bool(conflicts),
            trade_facts=facts, updates=timelines[tid], update_count=len(timelines[tid]),
            _sort_id=max(r['id'] for r in entries), _thesis_id=tid, _journal_ids=[r['id'] for r in entries])
        journals.append(view)
    for view in journals:
        times = [view.get('created_at'), view.get('saved_at'), (view.get('trade_facts') or {}).get('created_at'),
                 (view.get('trade_facts') or {}).get('closed_at')]
        times += [r.get('created_at') for r in view.get('legacy_history') or []]
        times += [r.get('saved_at') for r in view.get('legacy_history') or []]
        times += [r.get('created_at') for r in view.get('updates') or []]
        view['saved_at'] = max((str(t) for t in times if t is not None), default='') or None
        view['reported_trade_date'] = _reported_trade_date(view)
        view['_sort_key'] = (view['saved_at'] or '', view.pop('_sort_id'))
    journals.sort(key=lambda r:r.pop('_sort_key'), reverse=True)
    if index_view:
        journals.sort(key=lambda r:(r['is_legacy'],r.get('trade_number') or r['legacy_journal_number']))
    total = len(journals)
    matching = journals
    if selection is not None:
        if selection.get('explicit_legacy'):
            original = next(r for r in rows if r['id'] == selection['record_id'])
            view = record(original, legacy=True)
            view.update(record_kind='legacy_journal',legacy_history=[],legacy_history_count=0,
                        canonical_record_exists=bool(selection.get('canonical')),
                        saved_at=details_saved_at.get(original['id']) or original.get('created_at'))
            view['reported_trade_date'] = _reported_trade_date(view)
            matching = [view]
        else:
            matching = [r for r in journals if not r.get('is_legacy') and r.get('trade_number') == selection['trade_number']]
    if latest == 'trade':
        matching = [r for r in matching if not r.get('is_legacy')
                    and (r.get('trade_facts') or {}).get('status') in ('OPEN','CLOSED','JOURNALED')]
    if basis == 'trade':
        dated = [(r, _date_interval(r.get('reported_trade_date'))) for r in matching]
        ambiguous = any(interval is None for _, interval in dated)
        if dated and not ambiguous:
            winner = max(range(len(dated)), key=lambda n: dated[n][1][0])
            ambiguous = any(interval[1] >= dated[winner][1][0]
                            for n, (_, interval) in enumerate(dated) if n != winner)
        if latest and ambiguous:
            return {'ok': False, 'status': 'trade_date_ambiguous', 'needs_clarification': True,
                    'error': 'The actual latest trade cannot be established from the saved trade dates. '
                             'Ask which Trade #, or whether the member means the latest saved record.',
                    'candidates': [{'trade_number': r.get('trade_number'),
                                    'legacy_journal_number': r.get('legacy_journal_number'),
                                    'saved_at': r.get('saved_at'),
                                    'reported_trade_date': r.get('reported_trade_date')} for r in matching]}
        matching = sorted(matching, key=lambda r: _date_key(r.get('reported_trade_date')) or float('-inf'), reverse=True)
    selected = matching[:1] if latest else matching[offset:offset+limit]
    photos, photos_available = [], True
    try:
        from gbop_voice_web.journal_bundle import attach_photos
        if not index_view:
            photos = attach_photos(db, guild_id, user_id, selected, include_bytes=include_photo_bytes)
    except Exception:
        photos_available = False
        for row in selected:
            row.update(photos_available=False, photo_count=None)
    for row in selected:
        row['record_key'] = ('trade:' + str(row['_thesis_id']) if not row.get('is_legacy')
                             else 'legacy_journal:' + str(row['journal_id']))
        row.pop('_thesis_id', None)
        row.pop('_journal_ids', None)
        for prior in row.get('legacy_history') or []:
            prior.pop('_thesis_id', None)
            prior.pop('_journal_ids', None)
    open_count = sum(r['status'] == 'OPEN' for r in trades)
    result = dict(ok=True, journals=selected, metadata_available=metadata_available, timeline_available=timeline_available,
        photos_available=photos_available, photo_count=len(photos) if photos_available else None,
        selection_basis=basis, latest=latest,
        selection_note=('Latest by reported trade date.' if basis == 'trade' else
                        'Ordered by saved/updated time; this does not establish when the trade occurred.'),
        journal_count=total, canonical_journal_count=len(trades),
        legacy_journal_count=sum(r['is_legacy'] for r in journals),
        preserved_legacy_history_count=preserved_count, stored_journal_entry_count=len(rows),
        trade_count=len(trades), open_trade_count=open_count,
        closed_trade_count=sum(r['status'] == 'CLOSED' for r in trades),
        matched_record_count=len(matching),
        has_more=False if latest else offset + len(selected) < len(matching), next_offset=offset + len(selected),
        identity_scope='Authenticated Discord account only; another login may have different records.',
        numbering_note='One Trade #N record includes its journal; Journal #N is an accepted alias. Separate historical entries use Legacy journal #N; ambiguous old journal numbers require clarification.')
    if index_view:
        from gbop_voice_web.trade_self_grades import self_grade_summary
        index=[]
        for row in selected:
            item={key:row.get(key) for key in ('journal_number','trade_number','legacy_journal_number',
                'record_kind','is_legacy','record_key','canonical_record_exists','legacy_history_count','result_r')}
            facts=row.get('trade_facts') or {}
            for key,value in (('asset',facts.get('asset') or row['metadata'].get('asset')),
                              ('status',facts.get('status'))):
                item[key]=value if isinstance(value,str) and len(value)<=120 else None
                if value is not None and item[key] is None:
                    item[key+'_label_omitted']=True
            grade=self_grade_summary(row.get('metadata'),row.get('result_r'))
            # The index needs a classification, never unbounded notes/prose.
            item['self_grade']=({key:grade[key] for key in
                ('type','member_reported','recorded_type','needs_clarification') if key in grade} if grade else None)
            item['detail_request']={'tool':'get_journal_history','args':{'view':'detail',
                **({'legacy_journal_number':row['legacy_journal_number']} if row['is_legacy'] else {'trade_number':row['trade_number']})}}
            index.append(item)
        result['journals']=index
        for key in ('timeline_available','photos_available','photo_count'):
            result.pop(key,None)
        result.update(view='index',details_omitted=True,
            selection_note='Record-number index. No chronology inferred; follow detail_request for the complete record.',
            detail_note='Notes, updates and photos were not fetched for this index. Missing details are not absent.')
        return result
    if include_photo_bytes:
        result['_delivery_photos'] = photos
    if latest or selection is not None:
        # Complete context is available in read-only pages, including older
        # feelings/updates that a normal bounded model preview would omit.
        full = '\n\n'.join(messages(result))
        start = max(0, int(args.get('detail_offset') or 0))
        end = min(start+12000, len(full))
        if len(json.dumps(full[start:end])) > 12000:
            lo, hi = start, end
            while lo < hi:
                middle = (lo + hi + 1) // 2
                if len(json.dumps(full[start:middle])) <= 12000:
                    lo = middle
                else:
                    hi = middle - 1
            end = lo
        result.update(context_text=full[start:end], has_more_details=end < len(full),
                      next_detail_offset=end)
    return result


def _date_key(value):
    interval = _date_interval(value)
    return interval[0] if interval else None


def _date_interval(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if len(str(value)) == 10:
            parsed = parsed.replace(tzinfo=ZoneInfo('America/New_York'))
            return parsed.timestamp(), (parsed + timedelta(days=1)).timestamp()
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp(), parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _reported_trade_date(row):
    records = [row] if row.get('canonical_record_exists') else [row] + (row.get('legacy_history') or [])
    values = set()
    for record in records:
        meta = record.get('metadata') or {}
        provenance = meta.get('provenance') or {}
        defaults = provenance.get('context_defaults') or []
        reported = provenance.get('member_reported') or []
        for key in ('reported_entry_at', 'trade_date'):
            value = meta.get(key)
            if isinstance(value, str) and value and (key not in defaults or key in reported):
                values.add(value)
                break
    return next(iter(values)) if len(values) == 1 else None


def _record_text(row, title):
    value = (title + f"\nResult: {result_text(row.get('result_r'))}"
             + '\nEntry: ' + str(row.get('summary') or 'Not specified')
             + '\nAdherence: ' + str(row.get('rule_adherence') or 'Not specified')
             + '\nStudy note: ' + str(row.get('study_note') or 'Not specified'))
    meta = row.get('metadata') or {}
    value += '\nSaved/logged at: ' + str(row.get('saved_at') or row.get('created_at') or 'Unknown')
    for key,label in (('trade_date','Reported trade date'),('reported_entry_at','Reported entry'),
                      ('reported_exit_at','Reported exit'),('reported_outcome','Reported outcome'),
                      ('asset','Reported instrument'),('direction','Reported direction'),('play','Reported play'),
                      ('entry_model','Reported entry model'),('tier','Reported tier'),('session','Reported session'),
                      ('entry_price','Reported entry price'),('exit_price','Reported exit price'),
                      ('stop_price','Reported stop'),('target_price','Reported target'),('pnl','Reported P/L'),
                      ('risk','Reported risk'),('exit_reason','Exit reason'),('uncertainties','Uncertainties'),
                      ('labels','Labels'),('kind','Record kind')):
        if meta.get(key) is not None and meta.get(key) != '':
            provenance = meta.get('provenance') or {}
            if key == 'trade_date' and key in (provenance.get('context_defaults') or []) and key not in (provenance.get('member_reported') or []):
                label = 'Reviewed market date (not a reported execution date)'
            value += '\n' + label + ': ' + _display_value(meta[key])
    if meta.get('emotion'):
        value += '\nLegacy feeling note (stage/time unspecified): ' + str(meta['emotion'])
    for report in meta.get('feeling_history') or []:
        if not isinstance(report, dict):
            continue
        value += ('\nFeeling #' + str(report.get('id', '?')) + ' [' + str(report.get('stage') or 'unknown') + ']: ' + str(report.get('feeling') or 'Not recorded')
            + ' · reported time: ' + str(report.get('reported_at') or 'unknown')
            + ' · logged: ' + str(report.get('recorded_at') or 'unknown'))
        if report.get('correction_of') is not None:
            value += ' · corrects feeling #' + str(report['correction_of'])
    from gbop_voice_web.trade_self_grades import self_grade_summary
    self_grade = self_grade_summary(meta, row.get('result_r'))
    if self_grade:
        label = self_grade['type'].replace('type', 'Type ') if self_grade['type'] else 'Ungraded'
        value += '\nMember SELF grade: ' + label + ' · ' + self_grade['adherence'] + ' · ' + self_grade['outcome']
        if self_grade.get('needs_clarification'):
            value += ' · assessment needs clarification after a journal correction'
        if self_grade['predefined_stop'] is not None:
            value += ' · predefined stop: ' + ('yes' if self_grade['predefined_stop'] else 'no')
        if self_grade['off_plan_reason']:
            value += ' · reported reason: ' + self_grade['off_plan_reason']
        if self_grade['note']:
            value += '\nSELF note: ' + self_grade['note']
    scope = (meta.get('market_review') or {}).get('selection') or {}
    if scope:
        value += '\nReviewed scope: ' + ' · '.join(str(scope[k]) for k in ('asset','date_ny','shift','anchor_start_ny') if scope.get(k))
    # Handwritten source prose is distinct from the member-facing summary.
    if meta.get('transcription') and meta['transcription'] != row.get('summary'):
        value += '\nOriginal transcription: ' + str(meta['transcription'])
    return value


def _display_value(value, *, serialized=False):
    """Readable saved fields, without dropping nested facts or editing prose."""
    if serialized and isinstance(value, str):
        def unique_object(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError('Repeated keys must retain their original text.')
            return result
        try:
            parsed = json.loads(value, object_pairs_hook=unique_object)
            if isinstance(parsed, (dict, list, str)):
                return _display_value(parsed)
        except (ValueError, TypeError, RecursionError):
            # Event details may be arbitrary member prose, including JSON.
            # Ambiguous/deep serialized text must stay intact, not lose facts
            # or stop the rest of the journal from being retrieved.
            return value
        return value
    if value is None:
        return 'Not recorded'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, dict):
        fields = []
        for key, item in value.items():
            label, rendered = str(key).replace('_', ' '), _display_value(item)
            fields.append(label + (':\n' + indent(rendered, '  ')
                          if isinstance(item, (dict, list, tuple)) or '\n' in rendered
                          else ': ' + rendered))
        return '\n'.join(fields) or '(empty object)'
    if isinstance(value, (list, tuple)):
        return '\n'.join(f'{index}. ' + _display_value(item).replace('\n', '\n   ')
                         for index, item in enumerate(value, 1)) or '(empty list)'
    return str(value)


def _text_chunks(value, limit=1800):
    """Lossless transport slices: paragraphs, lines, words, then long tokens.

    Count UTF-16 units conservatively for Discord, and never cut a Unicode
    character. Whitespace stays with its slice, so the source is recoverable.
    """
    if limit < 2:
        raise ValueError('The message limit must fit a Unicode character.')
    while value:
        units, end = 0, 0
        for char in value:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > limit:
                break
            units += size
            end += 1
        if end == len(value):
            yield value
            return
        window = value[:end]
        # Prefer natural sections, but avoid a tiny heading-only message.
        boundary = window.rfind('\n\n')
        if boundary >= end // 3:
            end = boundary + 2
        else:
            boundary = window.rfind('\n')
            if boundary >= end // 3:
                end = boundary + 1
            else:
                boundary = next((i for i in range(end - 1, -1, -1)
                                 if value[i].isspace()), -1)
                if boundary >= 0:
                    end = boundary + 1
        yield value[:end]
        value = value[end:]


def _record_chunks(value, title):
    continuation = title + ' (continued)\n'
    budget = 1800 - len(continuation.encode('utf-16-le')) // 2
    for index, chunk in enumerate(_text_chunks(value, budget)):
        yield (continuation if index else '') + chunk


def messages(result):
    header = (f"Your GBOP journal — {result['journal_count']} journal records; "
              f"{result['trade_count']} trade records ({result['open_trade_count']} open).")
    if result.get('legacy_journal_count') or result.get('preserved_legacy_history_count'):
        header += ' Original legacy entries are preserved and labeled separately.'
    output = [header]
    if result.get('selection_note'):
        output[0] += '\n' + result['selection_note']
    if result.get('metadata_available') is False or result.get('timeline_available') is False:
        output.append('Some saved notes or update history could not be retrieved; missing details are unknown.')
    for row in result['journals']:
        title = (f"Legacy journal #{row['legacy_journal_number']}" if row.get('is_legacy')
                 else f"Trade #{row.get('trade_number') or row.get('trade_id') or row['journal_number']} journal")
        if row.get('canonical_record_exists') is False and not row.get('is_legacy') and not row.get('virtual_trade_record'):
            value = title + '\nHistorical entries grouped below; no canonical journal has been saved yet.'
            if row.get('conflicting_legacy_fields'):
                value += '\nSome historical fields disagree; no outcome has been chosen.'
        else:
            value = _record_text(row, title)
        facts = row.get('trade_facts') or {}
        if facts:
            value += '\n\nTrade details:\n' + _display_value(facts)
        updates = row.get('updates') or []
        if updates:
            count_label = 'entry' if len(updates) == 1 else 'entries'
            value += f'\n\nSaved execution and update history ({len(updates)} {count_label})'
            value += '\nEach entry is a separate saved event; similar entries are retained.'
        for index, update in enumerate(updates, 1):
            kind = {'journal_update': 'Journal correction', 'execution': 'Execution',
                    'trade_event': 'Trade event'}.get(update.get('kind'), 'Update')
            value += f'\n\nSaved update #{index} · {kind}'
            value += '\nLogged: ' + str(update.get('created_at') or 'logging time unavailable')
            if update.get('kind') == 'journal_update':
                for key,change in (update.get('changes') or {}).items():
                    value += '\n' + key.replace('metadata.','').replace('_',' ') + ':'
                    value += '\nBefore: ' + _display_value(change.get('before'))
                    value += '\nAfter: ' + _display_value(change.get('after'))
            else:
                for key, item in update.items():
                    if key not in ('created_at', 'kind') and item is not None:
                        rendered = _display_value(item, serialized=key == 'details')
                        value += '\n' + key.replace('_', ' ') + ':'
                        value += ('\n' + indent(rendered, '  ') if '\n' in rendered
                                  else ' ' + rendered)
        # Deliver every saved source entry in bounded transport messages. The
        # bounded model-facing preview must never become the DM source of truth.
        for prior in row.get('legacy_history') or []:
            value += '\n\n' + _record_text(prior, f"Preserved legacy journal #{prior['legacy_journal_number']}")
        if row.get('photos_available') is False:
            value += '\nSaved photos could not be checked; the photo count is unknown.'
        elif 'photo_count' in row:
            value += '\nAssociated saved photos: ' + str(row['photo_count'])
        photos = row.get('photos') or []
        if photos:
            value += '\nPhoto observations are separate from the reported trade facts above.'
        for index, photo in enumerate(photos, 1):
            value += f'\n\nSaved photo {index} of {len(photos)} · ' + photo['id']
            for key in ('created_at','asset','play','entry_model','tier','analysis'):
                if photo.get(key) is not None and photo.get(key) != '':
                    label = ('Photo analysis (image-derived; execution details unconfirmed)'
                             if key == 'analysis' else 'Photo ' + key.replace('_',' '))
                    value += '\n' + label + ': ' + _display_value(photo[key])
        output.extend(_record_chunks(value, title))
    if result['has_more']:
        output.append('More records are available. Ask for the next page of your journal.')
    return [chunk for content in output for chunk in _text_chunks(content)]


def member_context_lines(db, guild_id, user_id, limit=5):
    """Compact same-conversation context uses the exact same grouped identity."""
    result = history(db,guild_id,user_id,{'limit':limit})
    output = []
    for row in result['journals']:
        label = (f"Legacy journal #{row['legacy_journal_number']}" if row.get('is_legacy')
                 else f"Trade #{row['trade_number']} journal")
        line = f"{label}: {str(row.get('summary') or 'Historical entries retained')[:350]}; result {result_text(row.get('result_r'))}; adherence {str(row.get('rule_adherence') or 'unknown')[:100]}"
        if row.get('update_count'):
            line += f"; {row['update_count']} saved execution/management/journal updates"
        if row.get('legacy_history_count'):
            line += f"; {row['legacy_history_count']} preserved historical entries"
        if row.get('needs_clarification'):
            line += '; conflicting historical fields need clarification'
        output.append(line)
    return output


def send_history(db, guild_id, user_id, args):
    args = {**args,'view':'detail'}  # An index must never replace the requested complete bundle.
    from gbop_voice_web.delivery_receipts import deliver, bounded_delivery_db
    try:
        result = history(bounded_delivery_db(db), guild_id, user_id, args,
                         include_photo_bytes=args.get('include_photos') is not False)
    except Exception:
        return {'ok': False, 'sent_count': 0, 'error': 'The selected journal could not be retrieved. No messages were sent.'}
    if not result.get('ok'):
        return result
    # The receipt is bound to the immutable identities and selected content,
    # not a moving "latest" query or re-resolved per-page display number.
    def snapshot(value):
        if isinstance(value, dict):
            return {k: snapshot(v) for k, v in value.items() if k not in
                    ('journal_number','trade_number','trade_id','legacy_journal_number')}
        return [snapshot(v) for v in value] if isinstance(value, list) else value
    fingerprint = {'records': snapshot(result['journals']),
                   **{k: result.get(k) for k in ('metadata_available','timeline_available','photos_available')}}
    scope = [j.get('record_key') or [j.get('journal_id'), j.get('trade_number'), j.get('legacy_journal_number')]
             for j in result['journals']]
    args = {**args,
            '_bundle_scope': sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
            '_bundle_fingerprint': sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()}
    return deliver(db, guild_id, user_id, 'send_journal_history', args,
        lambda operation: _send_history(bounded_delivery_db(db), guild_id, user_id, args, operation, result))


def _send_history(db, guild_id, user_id, args, operation, result):
    import httpx
    import base64
    from gbop_voice_web.photo_recall import recall_cards
    include_photos = args.get('include_photos') is not False
    result = dict(result)
    operation.state.update({key: result[key] for key in ('journal_count', 'trade_count',
        'open_trade_count', 'closed_trade_count', 'has_more', 'next_offset')})
    photos = result.pop('_delivery_photos', [])
    facts = {key: value for key, value in result.items() if key not in
             ('journals','context_text','has_more_details','next_detail_offset')}
    facts.update(include_photos=include_photos,
        journal_numbers=[j['journal_number'] for j in result['journals'] if j.get('journal_number') is not None],
        legacy_journal_numbers=[j['legacy_journal_number'] for j in result['journals'] if j.get('legacy_journal_number') is not None])
    operation.state.update({key: value for key, value in facts.items()
                           if key in ('include_photos','photo_count','journal_numbers','legacy_journal_numbers')})
    operation.state.update(text_sent_count=0, photo_sent_count=0)
    token = os.getenv('DISCORD_TOKEN', '')
    if not token:
        return operation.finish({**facts, 'journals': result['journals'], 'ok': False,
            'error': 'Journal retrieved; Discord delivery is not configured.'})
    with httpx.Client(base_url='https://discord.com/api/v10', headers={'Authorization': 'Bot ' + token}, timeout=20) as client:
        channel = client.post('/users/@me/channels', json={'recipient_id': str(user_id)})
        if channel.status_code >= 300:
            return operation.finish({**facts, 'ok': False,
                'error': 'Journal retrieved but your DMs could not be opened. Check Discord privacy settings.'})
        channel_id = channel.json()['id']
        for content in messages(result):
            operation.before_send()
            response = client.post(f'/channels/{channel_id}/messages',
                json={'content': content, 'allowed_mentions': {'parse': []}})
            if response.status_code >= 300:
                operation.rejected(response)
                return operation.finish({**facts, 'ok': False,
                    'error': 'Discord could not deliver the entire journal; confirmed message count is in sent_count.'})
            operation.accepted(response, component='text')
        for photo, filename, payload in recall_cards(photos):
            data = base64.b64decode(photo['image_base64'], validate=True)
            operation.before_send()
            response = client.post(f'/channels/{channel_id}/messages',
                data={'payload_json': json.dumps(payload)},
                files={'files[0]': (filename, data, photo['mime'])})
            if response.status_code >= 300:
                operation.rejected(response)
                return operation.finish({**facts, 'ok': False,
                    'error': 'The journal bundle was only partly delivered. Check text_sent_count and photo_sent_count before requesting a resend.'})
            operation.accepted(response, component='photo')
    # Full text was delivered privately; only transport facts enter the receipt.
    complete = (result.get('metadata_available', True) and result.get('timeline_available', True)
                and (not include_photos or result.get('photos_available', True)))
    if not complete:
        return operation.finish({**facts, 'ok': False,
            'error': 'Available journal context was delivered, but some associated records could not be retrieved. Missing content is unknown.'})
    return operation.finish(facts)


RECALL_SELECTORS = {
    'view': {'type':['string','null'],'enum':['index','detail',None],
             'description':'index lists all owned trade numbers and standalone legacy journals with pagination, no identifiers needed. detail reads full context. Use detail for sends.'},
    'latest': {'type': ['string', 'null'], 'enum': ['trade', 'journal', None],
               'description': 'Last trade uses reported trade date; last journal uses saved time. Never substitute one for the other.'},
    'date_basis': {'type': ['string', 'null'], 'enum': ['trade', 'saved', None]},
    'detail_offset': {'type': ['integer', 'null'], 'description': 'Character cursor for full read-only context; use next_detail_offset with the exact selected record.'},
}


def bind_recall_intent(name, args, text):
    """Keep explicit current text intent from being flattened by model selectors."""
    if name not in ('get_journal_history', 'send_journal_history', 'send_trade_photos') or not text:
        return args, None
    # A compound request can ask for an exact record and the complete index.
    # Do not narrow its index read using the other clause's record selectors.
    if name == 'get_journal_history' and args.get('view') == 'index':
        return args, None
    text = text.casefold()
    send = re.search(r"(?:^|[.!?;]\s*|\bplease\s+|\bi (?:want|need) you to\s+)"
                     r"(?:(?:can|could|would|will) you (?:please )?)?(?:please )?"
                     r"(?:send|dm|message|deliver|share|resend|re-send)\b", text)
    negated = re.search(r"\b(?:don't|do not|without|no need to|not asking you to)\b.{0,35}\b(?:send|dm|message|deliver|share|resend)\b", text)
    show_photos = (name == 'send_trade_photos' and re.search(
        r'^(?:(?:can|could|would|will) you )?(?:please )?show\b.{0,60}\b(?:photos|pictures|images)\b', text))
    if name.startswith('send_') and (not (send or show_photos) or negated):
        return args, {'ok': False, 'next_tool': 'get_delivery_status' if re.search(r'\b(?:send|sent|delivered|waiting)\b', text) else 'get_journal_history',
                      'error': 'This request does not authorize a private send. Read journal context or the existing delivery receipt.'}
    latest = re.search(r'\b(?:last|latest|most recent)\s+(?:(saved|recorded|uploaded)\s+)?(trade|journal(?: entry)?)\b', text)
    numbered = re.search(r'\b(?:trade|journal)\s*(?:#|number)\s*\d+', text)
    exact = re.search(r'\b(legacy journal|trade|journal)\s*(?:#|number)\s*(\d+)\b', text)
    if exact and name != 'send_trade_photos':
        field = {'trade': 'trade_number', 'journal': 'journal_number', 'legacy journal': 'legacy_journal_number'}[exact[1]]
        args = {**args, 'latest': None, 'trade_number': None, 'journal_number': None,
                'legacy_journal_number': None, field: int(exact[2]), 'offset': 0}
    if latest and not numbered and not args.get('detail_offset'):
        kind = 'trade' if latest[2] == 'trade' else 'journal'
        if name == 'send_trade_photos':
            return args, {'ok': False, 'error': 'Resolve the requested latest record with get_journal_history first, then use send_journal_history for its complete bundle.'}
        args = {**args, 'latest': kind, 'date_basis': 'saved' if latest[1] or kind == 'journal' else 'trade',
                'trade_number': None, 'journal_number': None, 'legacy_journal_number': None, 'offset': 0}
    if name == 'send_journal_history':
        text_only = re.search(r'\b(?:text[- ]only|without (?:the )?(?:photos|pictures|images)|no (?:photos|pictures|images))\b', text)
        args = {**args, 'view':'detail', 'include_photos': not bool(text_only)}
    return args, None


def run_recall(context, name, arguments, runner, generation):
    """Bind all reads/pages/send steps in a turn to the resolved owned identity."""
    from gbop_voice_web.delivery_receipts import run_delivery
    args = {k: v for k, v in arguments.items() if not k.startswith('_')}
    signature = {k: args.get(k) for k in ('latest','date_basis','trade_number','journal_number','legacy_journal_number')}
    identity = (tuple(context.owner or ()), context.session_id, generation)
    with context._lock:
        previous = getattr(context, '_recall_binding', None)
        if previous and previous['identity'] == identity and (
                previous['signature'] == signature or args.get('detail_offset')):
            args.update(_record_key=previous['record_key'], latest=None, trade_number=None,
                        journal_number=None, legacy_journal_number=None, offset=0)
            signature = previous['signature']
    if name == 'send_journal_history':
        # run_delivery strips untrusted internal arguments before admitting a
        # call. This closure restores only our current, server-bound identity.
        key = args.pop('_record_key', None)
        return run_delivery(context, name, args,
            lambda tool, values: runner(tool, {**values, **({'_record_key': key} if key else {})}),
            generation=generation)
    result = runner(name, args)
    with context._lock:
        if not context.current(generation):
            return {'ok': False, 'error': 'This journal request is no longer current.'}
        records = result.get('journals') or []
        if result.get('ok') and len(records) == 1 and records[0].get('record_key') and (
                any(signature.get(k) is not None for k in ('latest','trade_number','journal_number','legacy_journal_number'))
                or args.get('_record_key')):
            context._recall_binding = {'identity': identity, 'signature': signature,
                                       'record_key': records[0]['record_key']}
    return result


JOURNAL_RECALL_TOOLS = [schema('send_journal_history',
    'On an explicit send request, privately deliver the selected trade/journal complete bundle: execution and update history, notes, saved feelings, SELF grade and ALL associated photos. Photos are included by default; include_photos=false only for explicit text-only. Latest trade and latest journal are distinct; bind exact record selectors. No recipient override. Report text_sent_count and photo_sent_count; partial is not complete. Default send_or_recover; resend only when explicitly asked.',
    {'limit': {'type': ['integer', 'null']}, 'offset': {'type': ['integer', 'null']},
     'trade_number': {'type': ['integer', 'null']}, 'journal_number': {'type': ['integer', 'null']},
     'legacy_journal_number': {'type': ['integer', 'null']}, **{k:v for k,v in RECALL_SELECTORS.items() if k != 'view'},
     'include_photos': {'type': ['boolean', 'null']}, 'delivery_action': DELIVERY_ACTION})]
JOURNAL_RECALL_PROMPT = """
JOURNAL: Trade # bundles notes, executions, feelings, SELF grade, photos.
List IDs: get_journal_history view=index, no selectors; paginate next_offset
while has_more. No IDs needed. Standalone Legacy journals stay separate.
Answers: view=detail; follow context_text/next_detail_offset; no DM.
Latest trade: latest=trade,date_basis=trade; unknown dates need clarification.
Latest saved trade: date_basis=saved. Latest journal: latest=journal.
Saved time isn't trade time. Clarify ambiguous Legacy aliases.
Send: send_journal_history; all scoped photos, include_photos=false only for
explicit text-only. No account-wide photo send. Report text_sent_count and
photo_sent_count. Errors aren't empty. get_trade_state is OPEN only.
""".strip()
