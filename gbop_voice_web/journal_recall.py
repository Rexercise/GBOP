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


def _saved_key(value):
    """Compare logging timestamps as instants, without treating them as trade dates."""
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return float('-inf')


def _last_saved(values):
    return max((str(value) for value in values if _saved_key(value) != float('-inf')),
               key=_saved_key, default=None)


def _unfinished_drafts(conn, guild_id, user_id, trades, journals):
    """Read existing owner-scoped drafts; never initialize, finalize or resurrect one."""
    from gbop_voice_web import journal_drafts
    if not journal_drafts.available(conn):
        return []
    links = {row['id']: dict(row) for row in conn.execute(
        "SELECT id,journal_id,thesis_id FROM journal_story_drafts WHERE guild_id=? AND user_id=? AND status='unfinished'",
        (guild_id, user_id)).fetchall()}
    own_trades = {row['id'] for row in trades}
    own_journals = {row['id']: row for row in journals}
    result = []
    for draft in journal_drafts.read(conn, guild_id, user_id):
        link = links.get(draft['id'])
        if not link:
            continue
        jid, tid = link['journal_id'], link['thesis_id']
        if ((jid is not None and jid not in own_journals)
                or (tid is not None and tid not in own_trades)
                or (jid is not None and tid is not None and own_journals[jid]['thesis_id'] != tid)):
            continue  # Deleted/foreign links cannot resurrect a stale narrative.
        selected = draft.get('selected_key')
        if selected and tuple(selected) != (jid, tid):
            continue
        if tid is None and jid is not None and own_journals[jid]['thesis_id'] in own_trades:
            tid = own_journals[jid]['thesis_id']
        values = draft.get('values') or {}
        content = journal_drafts.substantive_content(draft)
        raw = journal_drafts.substantive_raw_passages(draft)
        if not content['values'] and not content['narration']:
            continue
        # updated_at also advances for pause/permission state. Older drafts have
        # no dedicated substantive stamp, so use their saved content evidence.
        corrected_at = []
        for correction in draft.get('corrections') or []:
            if not isinstance(correction, dict) or not isinstance(correction.get('fields'), dict):
                continue
            projections = [journal_drafts.substantive_content({'values': {
                key: change.get(side) for key, change in correction['fields'].items() if isinstance(change, dict)},
                'provenance': draft.get('provenance') or {}}) for side in ('before','after')]
            if projections[0] != projections[1]:
                corrected_at.append(correction.get('recorded_at'))
        saved_at = draft.get('substantive_updated_at') or _last_saved(
            [draft.get('created_at')] + [part.get('recorded_at') for part in raw]
            + corrected_at)
        result.append(dict(draft_id=draft['id'], draft_status='unfinished',
            title=values.get('title'), story=values,
            raw_story=[part for part in draft.get('raw_story') or []
                       if isinstance(part,dict) and isinstance(part.get('text'),str)],
            provenance=draft.get('provenance') or {}, saved_at=saved_at,
            _thesis_id=tid, _journal_id=jid))
    return result


def _trade_timelines(conn, guild_id, user_id, owned_theses, journals, ignored_saved_times=None):
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
            elif (row.get('event') in ('journal_canonical_v1', 'journal_source_v1', 'execution_receipt_v1',
                    'journal_read_v1', 'journal_view_v1', 'journal_access_v1', 'journal_receipt_v1')
                  or str(row.get('event') or '').startswith('journal_execution_v1:')):
                continue
            elif row.get('event') == 'journal_context_v1':
                # Opening-time context can contain the only surviving member
                # report. Preserve that report, but not automatic market snapshots
                # as new journal activity. Arbitrary member event labels such as
                # "delivery" below remain actual trading notes, never receipts.
                metadata = _metadata(row.get('details'))
                reported = {key:metadata[key] for key in
                    ('reported_entry_at','reported_exit_at','reported_outcome')
                    if metadata.get(key) is not None}
                if not reported:
                    continue
                item.update(kind='trade_event',event='Member-reported trade context',details=reported)
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
                    'labels', 'kind', 'adherence', 'reported_entry_at', 'reported_exit_at', 'reported_entry_time_text', 'reported_exit_time_text', 'time_zone', 'title', 'reported_outcome', 'self_grade', 'feeling_history', 'journal_story', 'source_attachments')
                for key in fields:
                    if old_meta.get(key) != new_meta.get(key):
                        changes['metadata.' + key] = {'before': old_meta.get(key), 'after': new_meta.get(key)}
                old_thesis, new_thesis = before.get('thesis') or {}, after.get('thesis') or {}
                for key in ('asset','direction','play','session','objective','thesis_invalidation','status','max_r','final_result_r','close_note'):
                    if old_thesis.get(key) != new_thesis.get(key):
                        changes['trade.' + key] = {'before': old_thesis.get(key), 'after': new_thesis.get(key)}
                if not changes:
                    if ignored_saved_times is not None:
                        old_time = (before.get('details') or {}).get('updated_at')
                        new_time = (after.get('details') or {}).get('updated_at')
                        if new_time and new_time != old_time:
                            ignored_saved_times[(journal['id'], new_time)] = old_time or journal.get('created_at')
                    continue
                item.update(kind='journal_update', changes=changes)
            else:
                item.update(kind='trade_event', event=row.get('event'),
                            details=row.get('details'), result_r=row.get('result_r'))
            timelines[tid].append(item)
    for items in timelines.values():
        items.sort(key=lambda item:_saved_key(item.get('created_at')))
    return timelines, available


