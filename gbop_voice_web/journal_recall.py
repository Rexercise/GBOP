"""Member-scoped journal recall and explicit private delivery, without AI writes."""
import os
from gbop_voice_web.trade_photos import schema
from gbop_voice_web.photo_recall import result_text


def history(db, guild_id, user_id, args):
    limit = max(1, min(int(args.get('limit') or 5), 20))
    offset = max(0, int(args.get('offset') or 0))
    with db() as conn:
        ids = conn.execute('SELECT id FROM journals WHERE guild_id=? AND user_id=? ORDER BY id', (guild_id, user_id)).fetchall()
        trades = conn.execute('SELECT id,status FROM theses WHERE guild_id=? AND user_id=? ORDER BY id', (guild_id, user_id)).fetchall()
        rows = conn.execute('SELECT * FROM journals WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ? OFFSET ?',
                            (guild_id, user_id, limit, offset)).fetchall()
    numbers = {r['id']: n for n, r in enumerate(ids, 1)}
    trade_numbers = {r['id']: n for n, r in enumerate(trades, 1)}
    journals = []
    for r in rows:
        row = dict(r)
        journals.append(dict(journal_id=row['id'], journal_number=numbers[row['id']],
            trade_id=trade_numbers.get(row.get('thesis_id')), result_r=row.get('result_r'),
            rule_adherence=row.get('rule_adherence'), summary=row.get('description'),
            study_note=row.get('study_note'), created_at=row.get('created_at')))
    total = len(ids)
    open_count = sum(r['status'] == 'OPEN' for r in trades)
    return dict(ok=True, journals=journals, journal_count=total, trade_count=len(trades),
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
        output.extend(value[n:n+1800] for n in range(0, len(value), 1800))
    if result['has_more']:
        output.append('More entries are available. Ask for the next page of your journal.')
    return output


def send_history(db, guild_id, user_id, args):
    import httpx
    result = history(db, guild_id, user_id, args)
    token = os.getenv('DISCORD_TOKEN', '')
    if not token:
        return {**result, 'ok': False, 'sent_count': 0, 'error': 'Journal retrieved; Discord delivery is not configured.'}
    sent = 0
    try:
        with httpx.Client(base_url='https://discord.com/api/v10', headers={'Authorization': 'Bot ' + token}, timeout=20) as client:
            channel = client.post('/users/@me/channels', json={'recipient_id': str(user_id)})
            if channel.status_code >= 300:
                return {**result, 'ok': False, 'sent_count': 0, 'error': 'Journal retrieved but your DMs could not be opened. Check Discord privacy settings.'}
            for content in messages(result):
                response = client.post(f"/channels/{channel.json()['id']}/messages",
                                       json={'content': content, 'allowed_mentions': {'parse': []}})
                if response.status_code >= 300:
                    return {**result, 'ok': False, 'sent_count': sent, 'error': 'Only part of the journal was delivered; Discord blocked or rate-limited the remaining messages.'}
                sent += 1
    except (httpx.HTTPError, KeyError, ValueError):
        return {**result, 'ok': False, 'sent_count': sent, 'error': 'Journal retrieved; Discord delivery failed or timed out. Delivery of the last attempted message is uncertain.'}
    # Full text was delivered privately; do not reload it into every voice turn.
    return {k: v for k, v in {**result, 'sent_count': sent,
            'journal_numbers': [j['journal_number'] for j in result['journals']],
            'delivery': 'private_discord_dm'}.items() if k != 'journals'}


JOURNAL_RECALL_TOOLS = [schema('send_journal_history',
    'On an explicit request to send journals, DM the authenticated member their saved entries and counts. No recipient override; never claim delivery unless sent_count is positive.',
    {'limit': {'type': ['integer', 'null']}, 'offset': {'type': ['integer', 'null']}})]
JOURNAL_RECALL_PROMPT = """
JOURNAL RECALL: You CAN retrieve this authenticated member's saved journal. For
'do you have any of my trades recorded?' call get_journal_history for both journal
and all-trade counts; get_trade_state alone lists OPEN trades, not all saved trades.
For 'send my journal/records', call send_journal_history now, not an offer or a
request to paste records. Its counts are member-scoped; a different Discord login
has different records. Never reveal the owner's history to the test member.
Only report no records after a successful empty lookup. A timeout/access error is
not an empty journal. Do not claim you cannot access private records without an
actual tool error. Stop the prior market discussion when the member requests records.
When photos are requested, also use send_trade_photos with matching resolved trade
or journal filters. A journal text DM alone is not proof of photo delivery.
""".strip()
