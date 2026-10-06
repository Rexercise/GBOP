"""Authenticated session drafts for multi-entry reports; saving never invents executions."""
from copy import deepcopy
from datetime import date
from dataclasses import dataclass
import json
import math
import re
from uuid import uuid4

from gbop_voice_web.journal_context import binding, JournalTarget, JournalThesisTarget
from gbop_voice_web.journal_numbers import resolve_journal_selector
from gbop_voice_web.trade_photos import schema, STR, NUM

STORY_NAMES = {'stage_journal_story', 'get_journal_story', 'save_journal_story'}
FIELDS = {'asset','direction','play','trade_date','session','context_notes','thesis_invalidation','invalidation_boundary','reported_outcome'}
ENTRY_FIELDS = {'entry_index','entry_model','candle_label','risk_r','objective','status','pnl_text','notes'}
STORY_PROMPT = """
MULTI-ENTRY JOURNAL STORIES
When a member journals a sequence, capture the WHOLE reported story first with
stage_journal_story; it is an unsaved draft, not execution recording. Patch only
new/corrected facts. Keep stable entry_index values for first entry/add/re-entry.
'No, scratch that, I meant ...' repairs spoken draft wording; it never authorizes
record deletion. Replace the corrected draft field, preserving other entries.
Summarize known facts first. Use its next_question only when needed, at most once
when actually delivered; do not recite a form or ask again for supplied facts.
Unknown risk, P/L, fills and optional notes do not block saving a journal story.
A candle label is not an exact fill time. 'Took profit' alone does not prove fully
closed versus partial; ask once if material. A stated 'close above ten o'clock'
without a boundary stays verbatim and unresolved; never silently choose its high.
Save an authorized narrative with save_journal_story using the returned draft_id.
If an existing trade may match, read/reconcile it and select its displayed Trade #;
never retry open_trade or create another record because a save answer was interrupted.
A story save preserves reported entries as narrative only, not synthetic fills,
execution-risk rows, closes, or P/L. Existing known executions remain unchanged.
Only use open_trade/add_entry/close_trade for separately grounded actual executions
with their required risk/result facts; a journal-only Trade # can later be promoted
through open_trade trade_id instead of creating a duplicate. After saving, state
its actual returned Trade # and what remains unknown. Never say still saving once
terminal tool evidence says saved; unknown outcome requires a state read, not retry.
"""


@dataclass(frozen=True)
class StoryPayload:
    """Server-built, not an accepted JSON tool argument."""
    encoded: str
    new_trade: bool = False


def _save_optout(text):
    return bool(text and re.search(r"\b(?:don't|do not|never|not yet|hold off(?: on)?)\s+(?:save|saving|record|recording|log|logging|persist)|\b(?:cancel|stop)\s+(?:the\s+)?(?:save|saving|recording)|\b(?:just draft|draft only|preview only)\b",text.replace('’',"'"),re.I))


def _save_request(text):
    if text is None:
        return True  # Audio has no local ASR; the authenticated audio model classifies intent.
    if _save_optout(text):return False
    return bool(re.search(r"(?:^|[.;!?]\s*)(?:(?:please|okay|ok|yes)[, ]+|(?:can|could|would) you (?:please )?|(?:let's|lets|let us) )?(?:save|record|log|journal|document)\b|^I (?:want|need) you to (?:save|record|log|journal|document)\b",text,re.I))


def _new_trade_request(text):
    for match in re.finditer(r'\b(?:new|separate|different|another)\s+(?:reported\s+)?trade\b',text,re.I):
        prefix=re.split(r'[.;!?]',text[:match.start()])[-1]
        if not re.search(r"\b(?:not|no|never|isn't|is not|don't|do not|without)\b(?:[ ,]+[\w’'-]+){0,6}[ ,]*$",prefix,re.I):
            return True
    return False


def begin_story_turn(context,text):
    draft=getattr(context,'_journal_story',None)
    if draft and draft['owner']==(*context.owner[:2],context.session_id) and _save_optout(text):
        draft['save_authorized']=False


def _direction(value):
    return {'bearish':'Bearish','short':'Bearish','sell':'Bearish',
            'bullish':'Bullish','long':'Bullish','buy':'Bullish'}.get(str(value or '').casefold())


