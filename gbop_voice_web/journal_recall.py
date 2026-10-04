"""Member-scoped journal recall and explicit private delivery, without AI writes."""
import os
import json
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
                    'labels', 'kind', 'adherence', 'reported_entry_at', 'reported_exit_at', 'reported_outcome', 'self_grade')
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


def history(db, guild_id, user_id, args):
    """One read-only member view per owned trade, plus explicitly legacy rows.

    Older duplicate journals are grouped without rewriting any stored row. Until
    a canonical journal exists, only uncontested fields are projected; all of the
    original text and metadata stays available in legacy_history.
    """
    from gbop_voice_web.journal_numbers import journal_display
    limit = max(1, min(int(args.get('limit') or 5), 20))
    offset = max(0, int(args.get('offset') or 0))
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
        trade_numbers = {r['id']: n for n, r in enumerate(trades, 1)}
        timelines, timeline_available = _trade_timelines(conn,guild_id,user_id,trade_numbers,rows)
        # Resolve all identities in bulk: remote DB round trips must not grow
        # linearly with every historical trade in a member's journal.
        canonical = {tid: None for tid in trade_numbers}
        for item in journal_display(conn,guild_id,user_id):
            if item['canonical']:
                canonical[item['thesis_id']] = item['id']
    metadata_by_id = {}
    metadata_available = True
    try:
        with db() as conn:
            details = conn.execute('''SELECT d.journal_id,d.metadata FROM journal_details d
                JOIN journals j ON j.id=d.journal_id AND j.guild_id=d.guild_id AND j.user_id=d.user_id
                WHERE d.guild_id=? AND d.user_id=?''', (guild_id, user_id)).fetchall()
        metadata_by_id = {r['journal_id']: _metadata(r['metadata']) for r in details}
    except Exception:
        metadata_available = False
    legacy_numbers = {r['id']: n for n,r in enumerate(rows,1)}

    def record(row, *, legacy=False):
        return dict(journal_id=row['id'], journal_number=None if legacy else trade_numbers.get(row.get('thesis_id')),
            trade_id=trade_numbers.get(row.get('thesis_id')), trade_number=trade_numbers.get(row.get('thesis_id')),
            legacy_journal_number=legacy_numbers[row['id']] if legacy else None,
            is_legacy=legacy, result_r=row.get('result_r'), rule_adherence=row.get('rule_adherence'),
            summary=row.get('description'), study_note=row.get('study_note'), created_at=row.get('created_at'),
            metadata=metadata_by_id.get(row['id'], {}))

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
                trade_facts=facts, updates=timelines[tid], update_count=len(timelines[tid]), _sort_id=tid)
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
            _sort_id=max(r['id'] for r in entries))
        journals.append(view)
    for view in journals:
        times = [view.get('created_at'), (view.get('trade_facts') or {}).get('created_at'),
                 (view.get('trade_facts') or {}).get('closed_at')]
        times += [r.get('created_at') for r in view.get('legacy_history') or []]
        times += [r.get('created_at') for r in view.get('updates') or []]
        view['_sort_key'] = (max((str(t) for t in times if t is not None), default=''), view.pop('_sort_id'))
    journals.sort(key=lambda r:r.pop('_sort_key'), reverse=True)
    total = len(journals)
    matching = journals
    if selection is not None:
        if selection.get('explicit_legacy'):
            original = next(r for r in rows if r['id'] == selection['record_id'])
            view = record(original, legacy=True)
            view.update(record_kind='legacy_journal',legacy_history=[],legacy_history_count=0,
                        canonical_record_exists=bool(selection.get('canonical')))
            matching = [view]
        else:
            matching = [r for r in journals if not r.get('is_legacy') and r.get('trade_number') == selection['trade_number']]
    selected = matching[offset:offset+limit]
    open_count = sum(r['status'] == 'OPEN' for r in trades)
    return dict(ok=True, journals=selected, metadata_available=metadata_available, timeline_available=timeline_available,
        journal_count=total, canonical_journal_count=len(trades),
        legacy_journal_count=sum(r['is_legacy'] for r in journals),
        preserved_legacy_history_count=preserved_count, stored_journal_entry_count=len(rows),
        trade_count=len(trades), open_trade_count=open_count,
        closed_trade_count=sum(r['status'] == 'CLOSED' for r in trades),
        matched_record_count=len(matching),
        has_more=offset + len(selected) < len(matching), next_offset=offset + len(selected),
        identity_scope='Authenticated Discord account only; another login may have different records.',
        numbering_note='One Trade #N record includes its journal; Journal #N is an accepted alias. Separate historical entries use Legacy journal #N; ambiguous old journal numbers require clarification.')


