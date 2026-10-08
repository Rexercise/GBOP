"""Backend-only, member-owned unfinished journals, separate from performance rows.

Payloads contain narrative and audit facts only. No credentials or audio buffers
are retained. Callers hold the normal authenticated member transaction guard.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

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



def substantive_raw_passages(draft):
    """Keep narration for recency without counting pure read/resume/save commands.

    Original passages remain stored untouched. This projection is only an
    ordering aid; it never supplies missing trade dates or performance facts.
    """
    passages=draft.get('raw_story') or []
    if not isinstance(passages,list):return []
    output=[]
    control_words=set(('the my this that a an new same unfinished saved current last latest '
        'reported journal journaling draft story narration trade record recording it now '
        'please again only up there one').split())
    for passage in passages:
        if not isinstance(passage,dict) or not isinstance(passage.get('text'),str):continue
        text=passage['text'].strip()
        normalized=text.casefold().replace('’',"'")
        normalized=re.sub(r'\b(trade|journal|draft)\s*#?\s*\d+\b(?![-/:]\d)',r'\1',normalized)
        words=re.findall(r"[^\W_]+(?:'[^\W_]+)?",normalized)
        if not text:continue
        if not words:
            output.append(passage)
            continue
        if words in (['yes'],['no'],['okay'],['ok'],['sure'],['thanks'],['yes','please']):continue
        for prefix in (['can','you'],['could','you'],['would','you'],['i','want','to'],['i','need','to'],["let's"]):
            if words[:len(prefix)]==prefix:words=words[len(prefix):];break
        while words and words[0] in ('please','okay','ok','yes'):words=words[1:]
        if words and words[0] in ('start','resume','continue','read','show','open','get','save','finish',
                'finalize','finalise','pause','stop','journal','log','record') and all(word in control_words for word in words[1:]):
            continue
        output.append(passage)
    return output


def substantive_content(draft):
    """Member narrative/facts only; bookkeeping/provenance clocks are excluded."""
    values=deepcopy(draft.get('values') or {})
    if not isinstance(values,dict):values={}
    provenance=draft.get('provenance') or {}
    title_provenance=provenance.get('title') if isinstance(provenance,dict) else None
    if isinstance(title_provenance,dict) and title_provenance.get('source')=='derived_title':
        values.pop('title',None)
    values={key:value for key,value in values.items() if value not in (None,'',[],{})}
    if isinstance(values.get('entries'),list):
        entries=[entry for entry in values['entries'] if isinstance(entry,dict)
                 and any(key!='entry_index' and value not in (None,'',[],{}) for key,value in entry.items())]
        if entries:values['entries']=entries
        else:values.pop('entries',None)
    return {'values':values,'narration':[p['text'] for p in substantive_raw_passages(draft)]}

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
    # Only a changed factual/narrative projection moves activity recency.
    # A receipt, delivered question, pause/resume or identical patch cannot.
    before=substantive_content(current[0]) if current else {'values':{},'narration':[]}
    after=substantive_content(value)
    if after!=before:
        value['substantive_updated_at']=now
    elif current and 'substantive_updated_at' in current[0]:
        value['substantive_updated_at']=current[0]['substantive_updated_at']
    else:
        value.pop('substantive_updated_at',None)
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
