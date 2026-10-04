"""One mutable journal per owned thesis, with immutable audit in existing events.

No schema migration or inferred legacy reassignment. Call writes inside the
conversation/member-fenced transaction. Advisory locks serialize all thesis writes
for the member; they are transaction-scoped (including canonical initialization).
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

CANONICAL_EVENT = 'journal_canonical_v1'
AUDIT_EVENT = 'journal_audit_v1'
SOURCE_EVENT = 'journal_source_v1'
FIELDS = ('description', 'rule_adherence', 'result_r', 'study_note')


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _events(conn, guild, user, thesis_id, event):
    if not conn.execute('PRAGMA table_info(thesis_events)').fetchall():
        return []
    return conn.execute('SELECT details FROM thesis_events WHERE thesis_id=? AND guild_id=? AND user_id=? AND event=? ORDER BY id',
                        (thesis_id, guild, user, event)).fetchall()


def canonical_journal_id(conn, guild, user, thesis_id):
    """Read-only identity: explicit marker wins; only a unique legacy row is safe."""
    rows = conn.execute('SELECT id FROM journals WHERE thesis_id=? AND guild_id=? AND user_id=? ORDER BY id',
                        (thesis_id, guild, user)).fetchall()
    ids = {r['id'] for r in rows}
    markers = _events(conn, guild, user, thesis_id, CANONICAL_EVENT)
    for event in reversed(markers):
        candidate = json.loads(event['details'] or '{}').get('journal_id')
        if candidate in ids:
            return candidate
    # A removed canonical record never promotes preserved legacy history.
    return rows[0]['id'] if len(rows) == 1 and not markers else None


def _event(conn, guild, user, thesis_id, event, details, timestamp):
    conn.execute('INSERT INTO thesis_events (thesis_id,guild_id,user_id,event,details,result_r,created_at) VALUES (?,?,?,?,?,NULL,?)',
                 (thesis_id, guild, user, event, _json(details), timestamp))


def owned_journal_details(conn, guild, user, journal_id):
    """Fail closed on inconsistent historical ownership before any overwrite."""
    row = conn.execute('SELECT * FROM journal_details WHERE journal_id=?', (journal_id,)).fetchone()
    if row and (row['guild_id'], row['user_id']) != (guild, user):
        raise ValueError('Journal detail ownership is inconsistent. No changes were saved; review this legacy record.')
    return dict(row) if row else None


def _snapshot(row, details):
    return {'journal': dict(row) if row else None, 'details': deepcopy(details)}


def ensure_canonical_journal(conn, guild, user, thesis_id, fields=None, metadata=None, timestamp=None):
    """Return canonical journal ID; update only supplied fields and retain audit.

    Explicit result_r=None clears the result. Omitted fields preserve their value.
    Text None preserves existing text. Metadata is a trusted server patch/merged
    object; caller validates member keys. Every material edit appends full before
    and after snapshots, so bounded presentation never removes durable history.
    """
    timestamp = timestamp or _stamp()
    fields = dict(fields or {})
    if set(fields) - set(FIELDS):
        raise ValueError('Unsupported canonical journal fields.')
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError('Journal metadata must be an object.')
    conn.execute('SELECT pg_advisory_xact_lock(?)', (user,))
    from gbop_voice_web.journal_context import member_revision
    member_revision(conn, guild, user)
    thesis = conn.execute('SELECT * FROM theses WHERE id=? AND guild_id=? AND user_id=?',
                          (thesis_id, guild, user)).fetchone()
    if not thesis:
        raise ValueError('Trade not found in your account.')
    journal_id = canonical_journal_id(conn, guild, user, thesis_id)
    old = conn.execute('SELECT * FROM journals WHERE id=? AND guild_id=? AND user_id=?',
                       (journal_id, guild, user)).fetchone() if journal_id else None
    previous_details = owned_journal_details(conn, guild, user, journal_id) if old else None
    before = _snapshot(old, previous_details)
    legacy = []
    prior_meta = json.loads(previous_details['metadata'] or '{}') if previous_details else {}
    if old:
        values = {key: old[key] for key in FIELDS}
    else:
        legacy = conn.execute('SELECT * FROM journals WHERE thesis_id=? AND guild_id=? AND user_id=? ORDER BY id',
                              (thesis_id, guild, user)).fetchall()
        values = dict(description='', rule_adherence='', result_r=None, study_note='')
        if legacy:
            # Preserve every legacy row byte-for-byte. Only uncontested facts
            # initialize the canonical view; conflicts remain explicit unknowns.
            for key in FIELDS:
                candidates = [r[key] for r in legacy]
                values[key] = candidates[0] if all(v == candidates[0] for v in candidates) else (None if key == 'result_r' else '')
            conflicts = [key for key in FIELDS if len({_json(r[key]) for r in legacy}) > 1]
            prior_meta['legacy_history'] = {'journal_ids': [r['id'] for r in legacy],
                                          'conflicting_fields': conflicts, 'needs_clarification': bool(conflicts)}
            # Canonical metadata retains all source references, without moving
            # a unique legacy primary attachment or guessing its ownership.
            sources = []
            for record in legacy:
                d = owned_journal_details(conn, guild, user, record['id'])
                if d and d.get('photo_id'):
                    sources.append({'photo_id': d['photo_id'], 'entry_index': d['entry_index'], 'legacy_journal_id': record['id']})
            if sources:
                prior_meta['source_attachments'] = sources
        if not values['description']:
            item = dict(thesis)
            values['description'] = 'Trade journal: ' + ' · '.join(str(item.get(k) or 'Not specified') for k in ('asset', 'direction', 'play'))
    for key, value in fields.items():
        if value is not None or key == 'result_r':
            values[key] = value
    if not isinstance(values['description'], str) or not values['description'].strip():
        raise ValueError('No readable journal details. Ask for a clearer picture or description.')
    merged = deepcopy(prior_meta)
    if metadata is not None:
        merged.update(deepcopy(metadata))
        # Callers can send new trade context as a patch. Never erase existing
        # source links or correction history with an older context snapshot.
        for key in ('source_attachments',):
            items = prior_meta.get(key, []) + metadata.get(key, [])
            if items:
                merged[key] = list({_json(v): v for v in items}.values())
        if prior_meta.get('provenance'):
            prov = {**deepcopy(prior_meta['provenance']), **deepcopy(metadata.get('provenance') or {})}
            corrections = prior_meta['provenance'].get('corrections', []) + (metadata.get('provenance') or {}).get('corrections', [])
            if corrections:
                prov['corrections'] = list({_json(v): v for v in corrections}.values())
            merged['provenance'] = prov
    if old:
        if any(values[key] != old[key] for key in FIELDS):
            conn.execute('UPDATE journals SET description=?,rule_adherence=?,result_r=?,study_note=? WHERE id=? AND guild_id=? AND user_id=?',
                         tuple(values[k] for k in FIELDS) + (journal_id, guild, user))
    else:
        cur = conn.execute('INSERT INTO journals (description,rule_adherence,result_r,study_note,guild_id,user_id,created_at,thesis_id) VALUES (?,?,?,?,?,?,?,?)',
                           tuple(values[k] for k in FIELDS) + (guild, user, timestamp, thesis_id))
        journal_id = cur.lastrowid
    marked = any(json.loads(e['details'] or '{}').get('journal_id') == journal_id
                 for e in _events(conn, guild, user, thesis_id, CANONICAL_EVENT))
    if not marked:
        _event(conn, guild, user, thesis_id, CANONICAL_EVENT,
               {'version': 1, 'journal_id': journal_id, 'legacy_journal_ids': [r['id'] for r in legacy]}, timestamp)
    if merged != prior_meta or (not previous_details and merged):
        conn.execute('''INSERT INTO journal_details (journal_id,guild_id,user_id,entry_index,metadata,updated_at)
            VALUES (?,?,?,1,?,?) ON CONFLICT(journal_id) DO UPDATE SET metadata=excluded.metadata,updated_at=excluded.updated_at
            WHERE journal_details.guild_id=excluded.guild_id AND journal_details.user_id=excluded.user_id''',
                     (journal_id, guild, user, _json(merged), timestamp))
    after_row = conn.execute('SELECT * FROM journals WHERE id=? AND guild_id=? AND user_id=?',
                             (journal_id, guild, user)).fetchone()
    after = _snapshot(after_row, owned_journal_details(conn, guild, user, journal_id))
    if before != after:
        _event(conn, guild, user, thesis_id, AUDIT_EVENT,
               {'version': 1, 'journal_id': journal_id, 'before': before, 'after': after,
                'legacy_journal_ids': [r['id'] for r in legacy]}, timestamp)
    return journal_id


def source_journal_id(conn, guild, user, photo_id, entry_index=1):
    """Resolve an exact saved page entry, never by contents/date/instrument."""
    row = conn.execute('SELECT journal_id FROM journal_details WHERE guild_id=? AND user_id=? AND photo_id=? AND entry_index=?',
                       (guild, user, photo_id, entry_index)).fetchone()
    candidates = [row['journal_id']] if row else []
    for detail in conn.execute('SELECT journal_id,metadata FROM journal_details WHERE guild_id=? AND user_id=? ORDER BY journal_id',
                               (guild, user)).fetchall():
        sources = json.loads(detail['metadata'] or '{}').get('source_attachments', [])
        if any(s.get('photo_id') == photo_id and s.get('entry_index') == entry_index for s in sources):
            candidates.append(detail['journal_id'])
    events = conn.execute('SELECT details FROM thesis_events WHERE guild_id=? AND user_id=? AND event=? ORDER BY id',
                          (guild, user, SOURCE_EVENT)).fetchall()
    for event in events:
        data = json.loads(event['details'] or '{}')
        if data.get('photo_id') == photo_id and data.get('entry_index') == entry_index:
            candidates.append(data.get('journal_id'))
    for candidate in reversed(candidates):
        own = conn.execute('SELECT id FROM journals WHERE id=? AND guild_id=? AND user_id=?',
                           (candidate, guild, user)).fetchone()
        if own:
            return own['id']
    return None


def attach_journal_source(conn, guild, user, journal_id, photo_id, entry_index=1, timestamp=None):
    """Idempotent multi-photo source association; keep the original primary link."""
    timestamp = timestamp or _stamp()
    conn.execute('SELECT pg_advisory_xact_lock(?)', (user,))
    from gbop_voice_web.journal_context import member_revision
    member_revision(conn, guild, user)
    row = conn.execute('SELECT * FROM journals WHERE id=? AND guild_id=? AND user_id=?',
                       (journal_id, guild, user)).fetchone()
    photo = conn.execute('SELECT id FROM trade_photos WHERE id=? AND guild_id=? AND user_id=?',
                         (photo_id, guild, user)).fetchone()
    if not row or not photo:
        raise ValueError('Journal or photo not found in your account.')
    existing = source_journal_id(conn, guild, user, photo_id, entry_index)
    if existing is not None and existing != journal_id:
        other = conn.execute('SELECT thesis_id FROM journals WHERE id=? AND guild_id=? AND user_id=?',
                             (existing, guild, user)).fetchone()
        if not other or not other['thesis_id'] or other['thesis_id'] != row['thesis_id']:
            raise ValueError('That page entry is already attached to another journal.')
    d = owned_journal_details(conn, guild, user, journal_id)
    metadata = json.loads(d['metadata'] or '{}') if d else {}
    source = {'photo_id': photo_id, 'entry_index': entry_index}
    sources = metadata.setdefault('source_attachments', [])
    if not any(s.get('photo_id') == photo_id and s.get('entry_index') == entry_index for s in sources):
        sources.append(source)
        ensure_canonical_journal(conn, guild, user, row['thesis_id'], metadata=metadata, timestamp=timestamp)
    # Only claim the primary slot when it is unused and no legacy primary owns
    # this exact source. Other sources remain searchable through immutable events.
    primary = conn.execute('SELECT journal_id FROM journal_details WHERE guild_id=? AND user_id=? AND photo_id=? AND entry_index=?',
                           (guild, user, photo_id, entry_index)).fetchone()
    if (not d or not d.get('photo_id')) and not primary:
        conn.execute('UPDATE journal_details SET photo_id=?,entry_index=?,updated_at=? WHERE journal_id=? AND guild_id=? AND user_id=?',
                     (photo_id, entry_index, timestamp, journal_id, guild, user))
    if existing != journal_id:
        _event(conn, guild, user, row['thesis_id'], SOURCE_EVENT,
               {'version': 1, 'journal_id': journal_id, **source}, timestamp)


def create_journal_thesis(conn, guild, user, metadata, timestamp=None):
    """A saved journal identity is not an execution or an open risk position."""
    timestamp = timestamp or _stamp()
    text = lambda key: metadata.get(key) or 'Not specified'
    cur = conn.execute('''INSERT INTO theses (guild_id,user_id,asset,direction,play,session,
        objective,thesis_invalidation,status,max_r,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,0,?)''',
        (guild, user, text('asset'), text('direction'), text('play'), metadata.get('session'),
         'Not specified', 'Not specified', 'JOURNALED' if metadata.get('kind') == 'trade' else 'IDEA', timestamp))
    return cur.lastrowid


def sync_thesis_fields(conn, guild, user, thesis_id, journal_id, fields, timestamp=None):
    """Synchronize explicitly reported journal facts with the same trade identity.

    No risk/execution/status columns are accepted. Original values are retained
    in immutable full snapshots using the existing journal audit event stream.
    """
    allowed = {'asset', 'direction', 'play', 'session', 'final_result_r', 'close_note'}
    if set(fields) - allowed:
        raise ValueError('Unsupported journal-to-trade correction.')
    conn.execute('SELECT pg_advisory_xact_lock(?)', (user,))
    from gbop_voice_web.journal_context import member_revision
    member_revision(conn,guild,user)
    row = conn.execute('SELECT * FROM theses WHERE id=? AND guild_id=? AND user_id=?',
                       (thesis_id,guild,user)).fetchone()
    journal = conn.execute('SELECT * FROM journals WHERE id=? AND thesis_id=? AND guild_id=? AND user_id=?',
                           (journal_id,thesis_id,guild,user)).fetchone()
    if not row or not journal:
        raise ValueError('Trade or journal not found in your account.')
    details = owned_journal_details(conn,guild,user,journal_id)
    patch = {key:value for key,value in fields.items() if row[key] != value}
    if not patch:
        return
    before = {**_snapshot(journal,details), 'thesis':dict(row)}
    conn.execute('UPDATE theses SET '+','.join(key+'=?' for key in patch)+' WHERE id=? AND guild_id=? AND user_id=?',
                 tuple(patch.values())+(thesis_id,guild,user))
    after_row = conn.execute('SELECT * FROM theses WHERE id=? AND guild_id=? AND user_id=?',
                             (thesis_id,guild,user)).fetchone()
    after = {**_snapshot(journal,details), 'thesis':dict(after_row)}
    _event(conn,guild,user,thesis_id,AUDIT_EVENT,
           {'version':1,'journal_id':journal_id,'before':before,'after':after,
            'member_corrected_thesis_fields':list(patch)}, timestamp or _stamp())
