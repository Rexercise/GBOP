"""Readable Discord photo cards. Saved trade facts stay distinct from photo tags."""
import json
import math
import re


def normalized_filters(args):
    """One selector interpretation for searches and their delivery fingerprints."""
    result = {k: args[k] for k in ('journal_number', 'legacy_journal_number', 'trade_number', 'tier') if args.get(k) is not None}
    for key in ('entry_model', 'play', 'asset'):
        value = args.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError('Photo tag selectors must be text.')
        value = (value or '').strip().casefold()
        if value:
            result[key] = value
    result.update(offset=max(0, int(args.get('offset') or 0)),
                  unlinked_only=bool(args.get('unlinked_only')))
    return result


def text(value, limit=240):
    """Keep optional values, markup, and serialized objects out of the UI."""
    if value is None or isinstance(value, (dict, list, tuple, bool)):
        return ''
    value = ' '.join(str(value).split())
    if value.casefold() in ('', 'unknown', 'none', 'null', 'n/a', 'not specified at entry'):
        return ''
    if value.startswith(('{', '[')):
        try:
            if isinstance(json.loads(value), (dict, list)):
                return ''
        except ValueError:
            pass
    value = value.replace('@', '@\u200b')
    value = re.sub(r'([\\*_~|\[\]\x60])', r'\\\1', value)
    if len(value) > limit:
        value = value[:limit - 1].rstrip('\\ ') + '…'
    return value


def result_text(value):
    if value is None or isinstance(value, bool):
        return 'Not recorded'
    try:
        number = float(value)
        return f'{number:+g}R' if math.isfinite(number) else 'Not recorded'
    except (ValueError, TypeError):
        return 'Not recorded'


def summary(photo):
    """One short saved summary, never a dump of database rows or analysis."""
    trade = photo.get('trade_details') or {}
    journal = photo.get('journal') or {}
    lines = []
    for label, value in (
        ('Play', trade.get('play')),
        ('Target', trade.get('objective')),
        ('Journal note', journal.get('description')),
        ('Study note', journal.get('study_note')),
        ('Rule adherence', journal.get('rule_adherence')),
    ):
        clean = text(value, 300 if label.endswith('note') else 140)
        if clean:
            lines.append(f'**{label}:** {clean}')
    if journal:
        lines.append('**Final R:** ' + result_text(journal.get('result_r')))
        if journal.get('historical_projection'):
            lines.append('Historical journal entries are grouped; conflicting details remain unresolved.')
    return '\n'.join(lines)


def photo_notes(photo):
    """Photo observations and tags remain distinct from the saved trade."""
    trade = photo.get('trade_details') or {}
    tags = []
    for key, label in (('asset', 'Instrument'), ('play', 'Play'), ('entry_model', 'Entry')):
        value = text(photo.get(key), 120)
        if value and value != text(trade.get(key), 120):
            tags.append(f'{label}: {value}')
    if photo.get('tier') in (1, 2, 3):
        tags.append('Tier ' + str(photo['tier']))
    lines = ['**Photo tags:** ' + ' · '.join(tags)] if tags else []
    analysis = text(photo.get('analysis'), 180)
    if analysis:
        lines.append('**Image notes (excerpt):** ' + analysis)
    # A handwritten page can contain several independent journals.
    entries = photo.get('handwritten_journals') or []
    for index, entry in enumerate(entries[:3], 1):
        note = text(entry.get('description'), 180)
        if note:
            lines.append(f'**Page entry {index}:** {note}\n'
                         '**Result:** ' + result_text(entry.get('result_r')))
    if len(entries) > 3:
        lines.append(f'{len(entries) - 3} more entries on this page; ask for the full journal.')
    return '\n'.join(lines)


