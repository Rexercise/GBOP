"""Read-only, exact-record image association for unified journal recall.

The selected database identities are supplied by journal_recall, never by tool
arguments. Fetching the complete owner-scoped set avoids a shifting page cursor
and cannot accidentally widen a single-record request to the member's account.
"""
from gbop_voice_web.trade_photos import _journal_photo_associations


def attach_photos(db, guild_id, user_id, records, *, include_bytes=False):
    if not records:
        return []
    with db() as conn:
        trades = {r['id']: dict(r) for r in conn.execute(
            'SELECT * FROM theses WHERE guild_id=? AND user_id=?',
            (guild_id, user_id)).fetchall()}
        journals, sources = _journal_photo_associations(conn, guild_id, user_id, set(trades))
        scopes = []
        for record in records:
            tid = record.get('_thesis_id')
            ids = set(record.get('_journal_ids') or [])
            if tid is not None and tid not in trades:
                raise ValueError('The selected trade is no longer available.')
            if any(jid not in journals or (tid is not None and journals[jid].get('thesis_id') != tid)
                   for jid in ids):
                raise ValueError('The selected journal association changed.')
            # An explicitly selected historical entry still belongs to its
            # owned thesis, including its canonical/other preserved sources.
            ids.update(jid for jid, row in journals.items()
                       if tid is not None and row.get('thesis_id') == tid)
            pids = set().union(*(sources[jid] for jid in ids)) if ids else set()
            scopes.append((record, tid, pids))
        all_tids = {tid for _, tid, _ in scopes if tid is not None}
        all_pids = set().union(*(pids for _, _, pids in scopes))
        clauses, params = [], [guild_id, user_id]
        if all_tids:
            clauses.append('thesis_id IN (' + ','.join('?' for _ in all_tids) + ')')
            params.extend(sorted(all_tids))
        if all_pids:
            clauses.append('id IN (' + ','.join('?' for _ in all_pids) + ')')
            params.extend(sorted(all_pids))
        if not clauses:
            rows = []
        else:
            fields = '*' if include_bytes else 'id,thesis_id,analysis,tier,entry_model,play,asset,created_at,mime'
            rows = conn.execute('SELECT ' + fields + ' FROM trade_photos '
                'WHERE guild_id=? AND user_id=? AND (' + ' OR '.join(clauses) + ') '
                'ORDER BY created_at,id', params).fetchall()
        output = {}
        for record, tid, pids in scopes:
            record['photos'] = []
            for row in rows:
                if row['id'] not in pids and not (tid is not None and row['thesis_id'] == tid):
                    continue
                photo = {key: row[key] for key in ('id','analysis','tier','entry_model','play','asset','created_at','mime')}
                record['photos'].append(photo)
                if row['id'] not in output:
                    item = {**photo, 'trade_number': record.get('trade_number'),
                            'trade_details': record.get('trade_facts') or {},
                            'journal': {'description': record.get('summary'),
                                'rule_adherence': record.get('rule_adherence'),
                                'result_r': record.get('result_r'), 'study_note': record.get('study_note')}}
                    if include_bytes:
                        item['image_base64'] = row['image_base64']
                    output[row['id']] = item
            record['photo_count'] = len(record['photos'])
            record['photos_available'] = True
    return list(output.values())
