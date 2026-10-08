"""Progressively saved member journals; finalization never invents executions."""
from copy import deepcopy
from datetime import date, datetime, timezone
from dataclasses import dataclass
import json
import math
import re
from uuid import uuid4

from gbop_voice_web.journal_context import binding, JournalTarget, JournalThesisTarget, JournalBinding, journal_transaction
from gbop_voice_web import journal_drafts
from gbop_voice_web.journal_numbers import resolve_journal_selector
from gbop_voice_web.trade_photos import schema, STR, NUM

STORY_NAMES = {'stage_journal_story', 'get_journal_story', 'save_journal_story'}
FIELDS = {'asset','direction','play','trade_date','session','context_notes','thesis_invalidation','invalidation_boundary','reported_outcome','objective','title','reported_entry_at','reported_exit_at','reported_entry_time_text','reported_exit_time_text','time_zone','result_r'}
ENTRY_FIELDS = {'entry_index','entry_model','candle_label','risk_r','risk_text','objective','status','pnl_text','notes','reported_entry_at','reported_exit_at','reported_entry_time_text','reported_exit_time_text'}
STORY_PROMPT = """
PROGRESSIVE PRIVATE JOURNALING
As soon as a member starts journaling, FIRST call stage_journal_story to save the
whole narration as an unfinished, editable journal BEFORE any follow-up question.
This uses the existing conversation, with no extra command, form or save step.
For voice, raw_story must faithfully transcribe the current journaling passage;
never put unrelated conversation, imagined wording, passwords or audio in it.
Typed member wording is captured by the server. Resume get_journal_story after a
pause/reconnect and patch the SAME draft_id; never restart known facts or entries.
If several unfinished journals exist, ask which one using their returned titles.
Read back supported asset/date/play/title/direction briefly and invite correction.
NAS means NAS100; keep the exact name 9ate8. A context downside target alone is
not proof of the member's short, especially for a hedge/countertrend entry. Infer
position direction only from actual-position evidence; otherwise preserve unknown.
Patch new/corrected facts only, keeping stable entry_index values. Corrections keep
raw narration and an audit trail. Never infer risk, P/L, results, fills, closure,
or historical dates. Preserve approximate/cross-midnight time wording verbatim;
a candle label is not a fill timestamp. Ask once for useful missing date/entry/exit
context after saving, without blocking narration or repeating known information.
Respect explicit do-not-record/preview-only requests; those remain session-only.
Never claim durable saving if persisted is false or a tool returns an error.
Unfinished drafts stay out of performance and execution records. save_journal_story
FINALIZES only on an explicit save/finalize/finish request. Starting to journal does
not finalize it. Finalization may keep unknowns null. Confirm the actual returned
Trade # and receipt. Read/reconcile existing matching trades before finalization;
never create another trade just because an answer was interrupted. open_trade,
add_entry and close_trade remain for separately grounded actual executions with
known required facts, never substitutes for narrative capture.
R IS OPTIONAL: save single-entry and multi-entry journals immediately with or
without risk or result R. Say "R unknown" when absent, never zero. Do not ask for
R as a prerequisite to saving. Later reported risk amounts go in entry risk_text
with units; profit/loss amounts go in pnl_text. Use result_r only for an explicitly
reported overall R result; never calculate it from an assumed risk budget.
To retract a reported R, use clear_fields: entries.1.risk_r for entry 1 risk,
result_r for the overall result. These corrections also work on a saved journal
and keep its Trade #. If "remove the R" is ambiguous, retain the narration and
ask at most one brief clarification about risk versus result; saving still works.
Use stage_journal_story for narration even if it describes only one execution.
"""


@dataclass(frozen=True)
class StoryPayload:
    """Server-built, not an accepted JSON tool argument."""
    encoded: str
    new_trade: bool = False
    storage_revision: int | None = None
    provenance: dict | None = None


def _save_optout(text):
    return bool(text and re.search(r"\b(?:don't|do not|never|not yet|hold off(?: on)?)\s+(?:save|saving|record|recording|log|logging|persist)|\b(?:cancel|stop)\s+(?:the\s+)?(?:save|saving|recording)|\b(?:just draft|draft only|preview only)\b",text.replace('’',"'"),re.I))


