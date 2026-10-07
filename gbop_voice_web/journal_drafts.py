"""Backend-only, member-owned unfinished journals, separate from performance rows.

Payloads contain narrative and audit facts only. No credentials or audio buffers
are retained. Callers hold the normal authenticated member transaction guard.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

SCHEMA_SQL = [
    '''CREATE TABLE IF NOT EXISTS journal_story_drafts (
        id TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('unfinished','finalized')),
        revision INTEGER NOT NULL, payload TEXT NOT NULL,
        journal_id INTEGER REFERENCES journals(id) ON DELETE CASCADE,
        thesis_id INTEGER REFERENCES theses(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY(guild_id,user_id) REFERENCES members(guild_id,user_id) ON DELETE CASCADE)''',
    '''CREATE INDEX IF NOT EXISTS journal_story_drafts_owner
        ON journal_story_drafts(guild_id,user_id,status,updated_at)''',
    'ALTER TABLE journal_story_drafts ENABLE ROW LEVEL SECURITY',
    'REVOKE ALL ON journal_story_drafts FROM PUBLIC, anon, authenticated',
]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def available(conn):
    return bool(conn.execute('PRAGMA table_info(journal_story_drafts)').fetchall())


def read(conn, guild, user, draft_id=None):
    if not available(conn):
        return []
    if draft_id is not None:
        rows = conn.execute('SELECT * FROM journal_story_drafts WHERE id=? AND guild_id=? AND user_id=?',
                            (draft_id,guild,user)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM journal_story_drafts WHERE guild_id=? AND user_id=? AND status='unfinished' ORDER BY updated_at DESC,id",
                            (guild,user)).fetchall()
    result=[]
    for row in rows:
        value=json.loads(row['payload'])
        if value.get('id') != row['id'] or tuple(value.get('member',())) != (guild,user):
            raise ValueError('This unfinished journal has inconsistent ownership. Nothing changed.')
        value.update(storage_revision=row['revision'],persisted=True,created_at=row['created_at'],
                     updated_at=row['updated_at'],draft_status=row['status'])
        if value.get('selected_key') is not None:value['selected_key']=tuple(value['selected_key'])
        result.append(value)
    return result


def write(conn, guild, user, draft, *, expected_revision=None, finalized=False, journal_id=None, thesis_id=None):
    """Optimistic version check complements the shared per-member transaction lock."""
    current=read(conn,guild,user,draft['id'])
    if current and expected_revision != current[0]['storage_revision']:
        raise ValueError('This unfinished journal changed in another conversation. Read the same draft again before correcting it.')
    if not current and expected_revision is not None:
        raise ValueError('This unfinished journal was deleted. It will not be recreated automatically.')
    now=stamp();value=deepcopy(draft)
    for key in ('owner','storage_revision','persisted','question','draft_status','updated_at'):
        value.pop(key,None)
    value['member']=[guild,user]
    value.setdefault('created_at',now)
    payload=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if len(payload.encode('utf-8'))>262144:
        raise ValueError('This journal is too large to add another passage safely. Existing saved narration is intact; start a separate journal.')
    status='finalized' if finalized else 'unfinished'
    old=conn.execute('SELECT payload,status,journal_id,thesis_id FROM journal_story_drafts WHERE id=? AND guild_id=? AND user_id=?',
                     (draft['id'],guild,user)).fetchone() if current else None
    # Keep links when an existing finalized journal receives an unfinished correction.
    journal_id=journal_id if journal_id is not None else (old['journal_id'] if old else None)
    thesis_id=thesis_id if thesis_id is not None else (old['thesis_id'] if old else None)
    if old and (old['payload'],old['status'],old['journal_id'],old['thesis_id'])==(payload,status,journal_id,thesis_id):
        return current[0]
    revision=(current[0]['storage_revision']+1) if current else 1
    if current:
        conn.execute('''UPDATE journal_story_drafts SET status=?,revision=?,payload=?,journal_id=?,thesis_id=?,updated_at=?
            WHERE id=? AND guild_id=? AND user_id=? AND revision=?''',
            (status,revision,payload,journal_id,thesis_id,now,draft['id'],guild,user,expected_revision))
    else:
        conn.execute('''INSERT INTO journal_story_drafts
            (id,guild_id,user_id,status,revision,payload,journal_id,thesis_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (draft['id'],guild,user,status,revision,payload,journal_id,thesis_id,value['created_at'],now))
    return read(conn,guild,user,draft['id'])[0]
