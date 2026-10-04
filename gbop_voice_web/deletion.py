"""Owned deletion with stable, aggregate previews; callers own the transaction."""
import hashlib
import hmac
import json


def lock_member_deletion(conn, guild_id, user_id):
    """Use the same member lock as journal/execution/photo writes, then recheck access."""
    conn.execute('SELECT pg_advisory_xact_lock(?)', (user_id,))
    from gbop_voice_web.journal_context import member_revision
    member_revision(conn, guild_id, user_id)


def validate_owned_relations(conn, guild_id, user_id, *, trade_id=None, journal_ids=()):
    """Fail closed on inconsistent legacy ownership before FK cascades can run."""
    if trade_id is not None:
        for table in ('journals', 'thesis_events', 'thesis_executions', 'risk_flags', 'trade_photos'):
            foreign = conn.execute(f"""SELECT 1 FROM {table} WHERE thesis_id=? AND
                (guild_id IS NULL OR user_id IS NULL OR guild_id<>? OR user_id<>?) LIMIT 1""",
                (trade_id, guild_id, user_id)).fetchone()
            if foreign:
                raise ValueError('Linked records have inconsistent ownership. Nothing deleted; contact support.')
        journal_ids = [r['id'] for r in conn.execute(
            'SELECT id FROM journals WHERE thesis_id=? AND guild_id=? AND user_id=?',
            (trade_id, guild_id, user_id)).fetchall()]
    if journal_ids:
        placeholders = ','.join('?' for _ in journal_ids)
        foreign = conn.execute(f"""SELECT 1 FROM journal_details WHERE journal_id IN ({placeholders}) AND
            (guild_id IS NULL OR user_id IS NULL OR guild_id<>? OR user_id<>?) LIMIT 1""",
            (*journal_ids, guild_id, user_id)).fetchone()
        if foreign:
            raise ValueError('Linked records have inconsistent ownership. Nothing deleted; contact support.')


