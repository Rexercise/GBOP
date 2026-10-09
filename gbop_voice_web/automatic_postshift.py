"""Fresh, bounded later evidence appended to an immutable completed-shift recap."""
from copy import deepcopy
from collections import OrderedDict
from threading import RLock
from datetime import datetime
import hashlib
import json

from gbop_voice_web.candle_evidence import parse_time, stamp, crt_review, summarize
from gbop_voice_web.shift_availability import shift_bounds

VERSION = 'automatic-postshift-v1'
MAX_AUTOMATIC_SECONDS = 24 * 3600
MAX_CACHED_APPENDICES = 18
MAX_CACHED_BYTES = 1024 * 1024
_cache = OrderedDict()
_cache_lock = RLock()
CONTRACT = 'Append outcome/variant/time; keep cutoff separate. No trade inference; gaps stay unverified.'


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _source_digest(bars, native, step, cutoff):
    native = [dict(b,provenance={k:v for k,v in b.get('provenance',{}).items() if k!='captured_at'})
              for b in native if b['time']+3600<=cutoff]
    return _digest({'bars':[b for b in bars if b['time']+step<=cutoff], 'native_h1':native,'step':step})


def build_plan(review, asset, symbol, bars, native):
    """Persist only cutoff identities. Never persist a future outcome here."""
    from gbop_voice_web.post_shift_followthrough import continuation_candidates
    story=review.get('shift_story') or {}
    opening,cutoff=shift_bounds(review['date_ny'],review['shift'])
    if (story.get('end_ny')!=stamp(cutoff) or not story.get('progression_complete')
            or not story.get('coverage',{}).get('complete')):
        return None
    candidates=continuation_candidates(review,asset,symbol)
    selected=next((r for r in story.get('ranges',[]) if r.get('anchor_start_ny')==story.get('active_anchor_ny')),None)
    relevant=None
    if (selected and selected.get('role')=='selected_range'
            and selected.get('selection_status')=='range_under_review'
            and selected.get('anchor',{}).get('complete')
            and selected['anchor'].get('timeframe')=='H1'
            and not selected.get('invalidated_at_ny')
            and not selected.get('hourly_evidence_conflict')
            and not selected.get('context_qualification',{}).get('selected_tf_return_confirmed')
            and (selected.get('directional_outcome') or {}).get('status')!='opposing_liquidity_delivered'):
        relevant={'anchor_start_ny':selected['anchor_start_ny'],
                  'cutoff_status':'relevant_range_under_review_not_qualified',
                  'anchor':{k:deepcopy(selected['anchor'][k]) for k in
                            ('start_ny','end_ny','timeframe','open','high','low','close','midpoint')}}
    if not candidates and relevant is None:
        return None
    return {'version':VERSION,'asset':asset,'symbol':symbol,'date_ny':review['date_ny'],
            'shift':review['shift'],'cutoff_ny':stamp(cutoff),
            'source_digest':_source_digest(bars,native,review['source_resolution_seconds'],cutoff),
            'candidates':candidates,'relevant_range':relevant}


def _clock(value):
    return datetime.fromisoformat(value).strftime('%I:%M %p').lstrip('0')


def _interval(value):
    return {k:deepcopy(value[k]) for k in ('bar_open_ny','bar_close_ny','precision_seconds') if k in (value or {})}


def _variant(value):
    """Keep classifier and milestone stages without duplicate explanation trees."""
    out={k:deepcopy(value[k]) for k in ('status','evidence_through_ny') if k in value}
    out['labels']=[{k:v[k] for k in ('code','name') if k in v} for v in value.get('labels',[])]
    known=(value.get('explanation') or {}).get('known_at_ny')
    if known: out['known_at_ny']=known
    out['delivery_milestones']={}
    for key,m in value.get('delivery_milestones',{}).items():
        if not isinstance(m,dict): continue
        item={k:deepcopy(m[k]) for k in ('status','is_full_completion','known_at_ny') if k in m}
        manner=m.get('manner') or {}
        if manner:
            item['manner']={k:deepcopy(manner[k]) for k in ('status','primary_code','labels','snapshot_through_ny') if k in manner}
        out['delivery_milestones'][key]=item
    return out