def recall_cards(photos, has_more=False):
    """Group a search page by trade, keeping unlinked pages apart.

    One file per message preserves the existing per-image upload limit. Only
    the first card per trade includes its summary; following cards are photos.
    No fetching, matching, or access-control decisions happen here.
    """
    groups = {}
    for photo in photos:
        number = photo.get('trade_number')
        key = ('trade', number) if number is not None else ('photo', photo['id'])
        groups.setdefault(key, []).append(photo)
    cards = []
    for group in groups.values():
        group = sorted(group, key=lambda p: (p.get('created_at') or '', p['id']))
        first = group[0]
        number = first.get('trade_number')
        associations = first.get('associated_trade_numbers') or []
        name = (f'Trade #{number}' if number is not None else
                'Journal photo · Trades ' + ', '.join('#' + str(n) for n in associations)
                if associations else 'Unlinked journal photo')
        trade = first.get('trade_details') or {}
        title_parts = [name]
        for key in ('asset', 'direction', 'status'):
            value = text(trade.get(key), 50)
            if value:
                title_parts.append(value)
        for index, photo in enumerate(group, 1):
            ext = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[photo['mime']]
            filename = 'trade-photo-' + photo['id'] + '.' + ext
            parts = [summary(first)] if index == 1 else []
            parts.append(photo_notes(photo))
            embed = {
                'title': ' · '.join(title_parts) if index == 1 else f'{name} · Photo {index}',
                'color': 0x218C74,
                'image': {'url': f'attachment://{filename}'},
                'footer': {'text': f'Photo {index} of {len(group)} in this batch'},
            }
            description = '\n\n'.join(part for part in parts if part)
            if description:
                embed['description'] = description
            cards.append((photo, filename, {
                'embeds': [embed],
                'attachments': [{'id': 0, 'filename': filename}],
                'allowed_mentions': {'parse': []},
            }))
    if cards and has_more:
        cards[-1][2]['embeds'][0]['footer']['text'] += ' · More photos available — ask for the next batch'
    return cards


def send_photos(db, guild_id, user_id, args):
    from gbop_voice_web.delivery_receipts import deliver, bounded_delivery_db
    return deliver(db, guild_id, user_id, 'send_trade_photos', args,
        lambda operation: _send_photos(bounded_delivery_db(db), guild_id, user_id, args, operation))


def _send_photos(db, guild_id, user_id, args, operation):
    import base64
    import os
    import httpx
    from gbop_voice_web.trade_photos import search
    result = search(db, guild_id, user_id, args, include_bytes=True)
    if not result.get('ok'):
        return operation.finish({'ok': False, 'error': result.get('error', 'Photo lookup failed.')})
    photos = result['photos']
    facts = {'ok': True, 'photo_count': len(photos), 'has_more': result.get('has_more', False),
             'next_offset': result.get('next_offset', 0)}
    operation.state.update(facts)
    token = os.getenv('DISCORD_TOKEN', '')
    if not token:
        return operation.finish({**facts, 'ok': False, 'error': 'Discord delivery is not configured.'}, 'no_photos' if not photos else None)
    with httpx.Client(base_url='https://discord.com/api/v10', headers={'Authorization': 'Bot ' + token}, timeout=30) as client:
        channel = client.post('/users/@me/channels', json={'recipient_id': str(user_id)})
        if channel.status_code >= 300:
            return operation.finish({**facts, 'ok': False,
                'error': 'Unable to open your DMs. Check your Discord privacy settings.'}, 'no_photos' if not photos else None)
        channel_id = channel.json()['id']
        if not photos:
            # The notice is part of the one explicitly requested photo delivery.
            # It survives waiter cancellation but is never counted as a photo.
            operation.before_send()
            response = client.post(f'/channels/{channel_id}/messages', json={
                'content': 'No saved photos match this request (0 photos).',
                'allowed_mentions': {'parse': []}})
            if response.status_code >= 300:
                operation.rejected(response)
                return operation.finish({**facts, 'ok': False,
                    'error': 'No saved photos match this request (0 photos); Discord could not confirm the notice.'}, 'no_photos')
            operation.accepted(response, notice=True)
            return operation.finish(facts, 'no_photos')
        for photo, filename, payload in recall_cards(photos, facts['has_more']):
            data = base64.b64decode(photo['image_base64'])
            operation.before_send()
            response = client.post(f'/channels/{channel_id}/messages',
                data={'payload_json': json.dumps(payload)},
                files={'files[0]': (filename, data, photo['mime'])})
            if response.status_code >= 300:
                operation.rejected(response)
                return operation.finish({**facts, 'ok': False,
                    'error': 'Discord could not deliver all photos; confirmed photo count is in sent_count.'})
            operation.accepted(response)
    return operation.finish(facts)