def _story_value(metadata):
    try:
        value=json.loads(metadata or '{}').get('journal_story')
        value=json.loads(value) if isinstance(value,str) else None
        return value if isinstance(value,dict) and isinstance(value.get('draft_id'),str) else None
    except (ValueError,TypeError,AttributeError):
        return None


def _context(args, guild, user):
    capability = binding(args)
    if capability is None:
        raise ValueError('Journal drafts require the current authenticated conversation.')
    return capability, capability.context


def _owned_draft(context, args, guild, user):
    draft = getattr(context, '_journal_story', None)
    if (not draft or draft['owner'] != (guild,user,context.session_id)
            or args.get('draft_id') not in (None,draft['id'])):
        raise ValueError('This draft is unavailable in the current member session. Read saved history before starting again.')
    return draft


def _text(value, label):
    if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>2500):
        raise ValueError(label+' must be nonempty reported text or null.')
    return value


def _patch(value):
    value = json.loads(value) if isinstance(value,str) else value
    if not isinstance(value,dict) or set(value)-FIELDS-{'entries','clear_fields'} or len(json.dumps(value))>16000:
        raise ValueError('Use a bounded journal story object with documented fields.')
    for key in FIELDS:
        if key in value:
            _text(value[key],key)
    if value.get('trade_date'):
        date.fromisoformat(value['trade_date'])
    if value.get('reported_outcome') not in (None,'open','win','loss','breakeven','stopped_out','unknown'):
        raise ValueError('Use an explicitly reported overall outcome or unknown.')
    entries = value.get('entries',[])
    if not isinstance(entries,list) or len(entries)>12:
        raise ValueError('Use at most twelve distinct reported entries.')
    indexes = set()
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)-ENTRY_FIELDS:
            raise ValueError('Use only documented reported-entry fields.')
        index=entry.get('entry_index')
        if type(index) is not int or not 1<=index<=12 or index in indexes:
            raise ValueError('Each entry needs one stable entry_index from 1 to 12.')
        indexes.add(index)
        for key in ENTRY_FIELDS-{'entry_index','risk_r'}:
            if key in entry: _text(entry[key],key)
        risk=entry.get('risk_r')
        if risk is not None and (isinstance(risk,bool) or not isinstance(risk,(int,float)) or not math.isfinite(risk) or risk<0):
            raise ValueError('Entry risk must be reported finite nonnegative R or null.')
        if entry.get('status') not in (None,'open','closed','partially_closed','unknown'):
            raise ValueError('Entry status must be open, closed, partially_closed, or unknown.')
    clears=value.get('clear_fields',[])
    if not isinstance(clears,list) or any(not isinstance(k,str) or not re.fullmatch(r'[a-z_]+|entries\.(?:[1-9]|1[0-2])\.[a-z_]+',k) for k in clears):
        raise ValueError('clear_fields must explicitly name corrected draft fields.')
    return value


def render_story(values):
    parts=[' · '.join(values.get(k) or 'Unknown '+k for k in ('asset','direction','play'))]
    if values.get('trade_date'): parts.append('Reported date: '+values['trade_date'])
    if values.get('context_notes'): parts.append(values['context_notes'])
    parts.append('Reported entry sequence (candle labels are not exact fill times):')
    for entry in values.get('entries',[]):
        text=f"Entry {entry['entry_index']}: "+(entry.get('entry_model') or 'entry model unknown')
        for key,label in (('candle_label','reported candle'),('objective','objective'),('status','status'),('pnl_text','reported P/L'),('notes','notes')):
            if entry.get(key):text+='; '+label+': '+entry[key]
        risk=entry.get('risk_r')
        text+='; entry risk: '+(f'{risk:g}R' if risk is not None else 'unknown')
        parts.append(text+'.')
    if values.get('thesis_invalidation'):
        parts.append('Reported thesis invalidation: '+values['thesis_invalidation'])
        parts.append('Boundary: '+(values.get('invalidation_boundary') or 'not clarified; do not infer a price or candle boundary'))
    parts.append('Overall reported outcome: '+(values.get('reported_outcome') or 'unknown'))
    return '\n'.join(parts)