def _record_text(row, title):
    value = (title + f"\nResult: {result_text(row.get('result_r'))}"
             + '\nEntry: ' + str(row.get('summary') or 'Not specified')
             + '\nAdherence: ' + str(row.get('rule_adherence') or 'Not specified')
             + '\nStudy note: ' + str(row.get('study_note') or 'Not specified'))
    meta = row.get('metadata') or {}
    for key,label in (('reported_entry_at','Reported entry'),('reported_exit_at','Reported exit'),
                      ('reported_outcome','Reported outcome')):
        if meta.get(key):
            value += '\n' + label + ': ' + str(meta[key])
    if meta.get('emotion'):
        value += '\nLegacy feeling note (stage/time unspecified): ' + str(meta['emotion'])
    for report in meta.get('feeling_history') or []:
        value += ('\nFeeling #' + str(report['id']) + ' [' + report['stage'] + ']: ' + report['feeling']
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


def messages(result):
    header = (f"Your GBOP journal — {result['journal_count']} journal records; "
              f"{result['trade_count']} trade records ({result['open_trade_count']} open).")
    if result.get('legacy_journal_count') or result.get('preserved_legacy_history_count'):
        header += ' Original legacy entries are preserved and labeled separately.'
    output = [header]
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
            value += '\nTrade details: ' + ' · '.join(str(k).replace('_',' ') + ': ' + str(v) for k,v in facts.items())
        for update in row.get('updates') or []:
            value += '\n\nSaved update (' + str(update.get('created_at') or 'logging time unavailable') + '): '
            if update.get('kind') == 'journal_update':
                for key,change in (update.get('changes') or {}).items():
                    value += '\n' + key.replace('metadata.','').replace('_',' ') + ': '
                    value += str(change.get('before') if change.get('before') is not None else 'Not recorded')
                    value += ' → ' + str(change.get('after') if change.get('after') is not None else 'Not recorded')
            else:
                value += ' · '.join(str(k).replace('_',' ') + ': ' + str(v)
                    for k,v in update.items() if k not in ('created_at',) and v is not None)
        # Deliver every saved source entry in bounded transport messages. The
        # bounded model-facing preview must never become the DM source of truth.
        for prior in row.get('legacy_history') or []:
            value += '\n\n' + _record_text(prior, f"Preserved legacy journal #{prior['legacy_journal_number']}")
        output.extend(value[n:n+1800] for n in range(0, len(value), 1800))
    if result['has_more']:
        output.append('More records are available. Ask for the next page of your journal.')
    return output


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
    from gbop_voice_web.delivery_receipts import deliver, bounded_delivery_db
    return deliver(db, guild_id, user_id, 'send_journal_history', args,
        lambda operation: _send_history(bounded_delivery_db(db), guild_id, user_id, args, operation))


def _send_history(db, guild_id, user_id, args, operation):
    import httpx
    result = history(db, guild_id, user_id, args)
    if not result.get('ok'):
        return operation.finish(result)
    operation.state.update({key: result[key] for key in ('journal_count', 'trade_count',
        'open_trade_count', 'closed_trade_count', 'has_more', 'next_offset')})
    token = os.getenv('DISCORD_TOKEN', '')
    if not token:
        return operation.finish({**result, 'ok': False,
            'error': 'Journal retrieved; Discord delivery is not configured.'})
    with httpx.Client(base_url='https://discord.com/api/v10', headers={'Authorization': 'Bot ' + token}, timeout=20) as client:
        channel = client.post('/users/@me/channels', json={'recipient_id': str(user_id)})
        if channel.status_code >= 300:
            return operation.finish({**result, 'ok': False,
                'error': 'Journal retrieved but your DMs could not be opened. Check Discord privacy settings.'})
        channel_id = channel.json()['id']
        for content in messages(result):
            operation.before_send()
            response = client.post(f'/channels/{channel_id}/messages',
                json={'content': content, 'allowed_mentions': {'parse': []}})
            if response.status_code >= 300:
                operation.rejected(response)
                return operation.finish({**result, 'ok': False,
                    'error': 'Discord could not deliver the entire journal; confirmed message count is in sent_count.'})
            operation.accepted(response)
    # Full text was delivered privately; only transport facts enter the receipt.
    return operation.finish({k: v for k, v in {**result,
        'journal_numbers': [j['journal_number'] for j in result['journals'] if j.get('journal_number') is not None],
        'legacy_journal_numbers': [j['legacy_journal_number'] for j in result['journals'] if j.get('legacy_journal_number') is not None]}.items() if k != 'journals'})


JOURNAL_RECALL_TOOLS = [schema('send_journal_history',
    'On an explicit request to send journals, DM the authenticated member their saved entries and counts. No recipient override; never claim delivery unless sent_count is positive. Default delivery_action=send_or_recover; use resend only when the member explicitly asks to send again.',
    {'limit': {'type': ['integer', 'null']}, 'offset': {'type': ['integer', 'null']},
     'trade_number': {'type': ['integer', 'null']}, 'journal_number': {'type': ['integer', 'null']},
     'legacy_journal_number': {'type': ['integer', 'null']}, 'delivery_action': DELIVERY_ACTION})]
JOURNAL_RECALL_PROMPT = """
JOURNAL RECALL: get_journal_history reads this Discord member's complete trade
journal counts; get_trade_state lists OPEN trades only. Each Trade #N includes
one journal and preserved historical updates. Unlinked originals use explicit
Legacy journal #N. Clarify ambiguous old Journal # aliases; never guess.
For 'send my journal', call send_journal_history now. Only successful empty
lookups establish no records; tool errors do not. Requested photos additionally
require send_trade_photos with matching filters: a text DM proves no image sent.
Never use another login's records or claim delivery without confirmed sent_count.
""".strip()