def _new_trade_request(text):
    for match in re.finditer(r'\b(?:new|separate|different|another)\s+(?:reported\s+)?trade\b',text,re.I):
        prefix=re.split(r'[.;!?]',text[:match.start()])[-1]
        if not re.search(r"\b(?:not|no|never|isn't|is not|don't|do not|without)\b(?:[ ,]+[\w’'-]+){0,6}[ ,]*$",prefix,re.I):
            return True
    return False


def _finalize_request(text):
    if text is None:
        return False  # Audio finalization must quote the member's explicit request.
    if _save_optout(text):return False
    return bool(re.search(r"(?:^|[.;!?]\s*)(?:(?:please|okay|ok|yes)[, ]+|(?:can|could|would) you (?:please )?|(?:let's|lets|let us) )?(?:save|finalize|finalise|finish|complete)\b",text,re.I))


def _journaling_request(text):
    return bool(text and not _save_optout(text) and re.search(
        r"(?:^|[.;!?]\s*)(?:(?:please|okay|ok|yes)[, ]+|(?:can|could|would) you (?:please )?|(?:let's|lets|let us) |I (?:want|need) to )?(?:journal|log|record|document|resume|continue)\b.*(?:journal|trade|story|entry|\bI\b|NAS|bought|sold)|^(?:journal|log)\b",text,re.I))


def _bind_loaded(context,draft,*,generation=None):
    with context._lock:
        if generation is not None and not context.current(generation):return draft
        draft['owner']=(*context.owner[:2],context.session_id)
        context._journal_story=draft
        context._journal_recording_paused=bool(getattr(context,'_journal_recording_paused',False) or draft.get('recording_paused',False))
    return draft


def _load_owned(conn,context,args,guild,user):
    current=getattr(context,'_journal_story',None)
    requested=args.get('draft_id') or (current or {}).get('id')
    if current and (current.get('owner')!=(guild,user,context.session_id) or current['id']!=requested):
        current=None
    if current and not current.get('persisted'):
        if requested!=current['id']:raise ValueError('That draft is unavailable in this account.')
        return deepcopy(current)
    rows=journal_drafts.read(conn,guild,user,requested)
    if not rows:
        if requested:raise ValueError('This draft is unavailable or was deleted. It will not be recreated automatically.')
        return None
    if len(rows)>1:
        raise ValueError('Several unfinished journals are available. Read get_journal_story and select the intended draft_id.')
    value=rows[0]
    # A stale conversation must read before applying a patch; do not overwrite
    # another device's correction merely because this process has old state.
    if current and current.get('storage_revision')!=value['storage_revision'] and not args.get('_refresh'):
        raise ValueError('This unfinished journal changed in another conversation. Read the same draft before correcting it.')
    value['owner']=(guild,user,context.session_id)
    return value


def begin_story_turn(context,text,*,generation=None):
    # Never hold a conversation lock while waiting for a DB/member lock.
    with context._lock:
        ticket=context.generation if generation is None else generation
        if not context.current(ticket):return
        draft=deepcopy(getattr(context,'_journal_story',None))
        optout=_save_optout(text)
        if optout:
            context._journal_recording_paused=True
            if draft and draft.get('owner')==(*context.owner[:2],context.session_id):
                context._journal_story['save_authorized']=False
                context._journal_story['recording_paused']=True
        elif _journaling_request(text):context._journal_recording_paused=False
    if optout and draft and draft.get('persisted') and getattr(context,'auth_provider',None):
        db,guild,user=context.auth_provider
        try:
            with journal_transaction(db,{'_journal_binding':JournalBinding(context,ticket)},guild,user,serialize=True) as conn:
                latest=journal_drafts.read(conn,guild,user,draft['id'])
                if latest:
                    value=latest[0];value['save_authorized']=False;value['recording_paused']=True
                    stored=journal_drafts.write(conn,guild,user,value,expected_revision=value['storage_revision'],finalized=value.get('draft_status')=='finalized')
                else:stored=None
            if stored:_bind_loaded(context,stored,generation=ticket)
        except Exception:
            with context._lock:
                if context.current(ticket):context._journal_capture_error='Recording is paused; the earlier saved journal remains unchanged.'
    # Audio has no local transcript; its first stage tool records the passage.
    if not _journaling_request(text) or not getattr(context,'auth_provider',None):return
    if len(text.split())<8 and not re.search(r'\b(?:resume|continue|NAS(?:100)?|NASDAQ|SPX|US30|XAUUSD|BTCUSD|EURUSD|9ate8|bought|sold|shorted|entered)\b',text,re.I):return
    if _new_trade_request(text):return
    with context._lock:
        if not context.current(ticket):return
        if draft and draft.get('draft_status')=='finalized':context._journal_story=None
    db,guild,user=context.auth_provider
    try:
        from gbop_voice_web.journal_coach import init_coach
        init_coach(db)
        result=stage_story(db,guild,user,{'story_json':'{}',
            '_journal_binding':JournalBinding(context,ticket)})
        with context._lock:
            if context.current(ticket):context._journal_capture_error=None if result.get('ok') else result.get('error')
    except Exception:
        with context._lock:
            if context.current(ticket):context._journal_capture_error='The narration could not be saved. Keep this passage available and retry staging before claiming it is saved.'


