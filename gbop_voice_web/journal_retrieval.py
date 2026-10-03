"""Member-scoped record counts and explicit private journal delivery.

No caller-supplied member/recipient ID, permission changes, or database writes.
Only send_journal_records has a side effect: delivery to the authenticated member.
"""
import base64
import json
import os
from gbop_voice_web.member_access import member_access_error


def _schema(name, description, fields):
    return {'type': 'function', 'name': name, 'description': description, 'strict': True,
            'parameters': {'type': 'object', 'properties': fields,
                           'required': list(fields), 'additionalProperties': False}}


PAGE = {'offset': {'type': ['integer', 'null'], 'minimum': 0},
        'limit': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 10}}
JOURNAL_TOOLS = [
    _schema('get_saved_records', 'Read this authenticated member\'s total saved trades, executions, journals and photos, plus paginated journal details. Use for "Do I have trades recorded?" and "show my journal". Closed trades count too.', PAGE),
    _schema('send_journal_records', 'Only when requested: privately DM this member their saved journal page, a complete text copy and optionally linked photos. No other recipient is accepted. Report partial delivery truthfully.',
            {**PAGE, 'include_photos': {'type': 'boolean'}}),
]
JOURNAL_NAMES = {t['name'] for t in JOURNAL_TOOLS}
JOURNAL_PROMPT = """
SAVED RECORD REQUESTS ARE ACTIONS, NOT MARKET CLASSIFICATIONS.
For 'do I have trades recorded?', use get_saved_records: distinguish total saved
trades, open trades, executions and journals. No open trades does not mean no history.
For 'send me my journal/records', call send_journal_records (include linked photos
unless the member asks for text only). Stop the prior market debate. Do not ask
them to paste records that the tools can retrieve. Report actual returned counts,
empty records, an access denial, or a temporary lookup failure distinctly. Never
claim inability to access private journals without a tool result. Only say 'sent'
when sent_count is positive; disclose partial photo delivery and page boundaries.
""".strip()


def read_records(db, guild_id, user_id, args):
    limit = max(1, min(int(args.get('limit') or 5), 10))
    offset = max(0, int(args.get('offset') or 0))
    with db() as conn:
        totals = {}
        for label, table in (('trades', 'theses'), ('executions', 'thesis_executions'),
                             ('journals', 'journals'), ('photos', 'trade_photos')):
            totals[label] = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE guild_id=? AND user_id=?',
                                         (guild_id, user_id)).fetchone()[0]
        totals['open_trades'] = conn.execute("SELECT COUNT(*) FROM theses WHERE guild_id=? AND user_id=? AND status='OPEN'",
                                             (guild_id, user_id)).fetchone()[0]
        trade_rows = conn.execute('SELECT id,asset,direction,play,status FROM theses WHERE guild_id=? AND user_id=? ORDER BY id',
                                  (guild_id, user_id)).fetchall()
        trade_numbers = {t['id']: i + 1 for i, t in enumerate(trade_rows)}
        journal_ids = conn.execute('SELECT id FROM journals WHERE guild_id=? AND user_id=? ORDER BY id',
                                   (guild_id, user_id)).fetchall()
        journal_numbers = {j['id']: i + 1 for i, j in enumerate(journal_ids)}
        rows = conn.execute('SELECT id,thesis_id,description,rule_adherence,result_r,study_note,created_at FROM journals WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ? OFFSET ?',
                             (guild_id, user_id, limit, offset)).fetchall()
        journals = []
        for row in rows:
            trade_number = trade_numbers.get(row['thesis_id'])
            trade = next((dict(t) for t in trade_rows if t['id'] == row['thesis_id']), {})
            journals.append({'journal_number': journal_numbers[row['id']], 'trade_number': trade_number,
                             'asset': trade.get('asset'), 'play': trade.get('play'),
                             'summary': row['description'], 'rule_adherence': row['rule_adherence'],
                             'result_r': row['result_r'], 'study_note': row['study_note'],
                             'record_created_at': str(row['created_at'])})
    has_more = offset + len(journals) < totals['journals']
    return {'ok': True, 'totals': totals, 'journals': journals, 'offset': offset,
            'has_more': has_more, 'next_offset': offset + len(journals) if has_more else None,
            'scope': 'Authenticated member only; creation timestamps are not inferred trading dates.'}


def journal_text(snapshot):
    lines = ['GBOP — Your saved journal records',
             'Record creation timestamps are not necessarily trading dates.',
             'Totals: ' + ', '.join(f'{v} {k.replace("_", " ")}' for k, v in snapshot['totals'].items()), '']
    for j in snapshot['journals']:
        lines.extend([f"Journal #{j['journal_number']}" +
                      (f" · Trade #{j['trade_number']}" if j['trade_number'] is not None else ''),
                      f"Recorded: {j['record_created_at']}",
                      f"Instrument: {j['asset'] or 'Not recorded'} · Play: {j['play'] or 'Not recorded'}",
                      'Result: ' + (f"{j['result_r']}R" if j['result_r'] is not None else 'Not recorded'),
                      'Summary: ' + str(j['summary'] or ''),
                      'Rule adherence: ' + str(j['rule_adherence'] or ''),
                      'Study note: ' + str(j['study_note'] or ''), ''])
    if snapshot['has_more']:
        lines.append(f"More journals are available; next offset is {snapshot['next_offset']}.")
    return '\n'.join(lines)


