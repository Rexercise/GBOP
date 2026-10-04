"""Canonical member numbers equal Trade #; old ordinals are explicit legacy IDs."""


def journal_display(conn, guild_id, user_id):
    """Read-only presentation for retained rows using bounded bulk queries."""
    import json
    from collections import defaultdict
    from gbop_voice_web.unified_journal import CANONICAL_EVENT
    trades = conn.execute('SELECT id FROM theses WHERE guild_id=? AND user_id=? ORDER BY id',
                          (guild_id, user_id)).fetchall()
    numbers = {r['id']: n for n, r in enumerate(trades, 1)}
    rows = conn.execute('SELECT id,thesis_id FROM journals WHERE guild_id=? AND user_id=? ORDER BY id',
                        (guild_id, user_id)).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        if row['thesis_id'] in numbers:
            grouped[row['thesis_id']].append(row['id'])
    markers = defaultdict(list)
    if conn.execute('PRAGMA table_info(thesis_events)').fetchall():
        for event in conn.execute('SELECT thesis_id,details FROM thesis_events WHERE guild_id=? AND user_id=? AND event=? ORDER BY id',
                                  (guild_id,user_id,CANONICAL_EVENT)).fetchall():
            markers[event['thesis_id']].append(json.loads(event['details'] or '{}').get('journal_id'))
    canonical = {}
    for thesis_id, ids in grouped.items():
        marked = next((candidate for candidate in reversed(markers[thesis_id]) if candidate in ids), None)
        canonical[thesis_id] = marked if marked is not None else (ids[0] if len(ids)==1 and not markers[thesis_id] else None)
    return [{'id': r['id'], 'thesis_id': r['thesis_id'] if r['thesis_id'] in numbers else None,
             'journal_number': numbers.get(r['thesis_id']), 'trade_number': numbers.get(r['thesis_id']),
             'legacy_journal_number': n, 'canonical': canonical.get(r['thesis_id']) == r['id'],
             'is_legacy': canonical.get(r['thesis_id']) != r['id']}
            for n, r in enumerate(rows, 1)]


def resolve_journal_selector(conn, guild_id, user_id, *, trade_number=None, journal_number=None,
                             legacy_journal_number=None, allow_group=True):
    """Never interpret an ambiguous historic Journal # as a different trade.

    Explicit Trade # selects the unified record, even if no journal exists yet.
    Explicit legacy_journal_number selects exactly the old row without relinking.
    Callers must decide whether editing an explicitly selected legacy row is
    appropriate; this resolver itself never changes data.
    """
    selectors = [v for v in (trade_number, journal_number, legacy_journal_number) if v is not None]
    if not selectors:
        return {'ok': False, 'error': 'Specify the Trade # or an explicit legacy journal number.'}
    if any(type(v) is not int or v < 1 for v in selectors):
        return {'ok': False, 'error': 'Journal and trade numbers must be positive whole numbers.'}
    if legacy_journal_number is not None and (trade_number is not None or journal_number is not None):
        return {'ok': False, 'status': 'ambiguous_journal_number',
                'error': 'Use either the Trade # or the legacy journal number, not both.'}
    if trade_number is not None and journal_number is not None and trade_number != journal_number:
        return {'ok': False, 'status': 'ambiguous_journal_number',
                'error': 'Trade # and Journal # must name the same trade. Which one did you mean?'}
    displays = journal_display(conn, guild_id, user_id)
    if legacy_journal_number is not None:
        row = next((r for r in displays if r['legacy_journal_number'] == legacy_journal_number), None)
        return ({'ok': True, **row, 'record_id': row['id'], 'explicit_legacy': True} if row else
                {'ok': False, 'error': 'Legacy journal number not found in your account.'})
    number = trade_number if trade_number is not None else journal_number
    thesis = conn.execute('SELECT id FROM theses WHERE guild_id=? AND user_id=? ORDER BY id LIMIT 1 OFFSET ?',
                          (guild_id, user_id, number - 1)).fetchone()
    old = next((r for r in displays if r['legacy_journal_number'] == number), None)
    if trade_number is None and old and (not thesis or old['thesis_id'] != thesis['id']):
        return {'ok': False, 'status': 'ambiguous_journal_number',
                'error': 'That Journal # can refer to old history or a different Trade #. Please specify the Trade # or legacy journal number.',
                'requested_number': number, 'legacy_journal_number': old['legacy_journal_number'],
                'legacy_trade_number': old['trade_number']}
    if not thesis:
        return {'ok': False, 'error': 'Trade number not found in your account.'}
    from gbop_voice_web.unified_journal import canonical_journal_id
    record_id = canonical_journal_id(conn, guild_id, user_id, thesis['id'])
    return {'ok': True, 'record_id': record_id, 'thesis_id': thesis['id'],
            'journal_number': number, 'trade_number': number, 'explicit_legacy': False,
            'canonical': record_id is not None}


def journal_number(db, guild_id, user_id, journal_id):
    with db() as conn:
        return next((r['journal_number'] for r in journal_display(conn, guild_id, user_id)
                     if r['id'] == journal_id), None)


def legacy_journal_number(db, guild_id, user_id, journal_id):
    with db() as conn:
        return next((r['legacy_journal_number'] for r in journal_display(conn, guild_id, user_id)
                     if r['id'] == journal_id), None)


def journal_record_id(db, guild_id, user_id, number):
    with db() as conn:
        result = resolve_journal_selector(conn, guild_id, user_id, journal_number=number)
    return result.get('record_id') if result['ok'] else None