def history(db, guild_id, user_id, args, *, include_photo_bytes=False, allow_recorded_recall=True):
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
            ('latest','trade_number','journal_number','legacy_journal_number','draft_id','detail_offset','date_basis')):
        return {'ok':False,'status':'index_selectors_conflict',
                'error':'Index lists all record numbers with limit/offset only. Use view=detail for exact or latest records and date selectors.'}
    limit = max(1, min(int(args.get('limit') or 5), 20))
    offset = max(0, int(args.get('offset') or 0))
    latest = args.get('latest')
    latest_recorded = bool(allow_recorded_recall and latest and args.get('date_basis') is None)
    basis = args.get('date_basis') or ('trade' if latest == 'trade' else 'saved')
    if latest_recorded:
        basis = 'saved'
    if latest not in (None, 'trade', 'journal') or basis not in ('saved', 'trade'):
        return {'ok': False, 'error': 'Choose latest trade or journal, and saved or trade date.'}
    if latest and (offset or any(args.get(k) is not None for k in ('trade_number','journal_number','legacy_journal_number','draft_id'))):
        return {'ok': False, 'error': 'Use either an exact record number or latest, without a record offset.'}
    draft_id = args.get('draft_id')
    if str(args.get('_record_key') or '').startswith('draft:'):
        draft_id = args['_record_key'].partition(':')[2]
    if draft_id and any(args.get(k) is not None for k in ('trade_number','journal_number','legacy_journal_number')):
        return {'ok': False, 'error': 'Use either a draft_id or an exact record number.'}
    read_drafts = not index_view and allow_recorded_recall and (latest_recorded or draft_id or args.get('_include_unfinished'))
    drafts, drafts_available, ignored_saved_times = [], True, {}
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
        if args.get('_record_key') and not draft_id:
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
        timelines, timeline_available = ({tid:[] for tid in trade_numbers},True) if index_view else _trade_timelines(conn,guild_id,user_id,trade_numbers,rows,ignored_saved_times)
        # Resolve all identities in bulk: remote DB round trips must not grow
        # linearly with every historical trade in a member's journal.
        canonical = {tid: None for tid in trade_numbers}
        for item in journal_display(conn,guild_id,user_id):
            if item['canonical']:
                canonical[item['thesis_id']] = item['id']
        if read_drafts:
            try:
                drafts = _unfinished_drafts(conn, guild_id, user_id, trades, rows)
            except Exception:
                drafts_available = False
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
        for jid, saved_at in details_saved_at.items():
            seen = set()
            while (jid, saved_at) in ignored_saved_times and saved_at not in seen:
                seen.add(saved_at)
                saved_at = ignored_saved_times[(jid, saved_at)]
            details_saved_at[jid] = saved_at
    except Exception:
        metadata_available = False
    legacy_numbers = {r['id']: n for n,r in enumerate(rows,1)}

    def record(row, *, legacy=False):
        metadata = metadata_by_id.get(row['id'], {})
        legacy_updates = [item.get('recorded_at') for item in metadata.get('legacy_audit') or [] if isinstance(item, dict)]
        saved_at = (_last_saved(legacy_updates) or row.get('created_at')
                    if row.get('thesis_id') not in trade_numbers else
                    details_saved_at.get(row['id']) or row.get('created_at'))
        return dict(journal_id=row['id'], journal_number=None if legacy else trade_numbers.get(row.get('thesis_id')),
            trade_id=trade_numbers.get(row.get('thesis_id')), trade_number=trade_numbers.get(row.get('thesis_id')),
            legacy_journal_number=legacy_numbers[row['id']] if legacy else None,
            is_legacy=legacy, result_r=row.get('result_r'), rule_adherence=row.get('rule_adherence'),
            summary=row.get('description'), study_note=row.get('study_note'), created_at=row.get('created_at'),
            metadata=metadata,
            saved_at=saved_at,
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
    # Only the latest-information read includes unfinished narratives. Link by
    # immutable owned IDs, preserving the complete existing trade bundle.
    standalone_drafts = []
    for draft in drafts:
        tid, jid = draft.pop('_thesis_id'), draft.pop('_journal_id')
        linked = next((row for row in journals if (tid is not None and row.get('_thesis_id') == tid)
                       or (tid is None and jid is not None and row.get('journal_id') == jid)), None)
        if linked is not None and not draft_id:
            linked.setdefault('unfinished_journals', []).append(draft)
        else:
            standalone_drafts.append(dict(journal_id=None, journal_number=None, trade_id=None,
                trade_number=None, legacy_journal_number=None, is_legacy=False,
                record_kind='unfinished_journal', draft_id=draft['draft_id'], draft_status='unfinished',
                summary=draft.get('title') or 'Unfinished journal',
                metadata={**draft['story'], 'story_provenance': draft['provenance']}, result_r=None,
                saved_at=draft['saved_at'], created_at=None, unfinished_journals=[draft],
                legacy_history=[], legacy_history_count=0, updates=[], update_count=0,
                _sort_id=draft['draft_id'], _thesis_id=None, _journal_ids=[]))
    recorded_count = len(journals)
    journals.extend(standalone_drafts)
    for view in journals:
        times = [view.get('created_at'), view.get('saved_at'), (view.get('trade_facts') or {}).get('created_at'),
                 (view.get('trade_facts') or {}).get('closed_at')]
        times += [r.get('created_at') for r in view.get('legacy_history') or []]
        times += [r.get('saved_at') for r in view.get('legacy_history') or []]
        times += [r.get('created_at') for r in view.get('updates') or []]
        times += [r.get('saved_at') for r in view.get('unfinished_journals') or []]
        for record_view in [view] + (view.get('legacy_history') or []):
            meta = record_view.get('metadata') or {}
            times += [item.get('recorded_at') for item in meta.get('feeling_history') or [] if isinstance(item, dict)]
            if isinstance(meta.get('self_grade'), dict):
                times.append(meta['self_grade'].get('recorded_at'))
        view['saved_at'] = _last_saved(times)
        view['reported_trade_date'] = _reported_trade_date(view)
        view['_sort_key'] = (_saved_key(view['saved_at']), str(view.pop('_sort_id')))
    journals.sort(key=lambda r:r.pop('_sort_key'), reverse=True)
    if index_view:
        journals.sort(key=lambda r:(r['is_legacy'],r.get('trade_number') or r['legacy_journal_number']))
    total = recorded_count
    matching = journals
    if draft_id:
        matching = [row for row in standalone_drafts if row['draft_id'] == draft_id]
        if not matching:
            return {'ok': False, 'status': 'draft_unavailable',
                    'error': 'This unfinished journal is unavailable or was deleted. No records were changed.'}
    if selection is not None:
        if selection.get('explicit_legacy'):
            original = next(r for r in rows if r['id'] == selection['record_id'])
            # A pinned standalone note may already carry its linked unfinished
            # narrative. Rebuilding it here would silently drop that narrative
            # on continuation pages after the first latest-information read.
            view = next((row for row in journals if row.get('is_legacy')
                         and row.get('journal_id') == original['id']), None)
            if view is None:
                view = record(original, legacy=True)
                view.update(record_kind='legacy_journal',legacy_history=[],legacy_history_count=0,
                            canonical_record_exists=bool(selection.get('canonical')))
            view['reported_trade_date'] = _reported_trade_date(view)
            matching = [view]
        else:
            matching = [r for r in journals if not r.get('is_legacy') and r.get('trade_number') == selection['trade_number']]
    if latest == 'trade' and not latest_recorded:
        matching = [r for r in matching if not r.get('is_legacy')
                    and (r.get('trade_facts') or {}).get('status') in ('OPEN','CLOSED','JOURNALED')]
    if basis == 'trade':
        dated = [(r, _date_interval(r.get('reported_trade_date'), (r.get('metadata') or {}).get('time_zone'))) for r in matching]
        day_labels_only = bool(dated) and all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(r.get('reported_trade_date') or '')) for r, _ in dated)
        ambiguous = any(interval is None for _, interval in dated)
        if dated and not ambiguous:
            winner = max(range(len(dated)), key=lambda n: dated[n][1][0])
            ambiguous = any((r['reported_trade_date'] == dated[winner][0]['reported_trade_date'] if day_labels_only
                             else interval[1] >= dated[winner][1][0])
                            for n, (r, interval) in enumerate(dated) if n != winner)
        if latest and ambiguous:
            return {'ok': False, 'status': 'trade_date_ambiguous', 'needs_clarification': True,
                    'selection_basis': 'unknown_trade_chronology',
                    'selection_note': 'Actual trade chronology is unknown or overlapping. The latest saved record is only a separately labeled fallback.',
                    'latest_saved': ({
                        'label': 'Latest saved trade record' if latest == 'trade' else 'Latest saved journal record',
                        'trade_number': matching[0].get('trade_number'),
                        'legacy_journal_number': matching[0].get('legacy_journal_number'),
                        'saved_at': matching[0].get('saved_at'),
                        'is_latest_trade': False,
                        'detail_request': {'tool': 'get_journal_history', 'args': {
                            ('legacy_journal_number' if matching[0].get('is_legacy') else 'trade_number'):
                            matching[0].get('legacy_journal_number') if matching[0].get('is_legacy') else matching[0].get('trade_number')}}}
                        if matching else None),
                    'error': 'The actual latest trade cannot be established from the saved trade dates. '
                             'Ask which Trade #, or whether the member means the latest saved record.',
                    'candidates': [{'trade_number': r.get('trade_number'),
                                    'legacy_journal_number': r.get('legacy_journal_number'),
                                    'saved_at': r.get('saved_at'),
                                    'reported_trade_date': r.get('reported_trade_date')} for r in matching]}
        matching = sorted(matching, key=lambda r: _date_key(r.get('reported_trade_date'),
            None if day_labels_only else (r.get('metadata') or {}).get('time_zone')) or float('-inf'), reverse=True)
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
        row['record_key'] = ('draft:' + row['draft_id'] if row.get('record_kind') == 'unfinished_journal' else
                            'trade:' + str(row['_thesis_id']) if not row.get('is_legacy')
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
        selection_note=(('Latest by reported trade chronology.' if latest else 'Ordered by reported trade chronology; undated records have unknown chronology.') if basis == 'trade' else
                        ('Latest saved record; this does not establish the latest actual trade.' if latest else
                         'Ordered by saved/updated time; this does not establish when the trade occurred.')),
        journal_count=total, canonical_journal_count=len(trades),
        legacy_journal_count=sum(r['is_legacy'] for r in journals),
        preserved_legacy_history_count=preserved_count, stored_journal_entry_count=len(rows),
        trade_count=len(trades), open_trade_count=open_count,
        closed_trade_count=sum(r['status'] == 'CLOSED' for r in trades),
        matched_record_count=len(matching),
        has_more=False if latest else offset + len(selected) < len(matching), next_offset=offset + len(selected),
        identity_scope='Authenticated Discord account only; another login may have different records.',
        numbering_note='One Trade #N record includes its journal; Journal #N is an accepted alias. Separate historical entries use Legacy journal #N; ambiguous old journal numbers require clarification.')
    if read_drafts:
        result.update(drafts_available=drafts_available, unfinished_journal_count=len(drafts))
    if latest_recorded or args.get('_latest_recorded'):
        result.update(latest_recorded=True, is_latest_trade=False,
            selection_basis='saved', selection_note='Latest recorded information, with its linked context. Saved time does not establish actual trade chronology.')
        if selected and selected[0].get('record_kind') == 'trade_journal' and not selected[0].get('reported_trade_date'):
            result['selection_note'] += ' The trade date is unrecorded.'
        if not drafts_available or not metadata_available or not timeline_available:
            result['selection_note'] += ' Some saved history could not be checked; newer information may be missing.'
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
    if latest or selection is not None or draft_id:
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
                      next_detail_offset=end, context_version=sha256(full.encode('utf-8')).hexdigest())
    return result