def story_prompt_context(context):
    if getattr(context,'_journal_capture_error',None):
        return '\nJOURNAL STORAGE NOTICE: '+context._journal_capture_error
    draft=getattr(context,'_journal_story',None)
    if not draft or draft.get('owner')!=(*context.owner[:2],context.session_id):return ''
    if draft.get('draft_status')=='finalized':return ''
    return '\nCURRENT UNFINISHED PRIVATE JOURNAL: '+json.dumps({
        'draft_id':draft['id'],'persisted':draft.get('persisted',False),
        'story':draft['values'],'provenance':draft.get('provenance',{}),**_raw_page(draft),
        'instruction':'Keep this draft, read all raw narration pages via get_journal_story before questions, capture new narration first, use known facts and invite corrections. Finalize only on explicit request.'},ensure_ascii=False)


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


def _text(value, label):
    if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>2500):
        raise ValueError(label+' must be nonempty reported text or null.')
    return value


def _patch(value):
    value = json.loads(value) if isinstance(value,str) else value
    if not isinstance(value,dict) or set(value)-FIELDS-{'entries','clear_fields'} or len(json.dumps(value))>16000:
        raise ValueError('Use a bounded journal story object with documented fields.')
    for key in FIELDS-{'result_r'}:
        if key in value:
            _text(value[key],key)
    result=value.get('result_r')
    if result is not None and (isinstance(result,bool) or not isinstance(result,(int,float)) or not math.isfinite(result)):
        raise ValueError('Result R must be a reported finite number or null.')
    if value.get('trade_date'):
        date.fromisoformat(value['trade_date'])
    from gbop_voice_web.journal_context import validate_reported
    validate_reported(value)
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
        validate_reported(entry)
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
    if values.get('title'): parts.insert(0,values['title'])
    if values.get('trade_date'): parts.append('Reported date: '+values['trade_date'])
    for key,label in (('reported_entry_at','Reported entry'),('reported_exit_at','Reported exit'),('reported_entry_time_text','Reported entry wording'),('reported_exit_time_text','Reported exit wording')):
        if values.get(key):parts.append(label+': '+values[key])
    if values.get('objective'):parts.append('Reported/inferred context objective: '+values['objective'])
    if values.get('context_notes'): parts.append(values['context_notes'])
    parts.append('Reported entry sequence (candle labels are not exact fill times):')
    for entry in values.get('entries',[]):
        text=f"Entry {entry['entry_index']}: "+(entry.get('entry_model') or 'entry model unknown')
        for key,label in (('candle_label','reported candle'),('objective','objective'),('status','status'),('risk_text','reported risk amount'),('pnl_text','reported P/L'),('notes','notes'),('reported_entry_at','reported entry'),('reported_exit_at','reported exit'),('reported_entry_time_text','reported entry wording'),('reported_exit_time_text','reported exit wording')):
            if entry.get(key):text+='; '+label+': '+entry[key]
        risk=entry.get('risk_r')
        text+='; entry risk: '+(f'{risk:g}R' if risk is not None else 'unknown (R unknown)')
        parts.append(text+'.')
    if values.get('thesis_invalidation'):
        parts.append('Reported thesis invalidation: '+values['thesis_invalidation'])
        parts.append('Boundary: '+(values.get('invalidation_boundary') or 'not clarified; do not infer a price or candle boundary'))
    parts.append('Overall reported outcome: '+(values.get('reported_outcome') or 'unknown'))
    result=values.get('result_r')
    parts.append('Overall result: '+(f'{result:g}R' if result is not None else 'R unknown'))
    return '\n'.join(parts)