def _qualified_fact(result):
    from gbop_voice_web.post_shift_followthrough import delivery_variant_clause
    r=result['review']; f=r['frozen_cutoff']; a=r['appendix']
    full=a['objectives']['full_objective']; event=full.get('evidence')
    label=_clock(f['parent']['start_ny'])+' H1 '+f['context']['phase'].replace('_',' ')
    summary=f"After shift: {label}, {f['context']['direction']}: "
    if a['status']=='full_objective_delivered_after_cutoff':
        summary+=f"full objective delivered in {_clock(event['bar_open_ny'])} {'M1' if f['source_resolution_seconds']==60 else 'M5'}; "
        summary+=delivery_variant_clause(a.get('variant_evidence',{}))+'.'
    elif a.get('invalidation'):
        summary+=f"invalidated at {_clock(a['invalidation']['known_at_ny'])}; valid full delivery is unverified."
    else:
        summary+=f"{'pending' if a['status']=='pending_at_followthrough_cutoff' else 'unverified'} through {_clock(a['through_ny'])}; variant unresolved."
    return {'anchor_start_ny':f['parent']['start_ny'],'phase':f['context']['phase'],
            'cutoff_status':'qualified_full_objective_pending','direction':f['context']['direction'],
            'status':a['status'],'full_objective':{'level':full['level'],'status':full['status'],
                                                 'source_interval':_interval(event),
                                                 'coverage_through_touch_complete':event.get('coverage_through_touch_complete') if event else None},
            'variant_evidence':_variant(a.get('variant_evidence',{})),
            'terminal':deepcopy(a.get('terminal')),
            'invalidation':({k:deepcopy(a['invalidation'][k]) for k in
                ('known_at_ny','bar_open_ny','first_invalidation_verified','source_path_through_close_complete')
                if k in a['invalidation']} if a.get('invalidation') else None),
            'coverage_complete':a['coverage']['complete'],'through_ny':a['through_ny'],'summary':summary}