def _question(draft):
    values=draft['values'];candidates=[]
    missing=[k for k in ('asset','direction') if not values.get(k)]
    if missing:candidates.append(('identity:'+','.join(missing),'What '+ ' and '.join(missing)+' should this journal use?'))
    if values.get('thesis_invalidation') and not values.get('invalidation_boundary'):
        text=values['thesis_invalidation']
        if re.search(r'\b(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[ -]*(?:o[’\']?clock|a\.?m\.?|p\.?m\.?)\b',text,re.I) and not re.search(r'\b(?:high|low|midpoint|open price|closing price)\b|\b\d{3,}(?:\.\d+)?\b',text,re.I):
            candidates.append(('boundary:'+text,'Which boundary do you mean in “'+text+'”: the candle high, or another specific level?'))
    for key,text in candidates:
        if key not in draft['asked']:
            return key,text
    return None,None


def _public(draft, context):
    key,question=_question(draft)
    draft['question']={'key':key,'text':question,'generation':context.generation} if question else None
    saved = draft.get('saved_revision') == draft['revision']
    return {'ok':True,'status':'saved' if saved else 'draft','saved':saved,'draft_id':draft['id'],'revision':draft['revision'],
            'trade_number':draft.get('trade_number'),'story':deepcopy(draft['values']),
            'draft_summary':render_story(draft['values']),'next_question':question,
            'instruction':('This revision was saved as a journal narrative; no execution records were created by staging.' if saved else 'Draft only. Preserve unknowns; use save_journal_story when journal saving is authorized. No execution records have been created by staging.')}


def stage_story(db,guild,user,args):
    patch=_patch(args.get('story_json'))
    capability,context=_context(args,guild,user)
    with capability.guard(guild,user):
        draft=getattr(context,'_journal_story',None)
        if args.get('new_draft') is True or draft is None:
            if args.get('draft_id') is not None:raise ValueError('Start a new draft without an old draft_id.')
            draft={'id':uuid4().hex,'owner':(guild,user,context.session_id),'values':{'entries':[]},'asked':[],
                   'revision':0,'trade_number':None,'new_trade':False,'save_authorized':False,'example_only':False}
        else:
            draft=deepcopy(_owned_draft(context,args,guild,user))
        source=getattr(context,'_client_text',None)
        if _save_optout(source):draft['save_authorized']=False
        elif _save_request(source):draft['save_authorized']=True
        if source and re.search(r'\b(?:hypothetical|example|imagine|suppose|pretend)\b',source,re.I):draft['example_only']=True
        if args.get('new_trade') is True and not draft['new_trade']:
            if source is not None and not _new_trade_request(source):
                raise ValueError('Only an explicitly separate new trade can bypass existing-record reconciliation.')
            if draft.get('saved_journal_id') or draft.get('trade_number'):
                raise ValueError('A selected/saved story cannot be converted to a new trade. Start a separate draft explicitly.')
            draft['new_trade']=True;draft['revision']+=1
        number=args.get('trade_number')
        if number is not None:
            if type(number) is not int or number<1:raise ValueError('Use a displayed Trade #.')
            if draft.get('saved_journal_id') and number!=draft['trade_number']:
                raise ValueError('A saved draft cannot be retargeted to another trade.')
            with db() as conn:
                selected=resolve_journal_selector(conn,guild,user,trade_number=number)
            if not selected.get('ok'):return selected
            selected_key=(selected.get('record_id'),selected.get('thesis_id'))
            if draft.get('trade_number')==number and draft.get('selected_key') not in (None,selected_key):
                raise ValueError('That displayed Trade # now identifies another record. Read history before selecting again.')
            if draft.get('selected_key') != selected_key:
                draft['revision']+=1
            draft['trade_number']=number
            draft['selected_key']=selected_key
        if patch.get('clear_fields') and (draft.get('saved_journal_id') or draft.get('selected_key')):
            raise ValueError('clear_fields only retracts unsaved draft facts. Use an explicit replacement or journal correction for already selected/saved records.')
        values=deepcopy(draft['values'])
        for key in FIELDS:
            if patch.get(key) is not None:values[key]=patch[key]
        entries={e['entry_index']:deepcopy(e) for e in values.get('entries',[])}
        for entry in patch.get('entries',[]):
            target=entries.setdefault(entry['entry_index'],{'entry_index':entry['entry_index']})
            target.update({k:v for k,v in entry.items() if v is not None})
        for key in patch.get('clear_fields',[]):
            if key in FIELDS:values.pop(key,None)
            else:
                match=re.fullmatch(r'entries\.(\d+)\.([a-z_]+)',key)
                if not match or match[2] not in ENTRY_FIELDS-{'entry_index'}:raise ValueError('Unsupported field to clear.')
                entries.get(int(match[1]),{}).pop(match[2],None)
        values['entries']=[entries[i] for i in sorted(entries)]
        if len(json.dumps(values))>16000:
            raise ValueError('This draft is too long; split separate trade stories instead of truncating details.')
        if values!=draft['values']:draft['revision']+=1
        draft['values']=values
        context._journal_story=draft
        return _public(draft,context)