def _question(draft):
    values=draft['values'];candidates=[]
    if draft.get('validation_issue'):
        candidates.append(('validation:'+draft['validation_issue'],'The narration is saved, but the reported times conflict. What is the corrected entry or exit date and time?'))
    missing=[k for k in ('asset','direction') if not values.get(k)]
    if missing:candidates.append(('identity:'+','.join(missing),'What '+ ' and '.join(missing)+' should this journal use?'))
    if values.get('thesis_invalidation') and not values.get('invalidation_boundary'):
        text=values['thesis_invalidation']
        if re.search(r'\b(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[ -]*(?:o[’\']?clock|a\.?m\.?|p\.?m\.?)\b',text,re.I) and not re.search(r'\b(?:high|low|midpoint|open price|closing price)\b|\b\d{3,}(?:\.\d+)?\b',text,re.I):
            candidates.append(('boundary:'+text,'Which boundary do you mean in “'+text+'”: the candle high, or another specific level?'))
    if not values.get('trade_date') and not values.get('reported_entry_at') and not any(e.get('reported_entry_at') for e in values.get('entries',[])):
        candidates.append(('chronology:date','What day was this trade? Approximate is fine; I’ll keep the date unknown if you’re not sure.'))
    elif not any(values.get(k) for k in ('reported_entry_at','reported_entry_time_text')) and not any(any(e.get(k) for k in ('reported_entry_at','reported_entry_time_text')) for e in values.get('entries',[])):
        candidates.append(('chronology:entry','About when did you enter? You can give an approximate time or leave it unknown.'))
    for key,text in candidates:
        if key not in draft['asked']:
            return key,text
    return None,None


def _raw_page(draft,offset=0):
    if type(offset) is not int or offset<0:raise ValueError('raw_offset must be a nonnegative whole number.')
    raw='\n'.join('['+p['recorded_at']+' '+p['source']+'] '+p['text'] for p in draft.get('raw_story',[]))
    if offset>len(raw):raise ValueError('Read raw narration using the returned next_raw_offset.')
    end=min(offset+8000,len(raw))
    return {'raw_story_text':raw[offset:end],'raw_offset':offset,'raw_has_more':end<len(raw),
            'next_raw_offset':end if end<len(raw) else None,'raw_total_chars':len(raw)}


def _public(draft, context,raw_offset=0):
    key,question=_question(draft)
    draft['question']={'key':key,'text':question,'generation':context.generation} if question else None
    saved = draft.get('saved_revision') == draft['revision']
    return {'ok':True,'status':'saved' if saved else 'draft','saved':saved,'persisted':draft.get('persisted',False),'draft_status':draft.get('draft_status','session_only'),'created_at':draft.get('created_at'),'updated_at':draft.get('updated_at'),'provenance':deepcopy(draft.get('provenance',{})),'draft_id':draft['id'],'revision':draft['revision'],
            'trade_number':draft.get('trade_number'),'story':deepcopy(draft['values']),
            'draft_summary':render_story(draft['values']),'next_question':question,**_raw_page(draft,raw_offset),
            'validation_issue':draft.get('validation_issue'),'ready_to_finalize':not bool(draft.get('validation_issue')),
            'raw_instruction':'Read every raw narration page before asking for facts that may already be reported. Continue get_journal_story with the same draft_id and next_raw_offset while raw_has_more.',
            'instruction':('This revision was saved as a journal narrative; no execution records were created by staging.' if saved else ('Unfinished journal saved privately and excluded from performance. Keep this draft after pauses; finalize only on explicit request.' if draft.get('persisted') else 'Session-only preview; not saved durably because recording was declined.'))}


