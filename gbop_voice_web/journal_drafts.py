"""Backend-only, member-owned unfinished journals, separate from performance rows.

Payloads contain narrative and audit facts only. No credentials or audio buffers
are retained. Callers hold the normal authenticated member transaction guard.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re

PAYLOAD_LIMIT = 262144
DISCARD_HISTORY_LIMIT = 32
_DISCARD_KEYS = ('_discarded', '_discard_history')
_DISCARD_PREFIX = '{"_discarded":'
DISCARDED_PAGE_LIMIT = 20
DISCARDED_MAX_OFFSET = 10000

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


def _project_row(row, guild, user):
    value=json.loads(row['payload'])
    if not isinstance(value,dict) or value.get('id')!=row['id'] or tuple(value.get('member',()))!=(guild,user):
        raise ValueError('This unfinished journal has inconsistent ownership. Nothing changed.')
    value.update(storage_revision=row['revision'],persisted=True,created_at=row['created_at'],
                 updated_at=row['updated_at'],draft_status='discarded' if '_discarded' in value else row['status'])
    if value.get('selected_key') is not None:value['selected_key']=tuple(value['selected_key'])
    return value


def read(conn, guild, user, draft_id=None, *, include_discarded=False):
    """Archived narration is only visible to explicit backend recovery reads."""
    if not available(conn):
        return []
    # Archive serialization puts this reserved key first. Filter before fetching
    # narration, using a portable literal prefix comparison (not LIKE, whose
    # underscore is a wildcard). Quoted narration and nested keys cannot match.
    active_sql='' if include_discarded else ' AND SUBSTR(payload,1,?)<>?'
    active_args=() if include_discarded else (len(_DISCARD_PREFIX),_DISCARD_PREFIX)
    if draft_id is not None:
        rows = conn.execute('SELECT * FROM journal_story_drafts WHERE id=? AND guild_id=? AND user_id=?'+active_sql,
                            (draft_id,guild,user)+active_args).fetchall()
    else:
        rows = conn.execute("SELECT * FROM journal_story_drafts WHERE guild_id=? AND user_id=? AND status='unfinished'"+
                            active_sql+' ORDER BY updated_at DESC,id',(guild,user)+active_args).fetchall()
    result=[]
    for row in rows:
        value=_project_row(row,guild,user)
        # Presence, rather than truthiness, fails closed for malformed markers.
        if value['draft_status']=='discarded' and not include_discarded:
            continue
        result.append(value)
    return result


def read_discarded_page(conn, guild, user, offset=0, limit=10):
    """Bounded recovery list of canonical archives; never downloads all history.

    Returns drafts, next_offset (or None), and has_more. At the offset safety
    bound, pagination_limit_reached is true and next_offset is None even if more
    archives exist. Legacy/noncompact JSON
    cannot be faithfully paged with one portable SQLite/Postgres predicate;
    explicit read(..., include_discarded=True) still supports its recovery.
    """
    if type(offset) is not int or not 0<=offset<=DISCARDED_MAX_OFFSET:
        raise ValueError('Discarded draft offset must be an integer from 0 to 10000.')
    if type(limit) is not int or not 1<=limit<=DISCARDED_PAGE_LIMIT:
        raise ValueError('Discarded draft limit must be an integer from 1 to 20.')
    if not available(conn):
        return {'drafts':[],'next_offset':None,'has_more':False}
    rows=conn.execute('''SELECT * FROM journal_story_drafts
        WHERE guild_id=? AND user_id=? AND status='unfinished' AND SUBSTR(payload,1,?)=?
        ORDER BY updated_at DESC,id LIMIT ? OFFSET ?''',
        (guild,user,len(_DISCARD_PREFIX),_DISCARD_PREFIX,limit+1,offset)).fetchall()
    values=[_project_row(row,guild,user) for row in rows[:limit]]
    # Keep a parsed top-level check even though canonical SQL prefix is exact.
    drafts=[value for value in values if value['draft_status']=='discarded']
    has_more=len(rows)>limit
    result={'drafts':drafts,'next_offset':offset+limit if has_more else None,'has_more':has_more}
    if has_more and offset+limit>DISCARDED_MAX_OFFSET:
        result.update(next_offset=None,pagination_limit_reached=True)
    return result


def _owned_row(conn, guild, user, draft_id):
    if not available(conn):
        raise ValueError('This unfinished journal is unavailable. Nothing changed.')
    row=conn.execute('SELECT * FROM journal_story_drafts WHERE id=? AND guild_id=? AND user_id=?',
                     (draft_id,guild,user)).fetchone()
    if row is None:
        raise ValueError('This unfinished journal is unavailable. Nothing changed.')
    value=json.loads(row['payload'])
    if not isinstance(value,dict) or value.get('id')!=draft_id or tuple(value.get('member',()))!=(guild,user):
        raise ValueError('This unfinished journal has inconsistent ownership. Nothing changed.')
    return row,value


def _require_unlinked(conn, guild, user, row, value):
    """Never hide canonical journals, including unfinished corrections of them."""
    if (row['status']!='unfinished' or row['journal_id'] is not None or row['thesis_id'] is not None
            or any(value.get(key) is not None for key in
                   ('saved_journal_id','selected_key','trade_number','saved_revision'))):
        raise ValueError('This draft is saved or linked to a trade. It cannot be discarded or restored this way.')
    # Finalization normally writes both links atomically. Check the durable
    # metadata too, so old/recovered rows with missing links remain protected.
    rows=conn.execute('''SELECT d.metadata FROM journals j JOIN journal_details d
        ON d.journal_id=j.id AND d.guild_id=j.guild_id AND d.user_id=j.user_id
        WHERE j.guild_id=? AND j.user_id=?''',(guild,user)).fetchall()
    for saved in rows:
        try:
            metadata=json.loads(saved['metadata'] or '{}')
            story=metadata.get('journal_story')
            story=json.loads(story) if isinstance(story,str) else story
        except (ValueError,TypeError,AttributeError):
            continue
        if isinstance(story,dict) and story.get('draft_id')==row['id']:
            raise ValueError('This draft is saved or linked to a trade. It cannot be discarded or restored this way.')


def ensure_discardable(conn, guild, user, draft):
    """Read-only preview eligibility; discard repeats it under the member lock."""
    row,value=_owned_row(conn,guild,user,draft['id'])
    _require_unlinked(conn,guild,user,row,value)
    if '_discarded' in value:
        raise ValueError('This unfinished journal was already discarded. Nothing changed.')
    if type(draft.get('storage_revision')) is not int or draft['storage_revision']!=row['revision']:
        raise ValueError('This unfinished journal changed in another conversation. Read the same draft again.')


def _change_discard_state(conn, guild, user, draft_id, expected_revision, *, restoring):
    """Caller must hold journal_transaction(..., serialize=True)."""
    if type(expected_revision) is not int or expected_revision<1:
        raise ValueError('Read the same unfinished journal before changing its discard state.')
    row,value=_owned_row(conn,guild,user,draft_id)
    _require_unlinked(conn,guild,user,row,value)
    history=value.get('_discard_history',[])
    if not isinstance(history,list) or not all(isinstance(item,dict) for item in history):
        raise ValueError('This draft has inconsistent discard history. Nothing changed.')
    active='_discarded' in value
    action='restore' if restoring else 'discard'
    last=history[-1] if history else None
    # Only the exact same transition may be retried without advancing revision.
    # A restore, edit or subsequent discard invalidates every older request.
    if active != restoring:
        if (last and last.get('action')==action and last.get('from_revision')==expected_revision
                and last.get('to_revision')==row['revision']
                and (restoring or value['_discarded']==last)):
            return read(conn,guild,user,draft_id,include_discarded=True)[0]
        raise ValueError('This draft changed or its discard state changed. Read the same draft again.')
    if expected_revision!=row['revision']:
        raise ValueError('This unfinished journal changed in another conversation. Read the same draft again before discarding or restoring it.')
    revision=row['revision']+1
    event={'action':action,'from_revision':row['revision'],'to_revision':revision,'at':stamp()}
    value['_discard_history']=(history+[event])[-DISCARD_HISTORY_LIMIT:]
    # Recovery must not replay old permission to finalize. A member's recording
    # pause remains untouched, as do every narrated fact and correction.
    value['save_authorized']=False
    if restoring:
        value.pop('_discarded')
    else:
        value['_discarded']=deepcopy(event)
        # Keep a literal top-level prefix for DB-side active-read filtering.
        # No narrated field or existing audit value changes when keys reorder.
        value={'_discarded':value['_discarded'],**value}
    payload=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    # Only our bounded transition history may shrink to fit. Narration, field
    # corrections, provenance and existing revision audit are never truncated.
    while len(payload.encode('utf-8'))>PAYLOAD_LIMIT and len(value['_discard_history'])>1:
        value['_discard_history'].pop(0)
        payload=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if len(payload.encode('utf-8'))>PAYLOAD_LIMIT:
        raise ValueError('This journal is too large to change its discard state safely. Existing narration is intact.')
    changed=conn.execute('''UPDATE journal_story_drafts SET revision=?,payload=?
        WHERE id=? AND guild_id=? AND user_id=? AND revision=? AND status='unfinished'
        AND journal_id IS NULL AND thesis_id IS NULL RETURNING id''',
        (revision,payload,draft_id,guild,user,expected_revision)).fetchone()
    if changed is None:
        raise ValueError('This unfinished journal changed in another conversation. Nothing changed.')
    # Do not move updated_at or substantive_updated_at for housekeeping actions.
    return read(conn,guild,user,draft_id,include_discarded=True)[0]


def discard(conn, guild, user, draft_id, *, expected_revision):
    """Soft-discard one owned, unlinked draft under the caller's member lock."""
    return _change_discard_state(conn,guild,user,draft_id,expected_revision,restoring=False)