def get_story(db,guild,user,args):
    capability,context=_context(args,guild,user)
    with capability.guard(guild,user):
        draft=_owned_draft(context,args,guild,user)
        if draft.get('saved_journal_id'):
            with db() as conn:
                row=_saved_story_row(conn,guild,user,draft['id'])
            if not row:
                draft.pop('saved_revision',None)
                return {'ok':False,'status':'saved_record_unavailable','error':'The earlier saved record is unavailable. Read history; do not recreate it automatically.'}
        return _public(draft,context)


def delivered_story_question(context,text,generation):
    draft=getattr(context,'_journal_story',None)
    question=(draft or {}).get('question')
    if (draft and draft['owner']==(*context.owner[:2],context.session_id) and question
            and question['generation']==generation and question['text'] in text and question['key'] not in draft['asked']):
        draft['asked'].append(question['key'])


def _saved_story_row(conn,guild,user,draft_id):
    rows=conn.execute('''SELECT j.id,j.thesis_id,j.description,j.result_r,d.metadata FROM journals j JOIN journal_details d
        ON d.journal_id=j.id AND d.guild_id=j.guild_id AND d.user_id=j.user_id
        WHERE j.guild_id=? AND j.user_id=?''',(guild,user)).fetchall()
    for row in rows:
        value=_story_value(row['metadata'])
        if value and value.get('draft_id')==draft_id:return row
    return None


def story_trade_candidates(conn,guild,user,values):
    rows=conn.execute('SELECT id,asset,direction,play,status FROM theses WHERE guild_id=? AND user_id=? ORDER BY id',(guild,user)).fetchall()
    normal=lambda text:str(text or '').upper().replace('NASDAQ','NAS100').replace('NAS100','NAS')
    return [{'trade_number':i,'asset':r['asset'],'direction':r['direction'],'play':r['play'],'status':r['status']}
            for i,r in enumerate(rows,1) if r['status'] in ('OPEN','JOURNALED','CLOSED')
            and normal(r['asset'])==normal(values.get('asset'))
            and (_direction(values.get('direction')) is None or _direction(r['direction'])==_direction(values.get('direction')))]


def reconciliation_required(matches):
    return {'ok':False,'status':'existing_trade_selection_required','candidate_trades':matches,
            'error':'Reconcile this story with the existing Trade # before saving. Select it with stage_journal_story if it is the same trade; ask once if ambiguous. Use new_trade only for an explicitly separate trade.'}


def _remember_saved(context,draft,row,number):
    with context._lock:
        current=getattr(context,'_journal_story',None)
        if current and current['id']==draft['id'] and current['owner']==draft['owner']:
            current['trade_number']=number
            current['saved_revision']=draft['revision']
            if row:
                current['saved_journal_id']=row['id']
                current['selected_key']=(row['id'],row['thesis_id'])