def deletion_snapshot(conn, guild_id, user_id, *, journal_id=None, trade_id=None):
    """Fingerprint every owned record in the selected journal/trade aggregate.

    Includes source photos even when they survive deletion, since they are part
    of what was reviewed. Missing tables/query failures must fail closed, never
    silently downgrade to a row-only fingerprint. Call within one transaction.
    """
    if (journal_id is None) == (trade_id is None):
        raise ValueError('Choose exactly one journal or trade for deletion.')
    lock_member_deletion(conn, guild_id, user_id)
    journal = None
    if journal_id is not None:
        journal = conn.execute('SELECT * FROM journals WHERE id=? AND guild_id=? AND user_id=?',
                               (journal_id, guild_id, user_id)).fetchone()
        if journal is None:
            raise ValueError('That journal was not found in your account. Nothing deleted.')
        trade_id = journal['thesis_id']
    validate_owned_relations(conn, guild_id, user_id, trade_id=trade_id,
                             journal_ids=(journal_id,) if journal_id is not None else ())
    trade = None
    records = {'guild_id': guild_id, 'user_id': user_id,
               'target_journal_id': journal_id, 'target_trade_id': trade_id}
    if trade_id is not None:
        trade = conn.execute('SELECT * FROM theses WHERE id=? AND guild_id=? AND user_id=?',
                             (trade_id, guild_id, user_id)).fetchone()
        if trade is None:
            raise ValueError('Linked trade was not found in your account. Nothing deleted.')
        records['trade'] = dict(trade)
        for table in ('journals', 'thesis_events', 'thesis_executions', 'risk_flags'):
            records[table] = [dict(r) for r in conn.execute(
                f'SELECT * FROM {table} WHERE thesis_id=? AND guild_id=? AND user_id=? ORDER BY id',
                (trade_id, guild_id, user_id)).fetchall()]
    else:
        records.update(journals=[dict(journal)], thesis_events=[], thesis_executions=[], risk_flags=[])
    journal_ids = [r['id'] for r in records['journals']]
    records['journal_details'] = []
    if journal_ids:
        placeholders = ','.join('?' for _ in journal_ids)
        records['journal_details'] = [dict(r) for r in conn.execute(
            f'SELECT * FROM journal_details WHERE guild_id=? AND user_id=? AND journal_id IN ({placeholders}) ORDER BY journal_id',
            (guild_id, user_id, *journal_ids)).fetchall()]
    source_ids = set()
    for detail in records['journal_details']:
        if detail.get('photo_id'):
            source_ids.add(detail['photo_id'])
        metadata = json.loads(detail.get('metadata') or '{}')
        for source in metadata.get('source_attachments', []):
            if isinstance(source, dict) and isinstance(source.get('photo_id'), str):
                source_ids.add(source['photo_id'])
    for event in records['thesis_events']:
        if event.get('event') == 'journal_source_v1':
            source = json.loads(event.get('details') or '{}')
            if source.get('journal_id') in journal_ids and isinstance(source.get('photo_id'), str):
                source_ids.add(source['photo_id'])
    photo_clauses, photo_params = [], [guild_id, user_id]
    if trade_id is not None:
        photo_clauses.append('thesis_id=?')
        photo_params.append(trade_id)
    if source_ids:
        photo_clauses.append('id IN (' + ','.join('?' for _ in source_ids) + ')')
        photo_params.extend(sorted(source_ids))
    # Hash image content inside the DB so large multi-photo journals never copy
    # base64 images into Python or the JSON fingerprint. Include every other
    # schema column so newly added mutable photo fields stay protected.
    photo_columns = [r['name'] for r in conn.execute('PRAGMA table_info(trade_photos)').fetchall()]
    if 'image_base64' not in photo_columns:
        raise ValueError('Photo records are unavailable for a complete deletion preview.')
    projection = ','.join('"' + name.replace('"', '""') + '"' for name in photo_columns if name != 'image_base64')
    projection += ',md5(image_base64) AS image_content_fingerprint'
    records['trade_photos'] = [dict(r) for r in conn.execute(
        'SELECT ' + projection + ' FROM trade_photos WHERE guild_id=? AND user_id=? AND ('
        + (' OR '.join(photo_clauses) or '1=0') + ') ORDER BY id', tuple(photo_params)).fetchall()]
    records['source_photo_ids'] = sorted(source_ids)
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, default=str).encode()).hexdigest()
    return {'journal': dict(journal) if journal else None, 'trade': dict(trade) if trade else None,
            'fingerprint': digest, 'counts': {'journals': len(records['journals']),
            'events': len(records['thesis_events']), 'executions': len(records['thesis_executions']),
            'risk_flags': len(records['risk_flags']), 'photos': len(records['trade_photos'])}}


def validate_deletion_snapshot(snapshot, expected_fingerprint):
    if not isinstance(expected_fingerprint, str) or not hmac.compare_digest(snapshot['fingerprint'], expected_fingerprint):
        raise ValueError('This journal or linked trade changed. Preview it again and confirm the updated records.')


def delete_trade_records(conn, guild_id, user_id, trade_id):
    lock_member_deletion(conn, guild_id, user_id)
    params = (trade_id, guild_id, user_id)
    row = conn.execute(
        'SELECT id FROM theses WHERE id=? AND guild_id=? AND user_id=? FOR UPDATE', params
    ).fetchone()
    if row is None:
        raise ValueError('Linked trade was not found in your account. Nothing deleted.')
    validate_owned_relations(conn, guild_id, user_id, trade_id=trade_id)
    counts = {}
    for table, label in (('risk_flags', 'risk_flags'), ('thesis_events', 'events'),
                         ('thesis_executions', 'executions'), ('journals', 'journals')):
        counts[label] = conn.execute(
            f'SELECT COUNT(*) FROM {table} WHERE thesis_id=? AND guild_id=? AND user_id=?', params
        ).fetchone()[0]
        conn.execute(f'DELETE FROM {table} WHERE thesis_id=? AND guild_id=? AND user_id=?', params)
    conn.execute('DELETE FROM theses WHERE id=? AND guild_id=? AND user_id=?', params)
    return counts
