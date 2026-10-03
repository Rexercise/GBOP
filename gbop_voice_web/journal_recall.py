"""Member-scoped journal retrieval and explicit private Discord delivery."""
import os
from gbop_voice_web.member_access import member_access_error

JOURNAL_PROMPT = """
JOURNAL REQUESTS ARE ACTIONS, NOT GENERIC PRIVACY QUESTIONS.
For 'do you have any of my trades recorded?', 'my records', or 'show my journal',
call get_journal_history. It includes closed journals and account-scoped counts;
get_trade_state alone returns open trades and cannot establish an empty history.
For 'send/DM me my journal records', call send_journal_history. It sends only to
the authenticated member. Never ask them to paste records you have not tried to
retrieve. Never claim you cannot access journals merely because they are private.
Report total_journals/total_trades separately; journals are not necessarily trades.
Use journal_number/trade_id for display, not internal IDs. Use offset for more pages.
An access_denied error, storage_unavailable error and a successful empty result are
different. State the actual tool result and its next step without inventing causes.
Never claim delivery unless sent_count > 0; disclose partial delivery. For requested
photos, use the returned photo_filters with send_trade_photos, one distinct filter
at a time; no unrelated filters and no claim of photo delivery until confirmed.
When the member changes subject, answer that latest request and stop the old analysis.
""".strip()


def get_history(db, guild_id, user_id, args):
    limit = max(1, min(int(args.get('limit') or 5), 10))
    offset = max(0, int(args.get('offset') or 0))
    with db() as conn:
        total = conn.execute('SELECT COUNT(*) AS n FROM journals WHERE guild_id=? AND user_id=?',
                             (guild_id, user_id)).fetchone()['n']
        trades = conn.execute('SELECT id,asset,direction,play,status FROM theses WHERE guild_id=? AND user_id=? ORDER BY id',
                              (guild_id, user_id)).fetchall()
        trade_map = {t['id']: (i + 1, dict(t)) for i, t in enumerate(trades)}
        rows = conn.execute('''SELECT id,thesis_id,description,rule_adherence,result_r,study_note,created_at,
            ROW_NUMBER() OVER (ORDER BY id) AS journal_number FROM journals
            WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ? OFFSET ?''',
            (guild_id, user_id, limit, offset)).fetchall()
        journals, filters, seen = [], [], set()
        for row in rows:
            trade_number, trade = trade_map.get(row['thesis_id'], (None, {}))
            photo_count = conn.execute('''SELECT COUNT(DISTINCT p.id) AS n FROM trade_photos p
                WHERE p.guild_id=? AND p.user_id=? AND
                ((p.thesis_id=? AND p.thesis_id IS NOT NULL) OR p.id IN
                 (SELECT d.photo_id FROM journal_details d WHERE d.journal_id=? AND d.guild_id=? AND d.user_id=?))''',
                (guild_id, user_id, row['thesis_id'] if trade else None, row['id'], guild_id, user_id)).fetchone()['n']
            journals.append({'journal_id': row['id'], 'journal_number': row['journal_number'],
                'trade_id': trade_number, 'asset': trade.get('asset'), 'direction': trade.get('direction'),
                'play': trade.get('play'), 'created_at': row['created_at'], 'result_r': row['result_r'],
                'rule_adherence': row['rule_adherence'], 'summary': row['description'],
                'study_note': row['study_note'], 'photo_count': photo_count})
            key = ('trade_number', trade_number) if trade_number else ('journal_number', row['journal_number'])
            if photo_count and key not in seen:
                seen.add(key)
                filters.append({key[0]: key[1]})
    return {'ok': True, 'status': 'records_found' if total else 'empty',
            'total_journals': total, 'total_trades': len(trades), 'journals': journals,
            'returned_count': len(journals), 'has_more': offset + len(journals) < total,
            'next_offset': offset + len(journals) if offset + len(journals) < total else None,
            'photo_filters': filters}


