"""Single leased watcher in the existing Discord process; no AI polling calls."""
import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from gbop_voice_web.market_watch import NY, VERSION, runtime_lease
from gbop_voice_web.member_access import member_access_error

TASK = None
log = logging.getLogger(__name__)


def ts(value):
    return int(datetime.fromisoformat(value).timestamp())


def clock(value):
    return datetime.fromtimestamp(value, NY).strftime('%Y-%m-%d %H:%M NY')


def extract_events(result):
    """Stable observations, not trading recommendations. Never infer executions."""
    if not result.get('ok'):
        return []
    asset = result['asset']; review = result.get('review', {})
    story = review.get('shift_story')
    ranges = [r for r in story.get('ranges', []) if r.get('role') == 'selected_range'] if story else [review]
    events = {}
    def add(kind, at, identity, description):
        if not at:
            return
        t = ts(at) if isinstance(at,str) else int(at)
        key = hashlib.sha256(json.dumps([asset,kind,t,identity],sort_keys=True).encode()).hexdigest()
        events[key] = {'key':key,'kind':kind,'at':t,'text':f'{asset} | {description} | {clock(t)}. Closed-candle observation, not an order.'}
    for row in ranges:
        anchor = row.get('anchor_start_ny') or row.get('anchor',{}).get('start_ny')
        for candle in row.get('candle_lifecycle',{}).get('purge_candles',[]):
            identity = [anchor,candle.get('purged_side'),candle.get('purged_level'),candle['bar_open_ny']]
            label = 'Model 1 body soup' if candle['purge_type']=='body_soup' else 'Turtle Wick Soup'
            add(candle['purge_type'],candle['bar_close_ny'],identity,
                f"{label} on {candle['timeframe']}; candle opened {clock(ts(candle['bar_open_ny']))}, purged {candle['purged_side']} side at {candle['purged_level']}; range {anchor}")
            csd = candle.get('csd',{})
            if csd.get('status')=='confirmed':
                add('csd',csd['evidence']['confirmed_at_ny'],identity,
                    f"CSD closed through Model 1 body-open level {csd['reference_level']}; Model 1 {candle['bar_open_ny']}")
            soup = candle.get('super_soup',{})
            cls = candle.get('super_soup_structure',{})
            # Same-candle soup/CSD is preserved for discussion, not a pre-CSD alert.
            if soup.get('status')=='observed_before_csd' and soup.get('evidence'):
                ret = soup['evidence']['return_candle']
                add('super_soup',ret['bar_close_ny'],identity,
                    f"Super Soup of Model 1 {candle['bar_open_ny']}; {cls.get('structural_quality','observed')} formation; variant {','.join(v['code'] for v in cls.get('variants',[])) or 'distribution unresolved'}; success assessed separately")
            for name, objective in candle.get('objectives_after_formation',{}).items():
                if objective.get('status')=='observed_after_event' and objective.get('first_touch'):
                    hit=objective['first_touch']
                    add('objective',hit['bar_close_ny'],[anchor,candle.get('purged_side'),name,objective['level']],
                        f"{name.replace('_',' ')} touched at {objective['level']} for range {anchor}; not a recorded trade result")
        if row.get('invalidated_at_ny'):
            add('invalidation',row['invalidated_at_ny'],[anchor],f'Range {anchor} invalidated by its timeframe close')
    paired = [review.get('paired_smt',{})]
    paired += [x.get('paired_review',{}) for x in review.get('paired_context',{}).get('ranges',[])]
    for comparison in paired:
        for event in comparison.get('events',[]):
            if not event.get('anchors_valid_at_event'):
                continue
            add('smt',event['bar_close_ny'],[event.get('side'),event.get('swept_asset'),event.get('nonconfirming_asset')],
                f"{event['direction']} SMT: {event['swept_asset']} visibly purged; {event.get('boneless_asset',event.get('nonconfirming_asset'))} was boneless at that moment")
    return sorted(events.values(),key=lambda e:(e['at'],e['key']))