def restore(conn, guild, user, draft_id, *, expected_revision):
    """Recover archived narration without touching any performance records."""
    return _change_discard_state(conn,guild,user,draft_id,expected_revision,restoring=True)



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
    current=read(conn,guild,user,draft['id'],include_discarded=True)
    if current and current[0]['draft_status']=='discarded':
        raise ValueError('This unfinished journal was discarded. Restore it before making changes; it will not be recreated automatically.')
    if current and expected_revision != current[0]['storage_revision']:
        raise ValueError('This unfinished journal changed in another conversation. Read the same draft again before correcting it.')
    if not current and expected_revision is not None:
        raise ValueError('This unfinished journal was deleted. It will not be recreated automatically.')
    if not current and conn.execute('SELECT id FROM journal_story_drafts WHERE id=?',(draft['id'],)).fetchone():
        raise ValueError('This unfinished journal is unavailable. Nothing changed.')
    now=stamp();value=deepcopy(draft)
    for key in _DISCARD_KEYS:
        existing=current[0].get(key) if current else None
        if key in value and (not current or key not in current[0] or value[key]!=existing):
            raise ValueError('Discard state and history are managed by the journal storage service. Nothing changed.')
        if current and key in current[0]:
            value[key]=deepcopy(existing)
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
    if len(payload.encode('utf-8'))>PAYLOAD_LIMIT:
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
        changed=conn.execute('''UPDATE journal_story_drafts SET status=?,revision=?,payload=?,journal_id=?,thesis_id=?,updated_at=?
            WHERE id=? AND guild_id=? AND user_id=? AND revision=? RETURNING id''',
            (status,revision,payload,journal_id,thesis_id,now,draft['id'],guild,user,expected_revision)).fetchone()
        if changed is None:
            raise ValueError('This unfinished journal changed in another conversation. Nothing changed.')
    else:
        conn.execute('''INSERT INTO journal_story_drafts
            (id,guild_id,user_id,status,revision,payload,journal_id,thesis_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (draft['id'],guild,user,status,revision,payload,journal_id,thesis_id,value['created_at'],now))
    return read(conn,guild,user,draft['id'])[0]