def journal_text(result):
    lines = [f"Your GBOP journal — {result['total_journals']} saved entries; {result['total_trades']} trade records."]
    for row in result['journals']:
        title = f"Journal #{row['journal_number']}"
        if row['trade_id'] is not None:
            title += f" | Trade #{row['trade_id']}"
        for key in ('asset', 'direction', 'play'):
            if row.get(key):
                title += ' | ' + str(row[key])
        lines.extend(['', title, 'Saved at: ' + str(row['created_at'])])
        for label, key in [('Summary', 'summary'), ('Rule adherence', 'rule_adherence'), ('Study note', 'study_note')]:
            if row.get(key):
                lines.append(label + ': ' + str(row[key]))
        value = row['result_r']
        lines.append('Result: ' + ('Not recorded' if value is None else str(value) + 'R'))
        if row['photo_count']:
            lines.append(f"Linked photos: {row['photo_count']} — ask for these photos to have them sent privately.")
    if result['has_more']:
        lines.append(f"More entries available. Next page starts at offset {result['next_offset']}.")
    if not result['journals']:
        lines.append('No journal entries on this page. This does not imply there are no open trades.')
    return '\n'.join(lines).replace('@', '@\u200b')


def send_history(result, user_id):
    import httpx
    token = os.getenv('DISCORD_TOKEN', '')
    if not token:
        return {**result, 'ok': False, 'sent_count': 0, 'error': 'Discord journal delivery is not configured.'}
    sent = 0
    text = journal_text(result)
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    try:
        with httpx.Client(base_url='https://discord.com/api/v10',
                          headers={'Authorization': f'Bot {token}'}, timeout=20) as client:
            channel = client.post('/users/@me/channels', json={'recipient_id': str(user_id)})
            if channel.status_code >= 300:
                return {**result, 'ok': False, 'sent_count': 0, 'error': 'Could not open your Discord DMs. Check your DM privacy settings.'}
            for chunk in chunks:
                response = client.post(f"/channels/{channel.json()['id']}/messages",
                    json={'content': chunk, 'allowed_mentions': {'parse': []}})
                if response.status_code >= 300:
                    return {**result, 'ok': False, 'sent_count': sent, 'partial_delivery': sent > 0,
                            'error': 'Discord did not deliver all journal messages. No records were changed.'}
                sent += 1
    except (httpx.HTTPError, KeyError, ValueError):
        return {**result, 'ok': False, 'sent_count': sent, 'partial_delivery': sent > 0,
                'error': 'Journal delivery could not be completed. No records were changed.'}
    return {**result, 'sent_count': sent, 'delivery': 'private_discord_dm'}


def journal_tool(db, guild_id, user_id, owner_id, name, args):
    denial = member_access_error(db, guild_id, user_id, owner_id)
    if denial:
        return {'ok': False, 'status': 'access_denied', 'error': denial}
    try:
        result = get_history(db, guild_id, user_id, args)
    except (TypeError, ValueError):
        return {'ok': False, 'status': 'invalid_request', 'error': 'Use a journal page size from 1 to 10 and a nonnegative offset.'}
    except Exception as exc:
        print('[GBOP-JOURNAL-RECALL] storage error:', type(exc).__name__)
        return {'ok': False, 'status': 'storage_unavailable',
                'error': 'Your journal lookup failed. This is not an empty journal or a loss of your records. Please retry.'}
    return send_history(result, user_id) if name == 'send_journal_history' else result


def configure_journal_tools(tools):
    """Extend existing tool contracts once, without changing member identity."""
    for tool in tools:
        if tool.get('name') == 'get_journal_history':
            tool['description'] = 'Read this member\'s saved journals, including closed trades, total counts and linked-photo filters. Never infer an empty journal from no open trades.'
            properties = tool['parameters']['properties']
            properties['offset'] = {'type': ['integer', 'null'], 'minimum': 0}
            if 'offset' not in tool['parameters']['required']:
                tool['parameters']['required'].append('offset')
    if not any(t.get('name') == 'send_journal_history' for t in tools):
        tools.append({'type': 'function', 'name': 'send_journal_history', 'strict': True,
            'description': 'Only when asked to send/DM journal records: deliver a page privately to the authenticated member. No recipient argument and no record changes.',
            'parameters': {'type': 'object', 'properties': {
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10},
                'offset': {'type': ['integer', 'null'], 'minimum': 0}},
                'required': ['limit', 'offset'], 'additionalProperties': False}})
