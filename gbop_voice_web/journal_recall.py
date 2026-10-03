"""Member-scoped journal recall and explicit private delivery, without AI writes."""
import os
import json
from gbop_voice_web.trade_photos import schema
from gbop_voice_web.photo_recall import result_text
from gbop_voice_web.delivery_receipts import DELIVERY_ACTION


def history(db, guild_id, user_id, args):
    limit = max(1, min(int(args.get('limit') or 5), 20))
    offset = max(0, int(args.get('offset') or 0))
    with db() as conn:
        ids = conn.execute('SELECT id FROM journals WHERE guild_id=? AND user_id=? ORDER BY id', (guild_id, user_id)).fetchall()
        trades = conn.execute('SELECT id,status FROM theses WHERE guild_id=? AND user_id=? ORDER BY id', (guild_id, user_id)).fetchall()
        rows = conn.execute('SELECT * FROM journals WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ? OFFSET ?',
                            (guild_id, user_id, limit, offset)).fetchall()
    # Existing deployments may still be initializing the optional detail table.
    # A missing detail read never changes the member-owned base records.
    metadata_by_id = {}
    metadata_available = True
    try:
        with db() as conn:
            selected_ids = [row['id'] for row in rows]
            placeholders = ','.join('?' for _ in selected_ids)
            details = conn.execute('SELECT journal_id,metadata FROM journal_details WHERE guild_id=? AND user_id=? '
                                   + 'AND journal_id IN (' + placeholders + ')',
                                   (guild_id, user_id, *selected_ids)).fetchall() if selected_ids else []
        metadata_by_id = {r['journal_id']: json.loads(r['metadata'] or '{}') for r in details}
    except Exception:
        metadata_available = False
    numbers = {r['id']: n for n, r in enumerate(ids, 1)}
    trade_numbers = {r['id']: n for n, r in enumerate(trades, 1)}
    journals = []
    for r in rows:
        row = dict(r)
        journals.append(dict(journal_id=row['id'], journal_number=numbers[row['id']],
            trade_id=trade_numbers.get(row.get('thesis_id')), result_r=row.get('result_r'),
            rule_adherence=row.get('rule_adherence'), summary=row.get('description'),
            study_note=row.get('study_note'), created_at=row.get('created_at'),
            metadata=metadata_by_id.get(row['id'], {})))
    total = len(ids)
    open_count = sum(r['status'] == 'OPEN' for r in trades)
    return dict(ok=True, journals=journals, metadata_available=metadata_available, journal_count=total, trade_count=len(trades),
                open_trade_count=open_count, closed_trade_count=sum(r['status'] == 'CLOSED' for r in trades),
                has_more=offset + len(rows) < total, next_offset=offset + len(rows),
                identity_scope='Authenticated Discord account only; another login may have different records.')


def messages(result):
    header = (f"Your GBOP journal — {result['journal_count']} saved entries; "
              f"{result['trade_count']} trade records ({result['open_trade_count']} open).")
    output = [header]
    for row in result['journals']:
        title = f"Journal #{row['journal_number']}"
        if row['trade_id'] is not None:
            title += f" · Trade #{row['trade_id']}"
        # Send complete text in bounded chunks, disabling all mentions at transport.
        value = (title + f"\nResult: {result_text(row['result_r'])}"
                 + '\nEntry: ' + str(row['summary'] or 'Not specified')
                 + '\nAdherence: ' + str(row['rule_adherence'] or 'Not specified')
                 + '\nStudy note: ' + str(row['study_note'] or 'Not specified'))
        meta = row.get('metadata') or {}
        if meta.get('reported_entry_at'):
            value += '\nReported entry: ' + meta['reported_entry_at']
        if meta.get('reported_exit_at'):
            value += '\nReported exit: ' + meta['reported_exit_at']
        if meta.get('reported_outcome'):
            value += '\nReported outcome: ' + meta['reported_outcome']
        scope = (meta.get('market_review') or {}).get('selection') or {}
        if scope:
            value += '\nReviewed scope: ' + ' · '.join(str(scope[k]) for k in ('asset','date_ny','shift','anchor_start_ny') if scope.get(k))
        output.extend(value[n:n+1800] for n in range(0, len(value), 1800))
    if result['has_more']:
        output.append('More entries are available. Ask for the next page of your journal.')
    return output


def send_history(db, guild_id, user_id, args):
    from gbop_voice_web.delivery_receipts import deliver, bounded_delivery_db
    return deliver(db, guild_id, user_id, 'send_journal_history', args,
        lambda operation: _send_history(bounded_delivery_db(db), guild_id, user_id, args, operation))


def _send_history(db, guild_id, user_id, args, operation):
    import httpx
    result = history(db, guild_id, user_id, args)
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
        'journal_numbers': [j['journal_number'] for j in result['journals']]}.items() if k != 'journals'})


JOURNAL_RECALL_TOOLS = [schema('send_journal_history',
    'On an explicit request to send journals, DM the authenticated member their saved entries and counts. No recipient override; never claim delivery unless sent_count is positive. Default delivery_action=send_or_recover; use resend only when the member explicitly asks to send again.',
    {'limit': {'type': ['integer', 'null']}, 'offset': {'type': ['integer', 'null']}, 'delivery_action': DELIVERY_ACTION})]
JOURNAL_RECALL_PROMPT = """
JOURNAL RECALL: get_journal_history retrieves this authenticated member's saved
journals and all-trade counts; get_trade_state lists only OPEN trades. Never use
another member's or the owner's records. For 'send my journal', call
send_journal_history, not an offer or request to paste it. Stop prior market talk.
Only a successful empty lookup means no records; timeout/access errors do not.
Never claim unavailable access without a tool error. For requested photos also
call send_trade_photos with matching trade/journal filters; a journal text DM
proves no photo delivery. Counts belong to this Discord login only.
""".strip()