def _date_key(value, timezone_name=None):
    interval = _date_interval(value, timezone_name)
    return interval[0] if interval else None


def _date_interval(value, timezone_name=None):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if len(str(value)) == 10:
            if timezone_name:
                parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
                return parsed.timestamp(), (parsed + timedelta(days=1)).timestamp()
            # A calendar label is not proof of a New York execution. For
            # mixed timestamp/day precision retain the full possible worldwide
            # interval; pure day labels can still be ordered as reported days.
            parsed = parsed.replace(tzinfo=timezone.utc)
            return (parsed - timedelta(hours=14)).timestamp(), (parsed + timedelta(days=1, hours=12)).timestamp()
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp(), parsed.timestamp()
    except (ValueError, TypeError, OverflowError, KeyError):
        return None


def _reported_trade_date(row):
    records = [row] if row.get('canonical_record_exists') else [row] + (row.get('legacy_history') or [])
    values = set()
    for record in records:
        meta = dict(record.get('metadata') or {})
        story = _metadata(meta.get('journal_story'))
        for key in ('trade_date', 'reported_entry_at', 'time_zone'):
            if not meta.get(key) and story.get(key):
                meta[key] = story[key]
        if not meta.get('reported_entry_at'):
            first = next((entry for entry in (story.get('entries') or []) if isinstance(entry, dict)
                          and entry.get('entry_index') == 1), {})
            if first.get('reported_entry_at'):
                meta['reported_entry_at'] = first['reported_entry_at']
        provenance = meta.get('provenance') or {}
        defaults = provenance.get('context_defaults') or []
        reported = provenance.get('member_reported') or []
        for key in ('reported_entry_at', 'trade_date'):
            value = meta.get(key)
            if (isinstance(value, str) and value and _date_interval(value, meta.get('time_zone')) is not None
                    and (key not in defaults or key in reported)):
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
                      ('reported_exit_at','Reported exit'),('reported_entry_time_text','Reported entry wording'),
                      ('reported_exit_time_text','Reported exit wording'),('time_zone','Reported timezone'),
                      ('title','Journal title'),('objective','Reported context objective'),('reported_outcome','Reported outcome'),
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
            story_provenance = meta.get('story_provenance')
            field_provenance = (story_provenance.get(key) or {}) if isinstance(story_provenance, dict) else {}
            if not isinstance(field_provenance, dict):field_provenance = {}
            if field_provenance.get('kind') == 'inferred':
                label = 'Inferred ' + label.removeprefix('Reported ').lower()
            elif field_provenance.get('source') == 'model_extracted_narration':
                label = 'Narration-derived ' + label.removeprefix('Reported ').lower()
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
        title = ('Unfinished journal' if row.get('record_kind') == 'unfinished_journal' else
                 f"Legacy journal #{row['legacy_journal_number']}" if row.get('is_legacy')
                 else f"Trade #{row.get('trade_number') or row.get('trade_id') or row['journal_number']} journal")
        if row.get('record_kind') == 'unfinished_journal':
            value = title + '\nSaved privately; unfinished and excluded from execution/performance records.'
        elif row.get('canonical_record_exists') is False and not row.get('is_legacy') and not row.get('virtual_trade_record'):
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
        for draft in row.get('unfinished_journals') or []:
            value += '\n\nUnfinished saved journal · ' + draft['draft_id']
            value += '\nLast substantive save: ' + str(draft.get('saved_at') or 'unknown')
            value += '\nMember narrative (unfinished; not execution proof):\n' + _display_value(draft['story'])
            if draft.get('provenance'):
                value += '\nNarrative provenance:\n' + _display_value(draft['provenance'])
            for passage in draft.get('raw_story') or []:
                value += '\n\nSaved narration [' + str(passage.get('recorded_at') or 'unknown') + ']:\n' + str(passage['text'])
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
    if args.get('draft_id') or str(args.get('_record_key') or '').startswith('draft:'):
        return {'ok': False, 'error': 'Unfinished journal recall is read-only; it does not authorize private delivery.'}
    args = {**args,'view':'detail'}  # An index must never replace the requested complete bundle.
    from gbop_voice_web.delivery_receipts import deliver, bounded_delivery_db
    try:
        result = history(bounded_delivery_db(db), guild_id, user_id, args,
                         include_photo_bytes=args.get('include_photos') is not False,
                         allow_recorded_recall=False)
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
               'description': 'Read: ordinary last trade or last journal uses either value with date_basis=null to return newest recorded information, including standalone notes and unfinished drafts with linked trade context. Send keeps exact existing scope.'},
    'date_basis': {'type': ['string', 'null'], 'enum': ['trade', 'saved', None],
                   'description': 'Read default null: newest substantive save across all record types; answer directly. trade: ONLY explicitly requested actual trade chronology; unknown dates need clarification. saved: explicitly limited latest saved trade/journal. Never invent dates.'},
    'draft_id': {'type': ['string', 'null'], 'description': 'Read-only exact unfinished journal ID from latest recall; use with detail_offset for its full context. No mutation or send authorization.'},
    'detail_offset': {'type': ['integer', 'null'], 'description': 'Character cursor for full read-only context; use next_detail_offset with the exact selected record.'},
}


