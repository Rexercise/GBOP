"""Read-only reconciliation of one settled, member-owned draft finalization.

The existing final journal and draft status commit together. An unchanged
unfinished storage revision proves no finalization committed; an exact finalized
revision plus its owned journal proves success. Missing/changed evidence remains
unknown. This never replays a write, chooses another draft, or ages out a fence.
"""
from contextlib import contextmanager
from hashlib import sha256
import json

from gbop_voice_web.journal_context import JournalBinding, journal_transaction
from gbop_voice_web import journal_drafts


@contextmanager
def _bounded_connection(db):
    with db() as conn:
        # Transaction-local bounds, never persistent settings or schema writes.
        if hasattr(conn, '_conn'):
            conn.execute("SET LOCAL lock_timeout = '1500ms'")
            conn.execute("SET LOCAL statement_timeout = '4000ms'")
        yield conn


def _digest(values):
    return sha256(json.dumps(values, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def prepare_story_finalization(db, guild, user, args):
    from gbop_voice_web.journal_story import _context, _load_owned, _story_value
    capability, context = _context(args, guild, user)
    with journal_transaction(lambda: _bounded_connection(db), args, guild, user, serialize=True) as conn:
        draft = _load_owned(conn, context, args, guild, user)
        if not draft or not draft.get('persisted'):
            return None
        # Retain identities/digests only, never another copy of private prose.
        draft_id = draft['id']
        revision, storage_revision = draft['revision'], draft['storage_revision']
        values_digest = _digest(draft['values'])
        original_status = draft['draft_status']
        session_id, auth_revision = context.session_id, context._auth_revision

    def reconcile():
        # This reader is called only after its synchronous writer has returned.
        # Take the same DB member lock, then the current conversation fence, so
        # an ambiguous server commit settles before interpreting the snapshot.
        with context._lock:
            if (context.closed or context.session_id != session_id
                    or tuple(context.owner[:2]) != (guild, user)
                    or context._auth_revision != auth_revision):
                return None
            current = JournalBinding(context, context.generation)
        with journal_transaction(lambda: _bounded_connection(db), {'_journal_binding': current}, guild, user, serialize=True) as conn:
            if context.session_id != session_id or context._auth_revision != auth_revision:
                return None
            rows = journal_drafts.read(conn, guild, user, draft_id)
            if len(rows) != 1:
                return None
            stored = rows[0]
            if stored['revision'] != revision or _digest(stored['values']) != values_digest:
                return None  # A concurrent correction is not this operation's receipt.
            if (original_status == stored['draft_status'] == 'unfinished'
                    and stored['storage_revision'] == storage_revision):
                return {'ok': False, 'saved': False, 'status': 'journal_not_saved',
                        'draft_id': draft_id, 'revision': revision,
                        'draft_status': 'unfinished', 'reconciliation_verified': True,
                        'error': 'Finalization did not commit. The unfinished journal is intact. '
                                 'A fresh explicit finalize request can retry the same draft.'}
            if stored['draft_status'] != 'finalized' or stored.get('saved_revision') != revision:
                return None
            links = conn.execute('SELECT journal_id,thesis_id FROM journal_story_drafts WHERE id=? AND guild_id=? AND user_id=?',
                                 (draft_id, guild, user)).fetchone()
            row = conn.execute('''SELECT j.id,j.thesis_id,d.metadata FROM journals j
                JOIN journal_details d ON d.journal_id=j.id AND d.guild_id=j.guild_id AND d.user_id=j.user_id
                JOIN theses t ON t.id=j.thesis_id AND t.guild_id=j.guild_id AND t.user_id=j.user_id
                WHERE j.id=? AND j.guild_id=? AND j.user_id=?''',
                (links['journal_id'], guild, user)).fetchone()
            if (not row or row['thesis_id'] != links['thesis_id']
                    or stored.get('saved_journal_id') != row['id']):
                return None
            story = _story_value(row['metadata'])
            if (not story or story.get('draft_id') != draft_id or story.get('revision') != revision
                    or _digest({k: v for k, v in story.items() if k not in ('draft_id', 'revision')}) != values_digest):
                return None
            from gbop_voice_web.journal_numbers import journal_display
            display = next((r for r in journal_display(conn, guild, user) if r['id'] == row['id']), None)
            if not display:
                return None
            return {'ok': True, 'saved': True, 'status': 'saved', 'persisted': True,
                    'draft_id': draft_id, 'revision': revision, 'draft_status': 'finalized',
                    'trade_number': display['trade_number'], 'journal_number': display['journal_number'],
                    'reconciliation_verified': True, 'execution_records_changed': False}

    return reconcile