def stage_story(db,guild,user,args):
    try:
        patch=_patch(args.get('story_json'))
    except (ValueError,TypeError,KeyError) as exc:
        # A bad extraction must not lose the narration it came from. Save the
        # raw passage with no structured guesses, then report the parse issue.
        capability,context=_context(args,guild,user)
        if not (getattr(context,'_client_text',None) or args.get('raw_story')):raise
        result=stage_story(db,guild,user,{**args,'story_json':'{}'})
        result.update(ok=False,status='narration_saved_needs_correction' if result.get('persisted') else 'preview_needs_correction',
                      error=str(exc),instruction='The narration was retained as reported. Correct the structured extraction; do not invent missing facts.')
        return result
    capability,context=_context(args,guild,user)
    from gbop_voice_web.journal_coach import init_coach
    init_coach(db)
    with journal_transaction(db,args,guild,user,serialize=True) as conn:
        draft=None if args.get('new_draft') is True else _load_owned(conn,context,args,guild,user)
        if args.get('new_draft') is True or draft is None:
            if args.get('draft_id') is not None:raise ValueError('Start a new draft without an old draft_id.')
            draft={'id':uuid4().hex,'owner':(guild,user,context.session_id),'values':{'entries':[]},'asked':[],
                   'revision':0,'trade_number':None,'new_trade':False,'save_authorized':False,'example_only':False,
                   'raw_story':[],'provenance':{},'corrections':[],'created_at':journal_drafts.stamp()}
        expected=draft.get('storage_revision')
        source=getattr(context,'_client_text',None)
        intent_source=source if source is not None else args.get('raw_story')
        if source is None and intent_source is not None:
            context._journal_audio_intent={'generation':context.generation,'text':intent_source}
        if _save_optout(intent_source):
            draft['save_authorized']=False
            draft['recording_paused']=True
            context._journal_recording_paused=True
        elif _journaling_request(intent_source) or (intent_source is not None and _finalize_request(intent_source)):
            context._journal_recording_paused=False
            draft['recording_paused']=False
            if _finalize_request(intent_source):draft['save_authorized']=True
        draft['recording_paused']=bool(draft.get('recording_paused') or getattr(context,'_journal_recording_paused',False))
        if intent_source and re.search(r'\b(?:hypothetical|example|imagine|suppose|pretend)\b',intent_source,re.I):draft['example_only']=True
        if args.get('new_trade') is True and not draft['new_trade']:
            if intent_source is not None and not _new_trade_request(intent_source):
                raise ValueError('Only an explicitly separate new trade can bypass existing-record reconciliation.')
            if draft.get('saved_journal_id') or draft.get('trade_number'):
                raise ValueError('A selected/saved story cannot be converted to a new trade. Start a separate draft explicitly.')
            draft['new_trade']=True;draft['revision']+=1
        number=args.get('trade_number')
        if number is not None:
            if type(number) is not int or number<1:raise ValueError('Use a displayed Trade #.')
            if draft.get('saved_journal_id') and number!=draft['trade_number']:
                raise ValueError('A saved draft cannot be retargeted to another trade.')
            selected=resolve_journal_selector(conn,guild,user,trade_number=number)
            if not selected.get('ok'):return selected
            selected_key=(selected.get('record_id'),selected.get('thesis_id'))
            if draft.get('trade_number')==number and draft.get('selected_key') not in (None,selected_key):
                raise ValueError('That displayed Trade # now identifies another record. Read history before selecting again.')
            if draft.get('selected_key') != selected_key:
                draft['revision']+=1
            draft['trade_number']=number
            draft['selected_key']=selected_key
        clears=patch.get('clear_fields',[])
        r_clears_only=all(k=='result_r' or re.fullmatch(r'entries\.(?:[1-9]|1[0-2])\.risk_r',k) for k in clears)
        if clears and not r_clears_only and (draft.get('saved_journal_id') or draft.get('selected_key')):
            raise ValueError('clear_fields only retracts unsaved draft facts. Use an explicit replacement or journal correction for already selected/saved records.')
        before=deepcopy(draft['values'])
        suppressed=set(draft.get('suppressed_fields',[]))
        if patch.get('play') is not None and patch['play']!=before.get('play'):
            suppressed.discard('objective')
        suppressed.difference_update(k for k in FIELDS if patch.get(k) is not None)
        suppressed.update(k for k in patch.get('clear_fields',[]) if k in FIELDS)
        draft['suppressed_fields']=sorted(suppressed)
        values=deepcopy(before)
        raw_before=len(draft['raw_story'])
        raw=source if source is not None else args.get('raw_story')
        if raw is not None and (not isinstance(raw,str) or len(raw)>16000):raise ValueError('Use a bounded faithful narration passage.')
        if source is None and not raw and not draft.get('raw_story'):
            raise ValueError('Capture the member’s actual narrated passage in raw_story before asking follow-up questions.')
        if raw and not _save_optout(raw):
            source_key=f'{context.session_id}:{context.generation}'
            if not any(v['source_key']==source_key and v['text']==raw for v in draft['raw_story']):
                draft['raw_story'].append({'source_key':source_key,'text':raw,'source':'member_text' if source is not None else 'voice_transcription','recorded_at':journal_drafts.stamp()})
            from gbop_voice_web.journal_inference import infer_story_context
            inferred=infer_story_context(raw,values=values,now=datetime.fromtimestamp(context._turn_now,tz=timezone.utc) if context._turn_now else None,timezone_name=values.get('time_zone'),context={'now_is_server_clock':True,'provenance':draft['provenance'],'suppressed_fields':draft['suppressed_fields']})
            values=inferred['values']
            draft['provenance']=deepcopy(inferred['provenance'])
        if (patch.get('play') is not None or 'play' in patch.get('clear_fields',[])) and not patch.get('objective') and (draft['provenance'].get('objective') or {}).get('source')=='contextual_target_default':
            values.pop('objective',None);draft['provenance'].pop('objective',None)
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
        for key in patch:
            if key!='clear_fields':draft['provenance'][key]={'kind':'explicit','source':'model_extracted_narration','recorded_at':journal_drafts.stamp()}
        for key in patch.get('clear_fields',[]):draft['provenance'].pop(key,None)
        if raw and not _save_optout(raw):
            normalized=infer_story_context(raw,values=values,now=datetime.fromtimestamp(context._turn_now,tz=timezone.utc) if context._turn_now else None,timezone_name=values.get('time_zone'),context={'now_is_server_clock':True,'provenance':draft['provenance'],'suppressed_fields':draft['suppressed_fields']})
            values=normalized['values']
            draft['provenance']=normalized['provenance']
        from gbop_voice_web.journal_context import validate_reported
        try:
            validate_reported(values)
            for entry in values.get('entries',[]):validate_reported(entry)
            draft.pop('validation_issue',None)
        except ValueError as exc:
            draft['validation_issue']=str(exc)
        if len(json.dumps(values))>16000:
            raise ValueError('This draft is too long; split separate trade stories instead of truncating details.')
        if values!=before:
            draft['revision']+=1
            changes={k:{'before':before.get(k),'after':values.get(k)} for k in set(before)|set(values) if before.get(k)!=values.get(k)}
            if any(change['before'] is not None for change in changes.values()):draft['corrections'].append({'recorded_at':journal_drafts.stamp(),'fields':changes})
        if args.get('new_draft') is True and intent_source is not None and not draft.get('example_only') and not (_new_trade_request(intent_source) or re.search(r'\bnew (?:journal|draft|story)\b',intent_source,re.I)):
            raise ValueError('Start another journal only when the member asks for a new or separate story.')
        if len(draft['raw_story'])!=raw_before and values==before:draft['revision']+=1
        draft['values']=values
        if not _save_optout(intent_source) and not draft.get('example_only') and not draft.get('recording_paused',False):
            linked=draft.get('selected_key') or (None,None)
            durable=journal_drafts.write(conn,guild,user,draft,expected_revision=expected,journal_id=linked[0],thesis_id=linked[1])
            draft=durable
        else:
            if expected is not None and _save_optout(intent_source):
                previous=journal_drafts.read(conn,guild,user,draft['id'])
                if previous:
                    paused=previous[0];paused['recording_paused']=True;paused['save_authorized']=False
                    stored_pause=journal_drafts.write(conn,guild,user,paused,expected_revision=expected,finalized=paused.get('draft_status')=='finalized')
                    draft['storage_revision']=stored_pause['storage_revision']
            draft['persisted']=False
            draft['draft_status']='session_only'
    _bind_loaded(context,draft,generation=capability.generation)
    with context._lock:
        if context.current(capability.generation):context._journal_capture_error=None
        result=_public(draft,context)
    if draft.get('persisted'):
        from gbop_voice_web.voice_runtime import journal_write_committed
        journal_write_committed(guild,user,{'draft_id':draft['id'],'revision':draft['revision'],
            'draft_status':'unfinished','persisted':True})
    return result