def bind_recall_intent(name, args, text):
    """Keep explicit current text intent from being flattened by model selectors."""
    if name not in ('get_journal_history', 'send_journal_history', 'send_trade_photos') or not text:
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
    strict_date = re.search(r'\bchronolog(?:ical(?:ly)?|y)\b|\b(?:by|according to)\s+(?:the\s+)?(?:actual\s+)?(?:trade|execution)\s+(?:date|time)\b|\b(?:last|latest|most recent)\s+actual\s+trade\b|\bactual\s+(?:last|latest|most recent)\s+trade\b|\b(?:last|latest|most recent)\s+trade\s+by\s+date\b', text)
    chronology_latest = bool(strict_date and re.search(r'\b(?:last|latest|most recent)\b', text))
    recorded_latest = re.search(r'\b(?:last|latest|most recent)\s+(?:piece of\s+)?(?:recorded|saved)\s+(?:information|thing|note|entry)\b', text)
    # Preserve a separately requested complete index in compound requests, but
    # a model-selected index must not flatten an ordinary latest-information read.
    if name == 'get_journal_history' and args.get('view') == 'index' and not args.get('detail_offset'):
        index_requested = re.search(r'\bindex\b|\b(?:all|every)\b.{0,30}\b(?:trade|journal|record)s?\b|\b(?:list|show)\s+(?:my\s+)?(?:trades|journals|records)\b', text)
        if index_requested or not (latest or chronology_latest or recorded_latest):
            return args, None
    numbered = re.search(r'\b(?:trade|journal)\s*(?:#|number)\s*\d+', text)
    exact = re.search(r'\b(legacy journal|trade|journal)\s*(?:#|number)\s*(\d+)\b', text)
    if exact and name != 'send_trade_photos':
        field = {'trade': 'trade_number', 'journal': 'journal_number', 'legacy journal': 'legacy_journal_number'}[exact[1]]
        args = {**args, 'latest': None, 'trade_number': None, 'journal_number': None,
                'legacy_journal_number': None, 'draft_id': None, field: int(exact[2]), 'offset': 0}
    if (latest or chronology_latest or recorded_latest) and not numbered and not args.get('detail_offset'):
        kind = 'trade' if chronology_latest or latest and latest[2] == 'trade' else 'journal'
        if name == 'send_trade_photos':
            return args, {'ok': False, 'error': 'Resolve the requested latest record with get_journal_history first, then use send_journal_history for its complete bundle.'}
        basis = ('trade' if chronology_latest else None) if name == 'get_journal_history' and not send else ('saved' if latest and latest[1] or kind == 'journal' else 'trade')
        args = {**args, 'latest': kind, 'date_basis': basis,
                'trade_number': None, 'journal_number': None, 'legacy_journal_number': None, 'draft_id': None, 'offset': 0,
                'view': 'detail'}
    if name == 'get_journal_history' and args.get('detail_offset'):
        args = {**args, 'view': 'detail'}
    if name == 'send_journal_history':
        text_only = re.search(r'\b(?:text[- ]only|without (?:the )?(?:photos|pictures|images)|no (?:photos|pictures|images))\b', text)
        args = {**args, 'view':'detail', 'include_photos': not bool(text_only)}
    return args, None