def _relevant_fact(relevant, bars, native, step, cutoff, through):
    """Later development of a selected anchor, never qualification at cutoff."""
    from gbop_voice_web.shift_narrative import attach_directional_outcome, classify_structure, range_objectives
    from gbop_voice_web.post_shift_followthrough import delivery_variant_clause
    from gbop_voice_web.chronological_context import attach_qualification
    start=parse_time(relevant['anchor_start_ny'])
    row=crt_review(bars,start,through,'H1',step,native_h1=native)
    attach_directional_outcome(row,bars,through,step)
    attach_qualification(row,bars,through,step,native)
    outcome=row['directional_outcome']; full=outcome['opposing_liquidity']; hit=full.get('evidence')
    valid_delivery=outcome.get('status')=='opposing_liquidity_delivered'
    variant_end=min(through,(parse_time(hit['bar_open_ny'])//3600+1)*3600) if valid_delivery and hit else through
    variant=classify_structure({**row,'direction_observed':outcome.get('direction'),
        'objectives':range_objectives(row),'invalidated_at_ny':None if valid_delivery else row.get('invalidated_at_ny')},
        bars,variant_end,step)
    milestones=variant.get('delivery_milestones',{})
    manner=(milestones.get('opposing_liquidity') or {}).get('manner',{})
    # Canonical source-time milestones can prove one-candle V2 delivery before
    # the enclosing H1 closes. Do not promote that to earlier H1 qualification.
    direction=outcome.get('direction')
    valid_delivery=outcome.get('status')=='opposing_liquidity_delivered'
    status=('full_objective_delivered_after_cutoff' if valid_delivery else
            'invalidated_after_cutoff_without_verified_full_delivery' if row.get('invalidated_at_ny') else
            'unverified_later_evidence' if not row.get('observation_coverage',{}).get('complete')
                or direction not in ('bullish','bearish') and row.get('events') else
            'pending_later_development')
    label=_clock(relevant['anchor_start_ny'])+' H1 range'
    summary=f'After shift: {label} was under review, not a qualified CRT at cutoff. '
    if valid_delivery and hit:
        summary+=f"Its later {direction} full objective delivered in the {_clock(hit['bar_open_ny'])} {'M1' if step==60 else 'M5'} candle"
        summary+='; '+delivery_variant_clause(variant)+'.'
    elif row.get('invalidated_at_ny'):
        summary+=f"The range invalidated at {_clock(row['invalidated_at_ny'])}; no earlier valid full delivery is verified."
    else:
        summary+=f"Later development remains {'unverified' if status=='unverified_later_evidence' else 'pending'} through {_clock(stamp(through))}."
    qualification=row.get('context_qualification',{})
    return {'anchor_start_ny':relevant['anchor_start_ny'],'phase':'later_development',
            'cutoff_status':relevant['cutoff_status'],'direction':direction,'status':status,
            'full_objective':{'level':row['anchor']['high' if direction=='bullish' else 'low'] if direction else None,
                              'status':full.get('status'),'source_interval':_interval(hit)},
            'variant_evidence':_variant(variant),
            'qualification_after_cutoff':deepcopy(qualification),
            'invalidation_ny':row.get('invalidated_at_ny'),'through_ny':stamp(through),'summary':summary}


def _source_sets(db, feed, start, end):
    """Use the existing database-validated payload cache, when available."""
    from gbop_voice_web.market_data import _history_sets
    if db is None:
        return _history_sets(db,feed,start,end)
    with db() as conn:
        reader=getattr(conn,'market_history_row',None)
        if reader is None:
            return _history_sets(db,feed,start,end)
        by_step={300:{b['time']:b for b in feed.get('bars',[])},
                 60:{b['time']:b for b in feed.get('bars_m1',[])}}
        headers=conn.execute("""SELECT step,day_utc FROM gbop_market_history
            WHERE asset=? AND symbol=? AND step IN (60,300)
            AND day_utc>=? AND day_utc<=? ORDER BY day_utc""",
            (feed['asset'],feed['symbol'],start//86400*86400,end//86400*86400)).fetchall()
        for header in headers:
            row=reader((feed['asset'],feed['symbol'],header['step'],header['day_utc']))
            if row:
                by_step[header['step']].update({b['time']:b for b in json.loads(row['payload'])})
    return {step:sorted((b for b in rows.values() if start<=b['time']<end),key=lambda b:b['time'])
            for step,rows in by_step.items()}


def _remember(key,value):
    size=len(json.dumps(value,separators=(',',':')).encode())
    if size>MAX_CACHED_BYTES:
        return
    with _cache_lock:
        _cache[key]=(deepcopy(value),size)
        _cache.move_to_end(key)
        while len(_cache)>MAX_CACHED_APPENDICES or sum(v[1] for v in _cache.values())>MAX_CACHED_BYTES:
            _cache.popitem(last=False)


def evaluate_plan(db, feed, plan, now):
    """Revalidate original source once, then read fresh later candles once."""
    from gbop_voice_web.market_data import _history_sets, _select_shift_history, history_native_h1, session_review
    from gbop_voice_web.post_shift_followthrough import build_followthrough
    if not isinstance(plan,dict) or plan.get('version')!=VERSION:
        return None
    cutoff=parse_time(plan['cutoff_ny'])
    if now<=cutoff:
        return None
    base={'cutoff_ny':plan['cutoff_ny'],'automatic_window_hours':24,'ranges':[],
          'original_shift_unchanged':True,'response_contract':CONTRACT}
    if not feed.get('ok') or any(feed.get(k)!=plan.get(k) for k in ('asset','symbol')):
        return dict(base,status='later_source_unavailable',summary='Post-shift evidence is unavailable for the original broker source.')
    captured=datetime.fromisoformat(feed['captured_at_utc'])
    if captured.tzinfo is None:
        raise ValueError('Post-shift evidence requires timezone-aware capture.')
    limit=min(now,int(captured.timestamp()),cutoff+MAX_AUTOMATIC_SECONDS)
    opening,_=shift_bounds(plan['date_ny'],plan['shift']); start=opening-7200
    native=history_native_h1(db,feed,start,limit)
    sets=_source_sets(db,feed,start,max(cutoff,limit))
    sets={step:[b for b in rows if b['time']+step<=limit] for step,rows in sets.items()}
    from gbop_voice_web.market_watch import preparation_rules_hash
    native_facts=[dict(b,provenance={k:v for k,v in b.get('provenance',{}).items() if k!='captured_at'}) for b in native]
    cache_key=_digest({'plan':plan,'sets':sets,'native':native_facts,'rules':preparation_rules_hash()})
    with _cache_lock:
        cached=_cache.get(cache_key)
        if cached:
            _cache.move_to_end(cache_key)
            value=deepcopy(cached[0]); value['as_of_ny']=stamp(now)
            return value
    original,step=_select_shift_history(sets,plan['date_ny'],plan['shift'],cutoff,native)
    frozen=session_review(original,plan['date_ny'],plan['shift'],step,native_h1=native)
    actual=build_plan(frozen,feed['asset'],feed['symbol'],original,native)
    def stable(value):
        value=deepcopy(value)
        if value:
            for candidate in value.get('candidates',[]):
                candidate.pop('source_scope_id',None)
        return value
    if stable(actual)!=stable(plan):
        return dict(base,status='frozen_source_changed',summary='Original shift evidence changed; refresh the shift before appending later outcomes.')
    through=max([b['time']+step for b in sets[step] if b['time']>=cutoff]
                +[b['time']+3600 for b in native if b['time']>=cutoff],default=cutoff)
    base.update(through_ny=stamp(through),as_of_ny=stamp(now),source_resolution_seconds=step)
    if through<=cutoff:
        return {'status':'later_evidence_unavailable','through_ny':stamp(cutoff),
                'summary':'Post-shift: no later closed candles available.'}
    for candidate in actual['candidates']:
        result=build_followthrough(frozen,sets[step],asset=feed['asset'],symbol=feed['symbol'],
            anchor_start_ny=candidate['anchor_start_ny'],phase=candidate['phase'],
            expected_scope_id=candidate['source_scope_id'],through=through,as_of=now,native_h1=native)
        if result.get('ok') and result.get('review'):
            base['ranges'].append(_qualified_fact(result))
    if plan.get('relevant_range'):
        base['ranges'].append(_relevant_fact(plan['relevant_range'],sets[step],native,step,cutoff,through))
    base.update(status='bounded_later_evidence',summary=' '.join(r['summary'] for r in base['ranges']))
    _remember(cache_key,base)
    return base


def append_fresh_outcomes(db, feed, review, now):
    plan=review.get('post_shift_plan')
    if not plan:
        return
    try:
        if feed is None:
            from gbop_voice_web.market_data import read_feed
            feed=read_feed(db,plan['asset'],now)
        appendix=evaluate_plan(db,feed,plan,now)
    except Exception:
        appendix={'status':'later_evidence_unverified','original_shift_unchanged':True,
                  'summary':'Post-shift unverified.'}
    if appendix:
        review['post_shift_outcomes']=appendix