def get_story(db,guild,user,args):
    capability,context=_context(args,guild,user)
    with journal_transaction(db,args,guild,user) as conn:
        if not args.get('draft_id') and not getattr(context,'_journal_story',None):
            rows=journal_drafts.read(conn,guild,user)
            if len(rows)>1:
                return {'ok':True,'status':'draft_selection_required','drafts':[
                    {'draft_id':d['id'],'title':d['values'].get('title') or 'Unfinished journal',
                     'asset':d['values'].get('asset'),'updated_at':d['updated_at']} for d in rows],
                    'instruction':'Which unfinished journal should we continue? Keep their existing identities.'}
        draft=_load_owned(conn,context,{**args,'_refresh':True},guild,user)
        if draft is None:return {'ok':True,'status':'no_unfinished_journal','drafts':[]}
        _bind_loaded(context,draft)
        if draft.get('saved_journal_id') and not _saved_story_row(conn,guild,user,draft['id']):
            draft.pop('saved_revision',None)
            return {'ok':False,'status':'saved_record_unavailable','error':'The earlier saved record is unavailable. Read history; do not recreate it automatically.'}
        return _public(draft,context,args.get('raw_offset') or 0)


def delivered_story_question(context,text,generation):
    with context._lock:
        draft=getattr(context,'_journal_story',None)
        question=(draft or {}).get('question')
        if not (context.current(generation) and draft and draft['owner']==(*context.owner[:2],context.session_id) and question
                and question['generation']==generation and question['text'] in text and question['key'] not in draft['asked']):return
        draft['asked'].append(question['key'])
        draft_id=draft['id'];key=question['key'];persisted=draft.get('persisted')
    if persisted and getattr(context,'auth_provider',None):
        db,guild,user=context.auth_provider
        try:
            with journal_transaction(db,{'_journal_binding':JournalBinding(context,generation)},guild,user,serialize=True) as conn:
                rows=journal_drafts.read(conn,guild,user,draft_id)
                if not rows:return
                latest=rows[0]
                if key not in latest['asked']:latest['asked'].append(key)
                stored=journal_drafts.write(conn,guild,user,latest,expected_revision=latest['storage_revision'],finalized=latest.get('draft_status')=='finalized')
            with context._lock:
                if context.current(generation) and (getattr(context,'_journal_story',None) or {}).get('id')==draft_id:
                    _bind_loaded(context,stored,generation=generation)
        except Exception:
            pass  # Keep the local receipt; never invalidate an answer already delivered.


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
    with journal_transaction(db,args,guild,user,serialize=True) as conn:
        current=_load_owned(conn,context,args,guild,user)
        if current is None:raise ValueError('Read the unfinished journal before finalizing it.')
        source=getattr(context,'_client_text',None)
        audio_intent=getattr(context,'_journal_audio_intent',{})
        if source is None and audio_intent.get('generation')==context.generation:
            source=audio_intent.get('text')
        if source is None:source=args.get('confirmation_text')
        if source is not None:_text(source,'confirmation_text')
        if (current.get('recording_paused') or getattr(context,'_journal_recording_paused',False)) and not _finalize_request(source):
            raise ValueError('Recording is paused. Resume or finalize only with the member’s explicit current request.')
        if _save_optout(source):
            current['save_authorized']=False
            raise ValueError('No new save was performed. Earlier saved records are unchanged.')
        draft=deepcopy(current)
        if not (_finalize_request(source) or draft.get('save_authorized')):
            raise ValueError('The narration is already saved as unfinished. Finalize only when the member explicitly asks to save, finish or finalize it.')
        if draft.get('validation_issue'):
            raise ValueError('The narration is safely unfinished. Clarify the conflicting reported chronology before finalizing it.')
        _patch(draft['values'])
        if draft.get('example_only'):
            raise ValueError('This draft was framed as an example or hypothetical. Do not save it as an executed trade; use the existing study/reflection journal flow.')
        if not draft.get('persisted'):
            draft=journal_drafts.write(conn,guild,user,draft,expected_revision=draft.get('storage_revision'))
            _bind_loaded(context,draft)
    values=draft['values']
    if not values.get('entries') and not values.get('context_notes') and not draft.get('raw_story'):
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
                stored_draft=journal_drafts.read(conn,guild,user,draft['id'])
                if stored_draft:_bind_loaded(context,stored_draft[0])
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
    metadata={k:values[k] for k in ('asset','direction','play','trade_date','session','reported_outcome','title','reported_entry_at','reported_exit_at','reported_entry_time_text','reported_exit_time_text','time_zone') if values.get(k)}
    if _direction(values.get('direction')):metadata['direction']=_direction(values['direction'])
    metadata.update(kind='trade')
    payload=StoryPayload(json.dumps({'draft_id':draft['id'],'revision':draft['revision'],**values},ensure_ascii=False),new_trade=draft['new_trade'],storage_revision=draft.get('storage_revision'),provenance=deepcopy(draft.get('provenance',{})))
    description=render_story(values)
    if draft.get('raw_story'):
        description+='\nOriginal member narration:\n'+'\n'.join(passage['text'] for passage in draft['raw_story'])
    save_args={'description':description,'metadata_json':json.dumps(metadata,ensure_ascii=False),
               '_journal_binding':capability,'_journal_story_payload':payload}
    if values.get('result_r') is not None:
        save_args['result_r']=values['result_r']
    elif 'result_r' in draft.get('suppressed_fields',[]):
        save_args['clear_result']=True
    if target:save_args['_journal_target']=target
    elif thesis_target:save_args['_journal_thesis_target']=thesis_target
    elif number is not None:save_args['trade_number']=number
    result=save_entry(db,guild,user,save_args)
    if result.get('ok'):
        with db() as conn:
            row=_saved_story_row(conn,guild,user,draft['id'])
        with db() as conn:
            stored=journal_drafts.read(conn,guild,user,draft['id'])
        if stored:_bind_loaded(context,stored[0],generation=capability.generation)
        _remember_saved(context,draft,row,result['trade_number'])
        result.update(status='saved',draft_id=draft['id'],revision=draft['revision'],execution_records_changed=False,
                      instruction='Journal narrative saved on the returned Trade #. Reported entry notes are not new execution rows. Unknown risk, prices and results remain unknown.')
    return result