def run_recall(context, name, arguments, runner, generation):
    """Bind all reads/pages/send steps in a turn to the resolved owned identity."""
    from gbop_voice_web.delivery_receipts import run_delivery
    args = {k: v for k, v in arguments.items() if not k.startswith('_')}
    signature = {k: args.get(k) for k in ('latest','date_basis','trade_number','journal_number','legacy_journal_number','draft_id')}
    identity = (tuple(context.owner or ()), context.session_id, generation)
    with context._lock:
        previous = getattr(context, '_recall_binding', None)
        if previous and previous['identity'] == identity and (
                previous['signature'] == signature or args.get('detail_offset')):
            if name == 'send_journal_history' and previous.get('read_only_selection'):
                return {'ok': False, 'error': 'Latest-information recall is read-only. Select an exact saved trade or journal for an explicitly requested send.'}
            args.update(_record_key=previous['record_key'], latest=None, trade_number=None,
                        journal_number=None, legacy_journal_number=None, draft_id=None, offset=0)
            if previous.get('read_only_selection'):
                args.update(_include_unfinished=True, _latest_recorded=True)
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
        if (result.get('ok') and args.get('detail_offset') and previous
                and previous['identity'] == identity and args.get('_record_key') == previous['record_key']
                and previous.get('context_version') and result.get('context_version') != previous['context_version']):
            return {'ok': False, 'status': 'recall_content_changed', 'restart_details': True,
                    'journals': [{key:row[key] for key in ('record_key','trade_number','legacy_journal_number','draft_id') if key in row}
                                 for row in records],
                    'error': 'This saved information or its display labels changed while reading its pages. Restart the same selected record at detail_offset=0; do not combine the old and new text.'}
        if result.get('ok') and len(records) == 1 and records[0].get('record_key') and (
                any(signature.get(k) is not None for k in ('latest','trade_number','journal_number','legacy_journal_number','draft_id'))
                or args.get('_record_key')):
            context._recall_binding = {'identity': identity, 'signature': signature,
                                       'record_key': records[0]['record_key'],
                                       'context_version': result.get('context_version'),
                                       'read_only_selection': bool(result.get('latest_recorded') or records[0].get('draft_id'))}
    return result


