import asyncio
import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from gbop_voice_web.market_watch import init_watches, runtime_lease, watch_tool, shift_window, VERSION
from gbop_voice_web.market_watch_runtime import extract_events, poll_watches, deliver_alerts

T=int(datetime.fromisoformat('2026-10-02T10:00:00-04:00').timestamp())
def iso(t):
    from gbop_voice_web.market_watch import NY
    return datetime.fromtimestamp(t,NY).isoformat()
def row(i,o=102,h=103,l=100,c=101):
    return dict(start_ny=iso(T+300*i),end_ny=iso(T+300*(i+1)),open=o,high=h,low=l,close=c,complete=True)


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:',check_same_thread=False)
        self.conn.row_factory=sqlite3.Row
        self.db=lambda:self.conn
        init_watches(self.db)
        self.conn.execute('CREATE TABLE members(guild_id INTEGER,user_id INTEGER,activated INTEGER,leadership_ack INTEGER,revoked INTEGER)')
        self.conn.executemany('INSERT INTO members VALUES(1,?,1,1,0)',[(7,),(8,)])
        self.conn.commit()
        runtime_lease(self.db,'test',T)
        self.feed_patch=patch('gbop_voice_web.market_data.read_feed',side_effect=lambda db,a,now=None:{'ok':True,'asset':a,'is_live':True})
        self.feed_patch.start()
    def tearDown(self):
        self.feed_patch.stop(); self.conn.close()
    def tool(self,action='start',user=7,now=T,**args):
        return watch_tool(self.db,1,user,42,'manage_market_watch',dict(action=action,asset='NAS100',event='body_soup',shift='day',anchor_start_ny=None,anchor_timeframe=None,watch_id=None,**args),now)
    def start(self,user=7):
        return self.tool(user=user)['watch']['id']
    def result(self,at=T+300):
        return {'ok':True,'asset':'NAS100','review':{'anchor':{'start_ny':iso(T-3600)},'entry_confirmed':False,
            'candle_lifecycle':{'purge_candles':[{'purge_type':'body_soup','bar_open_ny':iso(at-300),'bar_close_ny':iso(at),
            'timeframe':'M5','purged_side':'buy','purged_level':100,'csd':{'status':'not_observed_by_review_cutoff'}}]}}}
    def poll(self,at=T+330,result=None,live=True):
        return poll_watches(self.db,1,42,at,evaluator=lambda *a:result or self.result(),
            feed_reader=lambda *a:{'ok':True,'is_live':live})
    def test_registration_scoped_and_idempotent(self):
        key=self.start(); again=self.tool()
        self.assertTrue(again['already_registered'])
        self.assertEqual(key,again['watch']['id'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_market_watches').fetchone()[0],1)
    def test_no_heartbeat_no_false_promise(self):
        self.conn.execute('DELETE FROM gbop_watch_runtime'); self.conn.commit()
        self.assertEqual(self.tool()['status'],'monitor_unavailable')
    def test_revoked_member_cannot_subscribe(self):
        self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=7'); self.conn.commit()
        self.assertFalse(self.tool()['ok'])
    def test_member_cannot_read_peer_or_cancel_peer(self):
        a=self.start(7); b=self.start(8)
        listed=self.tool('list',user=7)['watches']
        self.assertEqual([w['id'] for w in listed],[a])
        r=watch_tool(self.db,1,7,42,'manage_market_watch',{'action':'cancel','watch_id':b},T)
        self.assertEqual(r['cancelled_count'],0)
    def test_owner_existing_bypass_unchanged(self):
        self.assertTrue(self.tool(user=42)['ok'])
    def test_body_only_does_not_alert_wick(self):
        self.start(); r=self.result(); r['review']['candle_lifecycle']['purge_candles'][0]['purge_type']='wick_soup'
        self.assertEqual(self.poll(result=r),0)
    def test_body_identity_not_blocked_by_unconfirmed_entry(self):
        self.start(); self.assertEqual(self.poll(),1)
    def test_restart_replay_deduplicates(self):
        self.start(); self.assertEqual(self.poll(),1); self.assertEqual(self.poll(),0)
    def test_no_retroactive_notifications(self):
        self.start(); self.assertEqual(self.poll(result=self.result(T-300)),0)
    def test_future_event_not_alerted(self):
        self.start(); self.assertEqual(self.poll(result=self.result(T+600)),0)
    def test_stale_feed_pauses(self):
        key=self.start(); self.assertEqual(self.poll(live=False),0)
        self.assertEqual(self.conn.execute('SELECT last_status FROM gbop_market_watches WHERE id=?',(key,)).fetchone()[0],'paused_stale_feed')
    def test_revocation_stops_existing_watch(self):
        key=self.start(); self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=7'); self.conn.commit()
        self.assertEqual(self.poll(),0)
        self.assertEqual(self.conn.execute('SELECT state FROM gbop_market_watches WHERE id=?',(key,)).fetchone()[0],'access_blocked')
    def test_no_events_after_expiry(self):
        self.start(); self.assertEqual(self.poll(at=T+8000,result=self.result(T+7900)),0)
    def test_delivery_once(self):
        self.start(); self.poll(); sent=[]
        async def sender(u,t): sent.append((u,t)); return 'dm-1'
        async def role(u): return True
        self.assertEqual(asyncio.run(deliver_alerts(self.db,1,42,sender,role,T+340)),1)
        self.assertEqual(asyncio.run(deliver_alerts(self.db,1,42,sender,role,T+350)),0)
        self.assertEqual(len(sent),1)
    def test_role_removed_before_delivery(self):
        self.start(); self.poll()
        async def sender(u,t): raise AssertionError('Unauthorized delivery')
        async def role(u): return False
        self.assertEqual(asyncio.run(deliver_alerts(self.db,1,42,sender,role,T+340)),0)
    def test_cancel_after_queue_prevents_delivery(self):
        key=self.start(); self.poll()
        watch_tool(self.db,1,7,42,'manage_market_watch',{'action':'cancel','watch_id':key},T+335)
        async def sender(u,t): raise AssertionError('Cancelled delivery')
        async def role(u): return True
        self.assertEqual(asyncio.run(deliver_alerts(self.db,1,42,sender,role,T+340)),0)
    def test_network_uncertainty_is_not_replayed(self):
        self.start(); self.poll(); sent=[]
        async def sender(u,t): sent.append(u); raise TimeoutError('uncertain acknowledgement')
        async def role(u): return True
        asyncio.run(deliver_alerts(self.db,1,42,sender,role,T+340))
        asyncio.run(deliver_alerts(self.db,1,42,sender,role,T+350))
        self.assertEqual(sent,[7])
        self.assertEqual(self.conn.execute('SELECT state FROM gbop_market_alerts').fetchone()[0],'delivery_uncertain')
    def test_runtime_lease_prevents_two_workers(self):
        self.assertFalse(runtime_lease(self.db,'second',T+10))
        self.assertTrue(runtime_lease(self.db,'second',T+100))
    def test_pause_stale_registration_is_explicit(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value={'ok':True,'is_live':False}):
            self.assertEqual(self.tool()['watch']['last_status'],'paused_stale_feed')
    def test_missing_feed_not_registered(self):
        with patch('gbop_voice_web.market_data.read_feed',return_value={'ok':False}):
            self.assertEqual(self.tool()['status'],'not_connected')
    def test_prepared_brief_missing_is_not_invented(self):
        r=watch_tool(self.db,1,7,42,'get_prepared_market_brief',{'asset':'NAS','shift':'day'},T)
        self.assertEqual(r['status'],'not_prepared')
    def test_shift_expiry_and_next_session(self):
        _,start,end=shift_window(T,'day')
        self.assertEqual(end.hour,12)
        _,night,finish=shift_window(T,'night')
        self.assertEqual(night.hour,21); self.assertEqual(finish.hour,0)
    def test_injected_identity_cannot_override_scope(self):
        self.tool(user_id=8,guild_id=2)
        r=self.conn.execute('SELECT guild_id,user_id FROM gbop_market_watches').fetchone()
        self.assertEqual(tuple(r),(1,7))

class TabIntegrationTests(unittest.TestCase):
    def test_both_transports_register_and_authorize_watch_tools(self):
        for name in ('bot.py','gbop_voice_web/server.py'):
            text=Path(name).read_text()
            self.assertIn('.extend(WATCH_TOOLS)',text)
            self.assertIn('if name in WATCH_NAMES:',text)
            self.assertIn('watch_tool(db, GTOP_GUILD_ID, user_id,',text)
    def test_primary_process_starts_runtime(self):
        text=Path('bot.py').read_text()
        self.assertIn('start_watch_runtime(client, db, GTOP_GUILD_ID, GTOP_OWNER_USER_ID, GTOP_MEMBER_ROLE_ID)',text)
    def test_classification_is_in_real_lifecycle(self):
        from gbop_voice_web.candle_lifecycle import lifecycle_review
        model=row(0,99,103,98,102); seq=[model,row(1,h=104),row(2,l=97,c=99)]
        bars=[dict(time=ts['start'],**ts['prices']) for ts in []]
        bars=[dict(time=int(datetime.fromisoformat(r['start_ny']).timestamp()),**{k:r[k] for k in ('open','high','low','close')}) for r in seq]
        anchor=dict(complete=True,start_ny=iso(T-3600),end_ny=iso(T),high=100,low=90,midpoint=95)
        r=lifecycle_review(bars,anchor,'M5',T+900,300)
        body=next(x for x in r['purge_candles'] if x['purge_type']=='body_soup')
        self.assertEqual(body['super_soup_structure']['structural_quality'],'clean')
    def test_voice_guidance_requires_registration(self):
        from gbop_voice_web.market_data import MARKET_PROMPT, LIVE_MARKET_PROMPT
        for text in (MARKET_PROMPT,LIVE_MARKET_PROMPT):
            self.assertIn('manage_market_watch',text)
            self.assertIn('private Discord DM',text)
    def test_latest_shift_date_uses_available_closed_candles(self):
        from gbop_voice_web.market_data import latest_available_shift_date
        feed={'bars':[{'time':T}], 'bars_m1':[]}
        self.assertEqual(latest_available_shift_date(feed,'day'),'2026-10-02')

if __name__=='__main__': unittest.main()

class RetainedHistoryTests(unittest.TestCase):
    def test_latest_date_uses_history_when_live_snapshot_has_no_shift_candles(self):
        from gbop_voice_web.market_data import latest_available_shift_date
        conn=sqlite3.connect(':memory:'); conn.row_factory=sqlite3.Row
        conn.execute('CREATE TABLE gbop_market_history(asset TEXT,symbol TEXT,step INTEGER,day_utc INTEGER,payload TEXT)')
        conn.execute('INSERT INTO gbop_market_history VALUES (?,?,?,?,?)',('NAS100','USTECm',300,T//86400*86400,json.dumps([{'time':T}])))
        conn.commit()
        try:
            feed={'asset':'NAS100','symbol':'USTECm','bars':[{'time':T+7*3600}],'bars_m1':[]}
            self.assertEqual(latest_available_shift_date(feed,'day',lambda:conn),'2026-10-02')
        finally:
            conn.close()
    def test_watch_dispatch_ignores_forged_member_and_checks_access(self):
        from test_member_readiness import function
        from unittest.mock import Mock
        for path,name,owner in [('bot.py','ai_execute_tool','GTOP_OWNER_USER_ID'),('gbop_voice_web/server.py','run_tool','OWNER_USER_ID')]:
            watched=Mock(return_value={'ok':True})
            ns={'member_access_error':Mock(return_value=None),'db':object(),'GTOP_GUILD_ID':1,owner:99,'WATCH_NAMES':{'manage_market_watch'},'watch_tool':watched}
            invoke=function(path,name,ns)
            args={'action':'list','user_id':99,'guild_id':2}
            self.assertTrue(invoke(10,'manage_market_watch',args)['ok'])
            self.assertEqual(watched.call_args.args[1:4],(1,10,99))
            ns['member_access_error'].return_value='revoked'; watched.reset_mock()
            self.assertFalse(invoke(10,'manage_market_watch',args)['ok']); watched.assert_not_called()