STORY_TOOLS=[
    schema('stage_journal_story','FIRST capture/correct single-entry or multi-entry narration as a durable editable journal before follow-up questions. R is optional: unknown stays null, never zero. story_json keys: '+', '.join(sorted(FIELDS))+'; entries is an array with '+', '.join(sorted(ENTRY_FIELDS))+'. result_r is numeric overall reported R; entry risk_text and pnl_text preserve cash amounts with units. Patch only new facts; null preserves known values. clear_fields retracts unfinished fields; result_r and entries.1.risk_r may also be cleared on saved/selected narratives without deleting records. new_trade=true only for an explicitly separate trade; new_draft starts a separate story. Select existing displayed trade_number after reading current state.',
           {'draft_id':STR,'raw_story':STR,'story_json':{'type':'string'},'trade_number':NUM,'new_draft':{'type':['boolean','null']},'new_trade':{'type':['boolean','null']}}),
    schema('get_journal_story','Resume the same member-owned unfinished journal after pauses/reconnects; read known facts and choose from titles when several remain. No write.',{'draft_id':STR,'raw_offset':NUM}),
    schema('save_journal_story','Explicitly FINALIZE a saved unfinished narrative only when the member asks to save/finalize/finish; for voice quote the actual current request in confirmation_text. Null for typed requests. Preserves reported multi-entry details without inventing execution/risk/P&L rows. Reconciles existing Trade # and same draft retries; does not delete records.',{'draft_id':{'type':'string'},'confirmation_text':STR}),
]
