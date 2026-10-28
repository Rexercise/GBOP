"""Owned photo evidence for a reviewed recap; never execution inference."""
from copy import deepcopy
import json


def validate_sources(conn, guild, user, sources):
    if not isinstance(sources, list) or len(sources) > 12:
        raise ValueError('Use at most twelve photo sources for one setup.')
    result, keys = [], set()
    for source in sources:
        if not isinstance(source, dict) or set(source) - {'photo_id', 'entry_index', 'transcription'}:
            raise ValueError('Photo sources use photo_id, page entry_index and optional visible transcription.')
        photo_id, index = source.get('photo_id'), source.get('entry_index', 1)
        if not isinstance(photo_id, str) or not 1 <= len(photo_id) <= 128 or type(index) is not int or not 1 <= index <= 50:
            raise ValueError('Use an uploaded photo and its stable page entry index.')
        transcript = source.get('transcription', '')
        if not isinstance(transcript, str) or len(transcript) > 6000:
            raise ValueError('Keep photo transcription bounded; mark unreadable text as unclear.')
        if not conn.execute('SELECT id FROM trade_photos WHERE id=? AND guild_id=? AND user_id=?',
                            (photo_id, guild, user)).fetchone():
            raise ValueError('Photo not found in your account. No journal was changed.')
        key = (photo_id, index)
        if key in keys:
            raise ValueError('Use each photo page entry once per setup.')
        keys.add(key)
        result.append(dict(photo_id=photo_id, entry_index=index, transcription=transcript))
    return result


def merge_sources(conn, guild, user, existing, sources):
    merged = {(s['photo_id'], s['entry_index']): deepcopy(s) for s in existing or []}
    for source in validate_sources(conn, guild, user, sources):
        key = (source['photo_id'], source['entry_index'])
        if not source['transcription'] and key in merged:
            source['transcription'] = merged[key].get('transcription', '')
        merged[key] = source
    if len(merged) > 12:
        raise ValueError('This setup already has twelve photo sources; none were removed.')
    return list(merged.values())


def attach_sources(conn, guild, user, journal_id, sources):
    from gbop_voice_web.unified_journal import attach_journal_source
    for source in validate_sources(conn, guild, user, sources):
        attach_journal_source(conn, guild, user, journal_id, source['photo_id'], source['entry_index'])
    row = conn.execute('SELECT metadata FROM journal_details WHERE journal_id=? AND guild_id=? AND user_id=?',
                       (journal_id, guild, user)).fetchone()
    metadata = json.loads(row['metadata'] or '{}')
    metadata['journal_photo_sources'] = deepcopy(sources)
    conn.execute('UPDATE journal_details SET metadata=? WHERE journal_id=? AND guild_id=? AND user_id=?',
                 (json.dumps(metadata, ensure_ascii=False), journal_id, guild, user))


def sources_saved(conn, guild, user, journal_id, sources):
    from gbop_voice_web.unified_journal import source_journal_id
    if not sources:
        return True
    try:
        validate_sources(conn, guild, user, sources)
    except ValueError:
        return False
    row = conn.execute('SELECT metadata FROM journal_details WHERE journal_id=? AND guild_id=? AND user_id=?',
                       (journal_id, guild, user)).fetchone()
    if not row or json.loads(row['metadata'] or '{}').get('journal_photo_sources') != sources:
        return False
    return all(source_journal_id(conn, guild, user, s['photo_id'], s['entry_index']) == journal_id for s in sources)
