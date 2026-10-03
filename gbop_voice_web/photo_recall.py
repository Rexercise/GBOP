"""Readable Discord photo cards. Saved trade facts stay distinct from photo tags."""
import json
import math
import re


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
        name = f'Trade #{number}' if number is not None else 'Unlinked journal photo'
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