def save_story(db,guild,user,args):
    capability,context=_context(args,guild,user)
    with capability.guard(guild,user):
        current=_owned_draft(context,args,guild,user)
        source=getattr(context,'_client_text',None)
        if _save_optout(source):
            current['save_authorized']=False
            raise ValueError('No new save was performed. Earlier saved records are unchanged.')
        draft=deepcopy(current)
        if not (_save_request(source) or draft.get('save_authorized')):
            raise ValueError('Save this draft only when the member requests saving. No new save was performed.')
        if draft.get('example_only'):
            raise ValueError('This draft was framed as an example or hypothetical. Do not save it as an executed trade; use the existing study/reflection journal flow.')
    values=draft['values']
    if not values.get('entries') and not values.get('context_notes'):
        raise ValueError('Capture the reported story before saving an empty draft.')
    target=None;thesis_target=None;number=draft.get('trade_number')
    with db() as conn:
        previous=_saved_story_row(conn,guild,user,draft['id'])
        if previous:
            target=JournalTarget(guild,user,previous['id'])
            stored=_story_value(previous['metadata'])
            if stored == {'draft_id':draft['id'],'revision':draft['revision'],**values}:
                from gbop_voice_web.journal_numbers import journal_display
                display=next(r for r in journal_display(conn,guild,user) if r['id']==previous['id'])
                _remember_saved(context,draft,previous,display['trade_number'])
                return {'ok':True,'saved':True,'status':'saved','deduplicated':True,
                        'draft_id':draft['id'],'revision':draft['revision'],'trade_number':display['trade_number'],
                        'journal_number':display['journal_number'],'description':previous['description'],
                        'result_r':previous['result_r'],'execution_records_changed':False}
        elif draft.get('saved_journal_id'):
            raise ValueError('The saved draft was deleted or changed. Read history; do not recreate it automatically.')
        elif number is not None:
            selected=resolve_journal_selector(conn,guild,user,trade_number=number)
            if not selected.get('ok'):return selected
            if draft.get('selected_key') != (selected.get('record_id'),selected.get('thesis_id')):
                raise ValueError('The selected record changed or was deleted. Read history; no other journal was changed.')
            if selected.get('record_id'):target=JournalTarget(guild,user,selected['record_id'])
            else:thesis_target=JournalThesisTarget(guild,user,selected['thesis_id'])
        elif not draft['new_trade']:
            matches=story_trade_candidates(conn,guild,user,values)
            if matches:return reconciliation_required(matches)
    from gbop_voice_web.journal_coach import save_entry
    metadata={k:values[k] for k in ('asset','direction','play','trade_date','session','reported_outcome') if values.get(k)}
    if _direction(values.get('direction')):metadata['direction']=_direction(values['direction'])
    metadata.update(kind='trade')
    payload=StoryPayload(json.dumps({'draft_id':draft['id'],'revision':draft['revision'],**values},ensure_ascii=False),new_trade=draft['new_trade'])
    save_args={'description':render_story(values),'metadata_json':json.dumps(metadata,ensure_ascii=False),
               '_journal_binding':capability,'_journal_story_payload':payload}
    if target:save_args['_journal_target']=target
    elif thesis_target:save_args['_journal_thesis_target']=thesis_target
    elif number is not None:save_args['trade_number']=number
    result=save_entry(db,guild,user,save_args)
    if result.get('ok'):
        with db() as conn:
            row=_saved_story_row(conn,guild,user,draft['id'])
        _remember_saved(context,draft,row,result['trade_number'])
        result.update(status='saved',draft_id=draft['id'],revision=draft['revision'],execution_records_changed=False,
                      instruction='Journal narrative saved on the returned Trade #. Reported entry notes are not new execution rows. Unknown risk, prices and results remain unknown.')
    return result


STORY_TOOLS=[
    schema('stage_journal_story','Capture/correct the whole reported multi-entry story as an UNSAVED session draft. Never require risk/P&L to capture it. story_json keys: '+', '.join(sorted(FIELDS))+'; entries is an array with '+', '.join(sorted(ENTRY_FIELDS))+'. Patch only new facts; null preserves known values. clear_fields explicitly retracts UNSAVED draft fields (e.g. play, entries.1.entry_model), never database records. new_trade=true only when the member explicitly describes a separate trade; new_draft starts a separate story. Select existing displayed trade_number after reading current state.',
           {'draft_id':STR,'story_json':{'type':'string'},'trade_number':NUM,'new_draft':{'type':['boolean','null']},'new_trade':{'type':['boolean','null']}}),
    schema('get_journal_story','Read the current authenticated session draft and its known/unknown facts; no database write.',{'draft_id':STR}),
    schema('save_journal_story','Persist an authorized staged narrative to one canonical journal. Preserves reported multi-entry details without inventing execution/risk/P&L rows. Reconciles existing Trade # and same draft retries; does not delete records.',{'draft_id':{'type':'string'}}),
]