JOURNAL_RECALL_TOOLS = [schema('send_journal_history',
    'On an explicit send request, privately deliver the selected trade/journal complete bundle: execution and update history, notes, saved feelings, SELF grade and ALL associated photos. Photos are included by default; include_photos=false only for explicit text-only. Latest trade and latest journal are distinct; bind exact record selectors. No recipient override. Report text_sent_count and photo_sent_count; partial is not complete. Default send_or_recover; resend only when explicitly asked.',
    {'limit': {'type': ['integer', 'null']}, 'offset': {'type': ['integer', 'null']},
     'trade_number': {'type': ['integer', 'null']}, 'journal_number': {'type': ['integer', 'null']},
     'legacy_journal_number': {'type': ['integer', 'null']}, **{k:v for k,v in RECALL_SELECTORS.items() if k not in ('view','draft_id')},
     'include_photos': {'type': ['boolean', 'null']}, 'delivery_action': DELIVERY_ACTION})]
JOURNAL_RECALL_PROMPT = """
JOURNAL: Trade # bundles notes, executions, feelings, SELF grade, photos.
List IDs: get_journal_history view=index, no selectors; paginate next_offset
while has_more. No IDs needed. Standalone Legacy journals stay separate.
Answers: view=detail; follow context_text/next_detail_offset; no DM.
Ordinary last trade/journal: latest=trade or journal,date_basis=null. Answer the
newest substantive recorded information directly, including standalone notes or
unfinished drafts; prefer its linked execution context, never an older trade.
Briefly caveat unknown trade dates. No permission/type-selection question.
Only explicit latest by trade date: date_basis=trade; ambiguity stays unknown.
Saved time isn't trade time. Recall selects no mutation or DM. Drafts stay
unfinished, outside performance. Clarify ambiguous Legacy aliases.
Send: send_journal_history; all scoped photos, include_photos=false only for
explicit text-only. No account-wide photo send. Report text_sent_count and
photo_sent_count. Errors aren't empty. get_trade_state is OPEN only.
""".strip()