def send_records(db, guild_id, user_id, args):
    import httpx
    from gbop_voice_web.photo_recall import recall_cards, text
    from gbop_voice_web.trade_photos import search
    snapshot = read_records(db, guild_id, user_id, args)
    if not snapshot['journals']:
        return {**snapshot, 'sent_count': 0, 'message': 'No journal entries on this page. Saved trade counts are shown separately.'}
    token = os.getenv('DISCORD_TOKEN', '')
    if not token:
        return {'ok': False, 'error_code': 'delivery_not_configured', 'sent_count': 0,
                'error': 'Discord journal delivery is not configured.'}
    photos, seen, more_photos = [], set(), False
    if args.get('include_photos'):
        for j in snapshot['journals']:
            found = search(db, guild_id, user_id, {'journal_number': j['journal_number'], 'offset': 0}, include_bytes=True)
            if not found.get('ok'):
                more_photos = True
                continue
            more_photos = more_photos or found.get('has_more', False)
            for photo in found.get('photos', []):
                if photo['id'] in seen:
                    continue
                seen.add(photo['id'])
                if len(photos) < 5:
                    photos.append(photo)
                else:
                    more_photos = True
    sent, photo_sent = 0, 0
    outcome = {'journal_count': len(snapshot['journals']), 'totals': snapshot['totals'],
               'has_more': snapshot['has_more'], 'next_offset': snapshot['next_offset'],
               'more_photos': more_photos}
    try:
        with httpx.Client(base_url='https://discord.com/api/v10',
                          headers={'Authorization': f'Bot {token}'}, timeout=30) as client:
            channel = client.post('/users/@me/channels', json={'recipient_id': str(user_id)})
            if channel.status_code >= 300:
                return {'ok': False, 'error_code': 'dm_unavailable', 'sent_count': 0,
                        'error': 'Your records exist, but Discord could not open your DMs. Check your Discord privacy settings.'}
            path = f"/channels/{channel.json()['id']}/messages"
            bullets = [f"Journal #{j['journal_number']} — {text(j['asset'], 40) or 'Instrument not recorded'}: {text(j['summary'], 160)}"
                       for j in snapshot['journals']]
            payload = {'content': ('**Your GBOP journal records**\n' + '\n'.join(bullets))[:1800],
                       'allowed_mentions': {'parse': []}, 'attachments': [{'id': 0, 'filename': 'gbop-journals.txt'}]}
            response = client.post(path, data={'payload_json': json.dumps(payload)},
                                   files={'files[0]': ('gbop-journals.txt', journal_text(snapshot).encode('utf-8'), 'text/plain')})
            if response.status_code >= 300:
                return {'ok': False, 'error_code': 'dm_delivery_failed', 'sent_count': 0,
                        'error': 'Your records were retrieved, but Discord did not accept the journal message.'}
            sent = 1
            for photo, filename, card in recall_cards(photos, more_photos):
                response = client.post(path, data={'payload_json': json.dumps(card)},
                                       files={'files[0]': (filename, base64.b64decode(photo['image_base64']), photo['mime'])})
                if response.status_code >= 300:
                    return {**outcome, 'ok': False, 'error_code': 'partial_photo_delivery',
                            'sent_count': sent, 'photo_sent_count': photo_sent,
                            'error': 'The journal text was sent, but not all linked photos were delivered.'}
                sent += 1
                photo_sent += 1
    except httpx.HTTPError:
        return {**outcome, 'ok': False, 'error_code': 'delivery_interrupted',
                'sent_count': sent, 'photo_sent_count': photo_sent,
                'delivery_status_unknown': True,
                'error': 'Discord delivery was interrupted. Counts include only confirmed sends; check DMs before retrying because the last request may have arrived.'}
    return {**outcome, 'ok': True, 'sent_count': sent, 'photo_sent_count': photo_sent}


def journal_tool(db, guild_id, user_id, name, args):
    owner_id = int(os.getenv('GTOP_OWNER_USER_ID') or os.getenv('GBOP_VOICE_USER_ID') or '0')
    denial = member_access_error(db, guild_id, user_id, owner_id)
    if denial:
        return {'ok': False, 'error_code': 'access_denied', 'error': denial, 'sent_count': 0}
    try:
        if name == 'get_saved_records':
            return read_records(db, guild_id, user_id, args)
        if name == 'send_journal_records':
            return send_records(db, guild_id, user_id, args)
        return {'ok': False, 'error_code': 'unknown_tool', 'error': 'Unknown journal request.'}
    except Exception as exc:
        print('[GBOP-JOURNAL] request failed:', type(exc).__name__)
        return {'ok': False, 'error_code': 'journal_unavailable',
                'error': 'Your journal request could not be completed. This is a temporary retrieval or delivery error, not evidence that your records are missing. Check DMs before retrying a send.'}