def matches(requested, kind):
    return requested=='all' or requested==kind or requested=='purge' and kind in ('body_soup','wick_soup') or requested=='model1' and kind=='body_soup'


def poll_watches(db, guild_id, owner_id, now=None, evaluator=None, feed_reader=None):
    from gbop_voice_web.market_data import market_tool, read_feed
    now=int(time.time() if now is None else now)
    evaluator=evaluator or market_tool; feed_reader=feed_reader or read_feed
    with db() as conn:
        conn.execute("UPDATE gbop_market_watches SET state='expired',last_status='shift_finished' WHERE state='active' AND expires_at<?",(now-120,))
        conn.execute("UPDATE gbop_market_alerts SET state='delivery_uncertain',updated_at=? WHERE state='sending' AND updated_at<?",(now,now-180))
        watches=conn.execute("SELECT * FROM gbop_market_watches WHERE guild_id=? AND state='active' AND starts_at<=? AND expires_at>=? ORDER BY created_at LIMIT 24",(guild_id,now,now-120)).fetchall()
    cache={}; feed_cache={}; queued=0
    for row in watches:
        user_id=row['user_id']; spec=json.loads(row['spec'])
        denial=member_access_error(db,guild_id,user_id,owner_id)
        if denial:
            with db() as conn:
                conn.execute("UPDATE gbop_market_watches SET state='access_blocked',last_status='access_denied',last_checked_at=? WHERE id=?",(now,row['id']))
            continue
        try:
            if spec['asset'] not in feed_cache:
                feed_cache[spec['asset']]=feed_reader(db,spec['asset'],now)
            feed=feed_cache[spec['asset']]
            status='paused_missing_feed' if not feed.get('ok') else 'paused_stale_feed' if not feed.get('is_live') else 'watching_closed_candles'
            if status=='watching_closed_candles':
                if spec.get('anchor_start_ny'):
                    name='review_market_crt'; args={'asset':spec['asset'],'anchor_start_ny':spec['anchor_start_ny'],
                        'anchor_timeframe':spec['anchor_timeframe'],'through_ny':datetime.fromtimestamp(min(now,row['expires_at'])//60*60,NY).isoformat(),'confirmation_timeframe':None}
                else:
                    name='review_market_session'; args={k:spec[k] for k in ('asset','date_ny','shift')}
                cache_key=json.dumps([name,args],sort_keys=True)
                if cache_key not in cache:
                    cache[cache_key]=evaluator(db,name,args)
                result=cache[cache_key]
                if not result.get('ok'):
                    status='analysis_unavailable'
                else:
                    for event in extract_events(result):
                        if not matches(spec['event'],event['kind']) or not max(row['created_at'],row['starts_at']-1,now-600)<event['at']<=min(now,row['expires_at']):
                            continue
                        key=hashlib.sha256((row['id']+event['key']).encode()).hexdigest()
                        with db() as conn:
                            added=conn.execute('''INSERT INTO gbop_market_alerts(id,watch_id,guild_id,user_id,event_at,payload,state,updated_at)
                                SELECT ?,?,?,?,?,?,'pending',? WHERE EXISTS(SELECT 1 FROM gbop_market_watches WHERE id=? AND state='active')
                                ON CONFLICT(id) DO NOTHING RETURNING id''',
                                (key,row['id'],guild_id,user_id,event['at'],json.dumps(event),now,row['id'])).fetchone()
                            queued += bool(added)
            with db() as conn:
                conn.execute('UPDATE gbop_market_watches SET last_status=?,last_checked_at=? WHERE id=?',(status,now,row['id']))
        except Exception:
            log.exception('Market watch evaluation failed; no market conclusion emitted')
            with db() as conn:
                conn.execute("UPDATE gbop_market_watches SET last_status='analysis_unavailable',last_checked_at=? WHERE id=?",(now,row['id']))
    return queued


async def deliver_alerts(db,guild_id,owner_id,sender,role_check,now=None):
    now=int(time.time() if now is None else now)
    def pending():
        with db() as conn:
            return conn.execute("SELECT * FROM gbop_market_alerts WHERE guild_id=? AND state='pending' ORDER BY event_at LIMIT 12",(guild_id,)).fetchall()
    rows=await asyncio.to_thread(pending)
    delivered=0
    for row in rows:
        user_id=row['user_id']
        denial=await asyncio.to_thread(member_access_error,db,guild_id,user_id,owner_id)
        allowed=not denial and await role_check(user_id)
        def claim():
            with db() as conn:
                watch=conn.execute('SELECT state,expires_at FROM gbop_market_watches WHERE id=? AND guild_id=? AND user_id=?',(row['watch_id'],guild_id,user_id)).fetchone()
                if not allowed or not watch or watch['state']!='active' or watch['expires_at']<now-120 or row['event_at']<now-600:
                    conn.execute("UPDATE gbop_market_alerts SET state='cancelled',updated_at=? WHERE id=? AND state='pending'",(now,row['id']))
                    return False
                return bool(conn.execute("UPDATE gbop_market_alerts SET state='sending',updated_at=? WHERE id=? AND state='pending' RETURNING id",(now,row['id'])).fetchone())
        if not await asyncio.to_thread(claim):
            continue
        state='delivered'; message_id=None
        try:
            message_id=await sender(user_id,json.loads(row['payload'])['text'])
            delivered += 1
        except Exception as exc:
            # Never blindly replay an ambiguous network send after a restart.
            state='delivery_failed' if getattr(exc,'status',None) in (400,401,403,404) else 'delivery_uncertain'
            log.warning('Market alert delivery %s (%s)',state,type(exc).__name__)
        def finish():
            with db() as conn:
                conn.execute('UPDATE gbop_market_alerts SET state=?,updated_at=?,message_id=? WHERE id=?',(state,now,str(message_id) if message_id else None,row['id']))
        await asyncio.to_thread(finish)
    return delivered


def prepare_next_shift(db,fingerprints,now=None):
    """One changed asset/shift per tick; bounded retention and no model calls."""
    from gbop_voice_web.market_data import read_feed, market_tool, latest_available_shift_date, history_bars
    from datetime import timedelta
    now=int(time.time() if now is None else now)
    with db() as conn:
        assets=[r['asset'] for r in conn.execute('SELECT asset FROM gbop_market_feed ORDER BY asset').fetchall()]
    candidates=sorted(((fingerprints.get(('rotation',a,s),0),a,s) for a in assets for s in ('day','night')),key=lambda x:x[0])
    selected=None
    for _,asset,shift in candidates[:3]:
        fingerprints[('rotation',asset,shift)]=now
        feed=read_feed(db,asset,now)
        if not feed.get('ok'):
            continue
        try:
            day=latest_available_shift_date(feed,shift,db)
        except ValueError:
            continue
        begins=datetime.fromisoformat(day).replace(hour=7 if shift=='day' else 19,tzinfo=NY)
        start=int(begins.timestamp()); end=int((begins+timedelta(hours=5)).timestamp())
        bars,step=history_bars(db,feed,start,end)
        if not bars:
            continue
        key=(asset,day,shift)
        token=(asset,day,shift,hashlib.sha256(json.dumps(bars,separators=(',',':')).encode()).hexdigest(),now//300)
        if fingerprints.get(key)==token:
            continue
        selected=(asset,day,shift,token)
        break
    if selected is None:
        return None
    asset,day,shift,token=selected
    result=market_tool(db,'review_market_session',{'asset':asset,'date_ny':day,'shift':shift})
    if not result.get('ok'):
        return None
    review=result['review']; story=review.get('shift_story',{})
    # Keep the prepared copy small; full evidence remains available on demand.
    brief={'as_of_ny':result.get('available_through_ny'),'recap':story.get('recap'),
           'range_transitions':story.get('range_transitions',[]),'paired_smt':review.get('paired_smt'),
           'paired_context':review.get('paired_context'),
           'selected_ranges':[{'anchor_start_ny':r.get('anchor_start_ny'),'variant_evidence':r.get('variant_evidence'),
              'candle_lifecycle':r.get('candle_lifecycle'),'invalidated_at_ny':r.get('invalidated_at_ny')}
              for r in story.get('ranges',[]) if r.get('role')=='selected_range']}
    payload=json.dumps(brief,separators=(',',':'))
    if len(payload.encode())>131072:
        brief['selected_ranges']=[{k:v for k,v in r.items() if k!='candle_lifecycle'} for r in brief['selected_ranges']]
        brief['detail_omitted']='Request review_market_session/CRT for all candle details.'
        payload=json.dumps(brief,separators=(',',':'))
    if len(payload.encode())>131072:
        brief.pop('paired_context',None); brief['detail_omitted']='Request full review for additional paired ranges.'
        payload=json.dumps(brief,separators=(',',':'))
    if len(payload.encode())>131072:
        return None
    with db() as conn:
        conn.execute('''INSERT INTO gbop_prepared_shifts(asset,date_ny,shift,version,prepared_at,payload)
          VALUES (?,?,?,?,?,?) ON CONFLICT(asset,date_ny,shift) DO UPDATE SET
          version=excluded.version,prepared_at=excluded.prepared_at,payload=excluded.payload''',(asset,day,shift,VERSION,now,payload))
        conn.execute('DELETE FROM gbop_prepared_shifts WHERE prepared_at<?',(now-7*86400,))
        conn.execute("DELETE FROM gbop_market_alerts WHERE updated_at<? AND state<>'pending'",(now-7*86400,))
        conn.execute("DELETE FROM gbop_market_watches WHERE expires_at<? AND state<>'active'",(now-7*86400,))
    fingerprints[(asset,day,shift)]=token
    fingerprints[('checked',asset,day,shift)]=now
    return {'asset':asset,'date_ny':day,'shift':shift,'prepared_at':now}


def start_watch_runtime(client,db,guild_id,owner_id,member_role_id):
    global TASK
    if TASK is not None and not TASK.done():
        return TASK
    instance=uuid.uuid4().hex
    async def sender(user_id,text):
        import discord
        user=client.get_user(user_id) or await client.fetch_user(user_id)
        message=await user.send(text[:1900],allowed_mentions=discord.AllowedMentions.none())
        return message.id
    async def role_check(user_id):
        guild=client.get_guild(guild_id)
        if not guild:
            return False
        try:
            member=await guild.fetch_member(user_id)
            return member.id==owner_id or any(r.id==member_role_id for r in member.roles)
        except Exception:
            return False
    async def run():
        fingerprints={}
        await client.wait_until_ready()
        while not client.is_closed():
            try:
                now=int(time.time())
                if await asyncio.to_thread(runtime_lease,db,instance,now):
                    await asyncio.to_thread(poll_watches,db,guild_id,owner_id,now)
                    await deliver_alerts(db,guild_id,owner_id,sender,role_check)
                    prepared=await asyncio.to_thread(prepare_next_shift,db,fingerprints)
                    if prepared:
                        log.info('[GBOP-TAB] Prepared %s %s %s',prepared['asset'],prepared['date_ny'],prepared['shift'])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('[GBOP-TAB] Monitor cycle failed; retrying without a market claim')
            await asyncio.sleep(30)
    TASK=asyncio.create_task(run(),name='gbop-market-watch')
    log.info('[GBOP-TAB] Closed-candle watch runtime started; private opt-in alerts, 30-second cycle')
    return TASK
