"""Regression for the Oct 2/3 call; synthetic bars preserve its matched facts."""
import asyncio
import contextlib
import copy
import json
import os
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.smt_evidence import compare_ranges, align_bars
from gbop_voice_web import market_data as market
from gbop_voice_web.journal_recall import history, send_history, messages, JOURNAL_RECALL_TOOLS
from gbop_voice_web.voice_runtime import guarded_voice_tool, recovery_options, VoiceRateLimitRecovery, compact_voice_tool_result
from test_member_readiness import function
from test_voice_latency import method


def metals(night=False):
    start = parse_time('2026-10-02T20:00:00' if night else '2026-10-02T08:00:00')
    gold, silver = [], []
    for n in range(240):
        g = 4210 if n < 72 else 4190 if n < 148 else 4160
        s = 61.7 if n < 72 else 61.2 if n < 174 else 60.8
        gold.append(dict(time=start+n*60, open=g, high=g+1, low=g-1, close=g))
        silver.append(dict(time=start+n*60, open=s, high=s+.1, low=s-.1, close=s))
    gold[0].update(high=4227.775, low=4174.379)
    silver[0].update(high=62.046, low=60.789)
    gold[62].update(open=4222.854, high=4225.432, low=4221.514, close=4222.471)
    silver[62].update(open=61.977, high=62.061, low=61.965, close=61.987)
    silver[63]['high'] = 62.065
    gold[72].update(open=4203, high=4204, low=4200, close=4200)
    silver[72].update(open=61.5, high=61.6, low=61.4, close=61.4)
    gold[148].update(open=4176, high=4177, low=4173, close=4173)
    gold[179].update(open=4160, high=4161, low=4158, close=4158.798)
    silver[174].update(open=60.9, high=61, low=60.75, close=60.81)
    silver[179].update(open=60.81, high=60.9, low=60.79, close=60.81)
    silver[239].update(open=60, high=60.1, low=59.7, close=59.766)
    return (start, dict(asset='XAUUSD', symbol='XAUUSDm', bars=gold, step=60),
            dict(asset='XAGUSD', symbol='XAGUSDm', bars=silver, step=60))


class SMTTests(unittest.TestCase):
    def compare(self, left=None, right=None, detect=True):
        start, a, b = metals()
        return compare_ranges(a if left is None else left, b if right is None else right,
                              start, start+3600, start+14400,
                              detect_through=start+7200 if detect else None)

    def test_october_2_matched_moment_is_bearish_9ate8_smt(self):
        result = self.compare()
        self.assertTrue(result['divergence_confirmed'])
        self.assertEqual(len(result['events']), 1)
        e = result['events'][0]
        self.assertEqual(e['direction'], 'bearish')
        self.assertEqual(e['play_context'], '9ate8')
        self.assertEqual(e['bar_open_ny'], '2026-10-02T09:02:00-04:00')
        self.assertEqual(e['swept_asset'], 'XAGUSD')
        self.assertEqual(e['swept_boundary'], 62.046)
        self.assertEqual(e['swept_bar_extreme'], 62.061)
        self.assertEqual(e['peer_boundary'], 4227.775)
        self.assertEqual(e['peer_extreme_through_event'], 4225.432)
        self.assertFalse(e['entry_confirmed'])
        self.assertFalse(e['exact_tick_time_known'])

    def test_later_invalidation_does_not_erase_divergence_or_targets(self):
        result = self.compare(); e = result['events'][0]
        self.assertTrue(e['anchors_valid_at_event'])
        self.assertEqual(result['invalidating_closes_ny']['XAUUSD'], '2026-10-02T11:00:00-04:00')
        self.assertEqual(result['invalidating_closes_ny']['XAGUSD'], '2026-10-02T12:00:00-04:00')
        outcomes = e['objectives_after_divergence']
        self.assertEqual(outcomes['XAUUSD']['midpoint']['first_later_touch_ny'], '2026-10-02T09:12:00-04:00')
        self.assertEqual(outcomes['XAUUSD']['opposing_liquidity']['first_later_touch_ny'], '2026-10-02T10:28:00-04:00')
        self.assertEqual(outcomes['XAGUSD']['opposing_liquidity']['first_later_touch_ny'], '2026-10-02T10:54:00-04:00')

    def test_peer_same_hour_catchup_keeps_timing_but_disqualifies_smt(self):
        _, gold, silver = metals(); gold['bars'][70]['high'] = 4228
        r = self.compare(gold, silver)
        self.assertEqual(r['events'][0]['peer_later_swept_at_ny'], '2026-10-02T09:10:00-04:00')
        self.assertFalse(r['divergence_confirmed'])
        self.assertTrue(r['timing_asynchrony_observed'])
        self.assertEqual(r['events'][0]['setup_interval']['status'], 'both_assets_purged_same_setup_interval')

    def test_same_bar_both_sweeps_have_no_verified_intrabar_divergence(self):
        _, gold, silver = metals(); gold['bars'][62]['high'] = 4228
        r = self.compare(gold, silver)
        self.assertFalse(r['divergence_confirmed'])
        self.assertIn('buy_side', r['same_bar_both_swept_sides'])

    def test_peer_already_swept_is_not_mistaken_for_nonconfirmation(self):
        _, gold, silver = metals(); gold['bars'][61]['high'] = 4228
        r = self.compare(gold, silver)
        self.assertEqual(r['events'][0]['swept_asset'], 'XAUUSD')
        self.assertEqual(r['events'][0]['peer_later_swept_at_ny'], '2026-10-02T09:02:00-04:00')

    def test_missing_anchor_is_unknown_not_no_setup(self):
        _, gold, silver = metals(); gold['bars'].pop(10)
        r = self.compare(gold, silver)
        self.assertEqual(r['status'], 'insufficient_paired_evidence')
        self.assertFalse(r['divergence_confirmed'])

    def test_missing_matched_prefix_cannot_prove_nonconfirmation(self):
        _, gold, silver = metals(); gold['bars'].pop(61)
        r = self.compare(gold, silver)
        self.assertEqual(r['status'], 'insufficient_paired_evidence')
        self.assertFalse(r['paired_coverage_complete'])

    def test_gap_after_event_preserves_event_but_not_later_targets(self):
        _, gold, silver = metals(); gold['bars'].pop(65)
        r = self.compare(gold, silver)
        self.assertFalse(r['divergence_confirmed'])
        self.assertTrue(r['timing_asynchrony_observed'])
        self.assertEqual(r['events'][0]['setup_interval']['status'], 'provisional_timing_asynchrony')
        self.assertFalse(r['paired_coverage_complete'])
        self.assertIsNone(r['events'][0]['objectives_after_divergence']['XAUUSD']['opposing_liquidity']['first_later_touch_ny'])

    def test_mixed_resolutions_do_not_invent_minute_precision(self):
        _, gold, silver = metals()
        silver['bars'] = align_bars(silver['bars'], 60, 300); silver['step'] = 300
        r = self.compare(gold, silver)
        self.assertEqual(r['precision_seconds'], 300)
        self.assertEqual(r['events'][0]['bar_open_ny'], '2026-10-02T09:00:00-04:00')
        self.assertEqual(r['events'][0]['bar_close_ny'], '2026-10-02T09:05:00-04:00')

    def test_same_event_bar_target_order_is_explicit(self):
        _, gold, silver = metals(); gold['bars'][62]['low'] = 4200
        r = self.compare(gold, silver)
        self.assertTrue(r['events'][0]['objectives_after_divergence']['XAUUSD']['midpoint']['same_event_bar_touch_order_unknown'])

    def test_bullish_direction_uses_sell_side_nonconfirmation(self):
        _, gold, silver = metals()
        for item in (gold, silver):
            for b in item['bars']:
                old = dict(b); pivot = 10000 if item['asset']=='XAUUSD' else 150
                b.update(open=pivot-old['open'], high=pivot-old['low'], low=pivot-old['high'], close=pivot-old['close'])
        r = self.compare(gold, silver)
        self.assertEqual(r['events'][0]['direction'], 'bullish')
        self.assertEqual(r['events'][0]['side'], 'sell_side')

    def test_night_shift_and_same_asset_validation(self):
        start, gold, silver = metals(True)
        r = compare_ranges(gold, silver, start, start+3600, start+14400, detect_through=start+7200)
        self.assertEqual(r['events'][0]['bar_open_ny'], '2026-10-02T21:02:00-04:00')
        self.assertEqual(r['events'][0]['play_context'], '9ate8')
        self.assertFalse(compare_ranges(gold, gold, start, start+3600, start+7200)['ok'])

    def test_prior_invalidation_is_separate_from_historical_divergence(self):
        start, gold, silver = metals()
        silver['bars'][62]['high']=62; silver['bars'][63]['high']=62
        gold['bars'][119].update(low=4170, close=4170)
        silver['bars'][122]['high']=62.1
        r = self.compare(gold, silver, detect=False)
        buy = next(e for e in r['events'] if e['side']=='buy_side')
        self.assertFalse(buy['anchors_valid_at_event'])
        self.assertEqual(buy['play_context'], 'selected_range_SMT')

    def test_session_tool_includes_pair_and_followup_objectives(self):
        start, gold, silver = metals()
        conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        conn.execute(market.CREATE_SQL); conn.execute(market.HISTORY_SQL)
        for item in (gold, silver):
            payload = dict(asset=item['asset'], symbol=item['symbol'], bid=1, ask=2,
                           tick_time=start+14400, bars=[], bars_m1=item['bars'])
            conn.execute('INSERT INTO gbop_market_feed VALUES (?,?,?,?)', (item['asset'],start+14400,start+14400,json.dumps(payload)))
        @contextlib.contextmanager
        def db(): yield conn
        r = market.market_tool(db, 'review_market_session', dict(asset='gold', date_ny='2026-10-02', shift='day'))
        self.assertTrue(r['review']['paired_smt']['divergence_confirmed'])
        e = r['review']['paired_smt']['events'][0]
        self.assertEqual(e['objectives_after_divergence']['XAUUSD']['opposing_liquidity']['first_later_touch_ny'], '2026-10-02T10:28:00-04:00')
        voice = compact_voice_tool_result('review_market_session', r)
        self.assertEqual(next(iter(voice['review'])), 'paired_smt')
        self.assertEqual(voice['review']['paired_smt'], r['review']['paired_smt'])
        direct = market.market_tool(db, 'review_market_smt', dict(asset='gold', comparison_asset='silver', anchor_start_ny='2026-10-02T08:00:00', through_ny='2026-10-02T12:00:00', anchor_timeframe='H1'))
        self.assertTrue(direct['divergence_confirmed'])
        self.assertEqual(direct['events'][0]['bar_open_ny'], e['bar_open_ny'])


class JournalRecallTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT,lease_until BIGINT,last_tick BIGINT,state TEXT);
            CREATE TABLE members(guild_id INT,user_id INT,activated INT,revoked INT);
            INSERT INTO members VALUES (1,100,1,0),(1,200,1,0);
        """)
        self.conn.execute('CREATE TABLE theses (id INT,guild_id INT,user_id INT,status TEXT)')
        self.conn.execute('CREATE TABLE journals (id INT,guild_id INT,user_id INT,thesis_id INT,description TEXT,result_r REAL,rule_adherence TEXT,study_note TEXT,created_at INT)')
        self.conn.executemany('INSERT INTO theses VALUES (?,?,?,?)', [(10,1,100,'CLOSED'),(20,1,200,'CLOSED'),(30,1,100,'OPEN'),(40,2,100,'OPEN'),(50,1,300,'CLOSED')])
        self.conn.executemany('INSERT INTO journals VALUES (?,?,?,?,?,?,?,?,?)', [
            (5,1,100,10,'owner first',2,'yes','study',1000),
            (7,1,200,20,'member private',None,'yes',None,1001),
            (9,1,100,30,'owner second',None,None,None,1002),
            (11,2,100,40,'another guild',9,None,None,1003)])

    @contextlib.contextmanager
    def db(self): yield self.conn

    def test_member_isolation_counts_and_local_numbers(self):
        owner = history(self.db,1,100,{'limit':5,'user_id':200,'guild_id':2})
        self.assertEqual(owner['journal_count'],2); self.assertEqual(owner['trade_count'],2)
        self.assertEqual(owner['open_trade_count'],1); self.assertEqual(owner['closed_trade_count'],1)
        self.assertEqual([r['journal_number'] for r in owner['journals']],[2,1])
        self.assertEqual([r['trade_id'] for r in owner['journals']],[2,1])
        self.assertNotIn('member private',str(owner)); self.assertNotIn('another guild',str(owner))
        member=history(self.db,1,200,{})
        self.assertEqual(member['journals'][0]['trade_id'],1)
        self.assertEqual(member['open_trade_count'],0); self.assertEqual(member['trade_count'],1)
        self.assertIsNone(member['journals'][0]['result_r'])

    def test_no_journals_does_not_erase_closed_trade_records(self):
        r=history(self.db,1,300,{})
        self.assertEqual(r['journal_count'],0); self.assertEqual(r['trade_count'],1)
        self.assertEqual(r['open_trade_count'],0)

    def test_empty_member_is_not_owners_records(self):
        r=history(self.db,1,999,{})
        self.assertTrue(r['ok']); self.assertEqual(r['journals'],[]); self.assertEqual(r['trade_count'],0)

    def test_pagination_has_stable_member_numbers(self):
        r=history(self.db,1,100,{'limit':1})
        self.assertTrue(r['has_more']); self.assertEqual(r['next_offset'],1)
        next_page=history(self.db,1,100,{'limit':1,'offset':r['next_offset']})
        self.assertEqual(next_page['journals'][0]['journal_number'],1)
        self.assertFalse(next_page['has_more'])

    def test_cross_member_bad_link_does_not_leak_trade_number(self):
        self.conn.execute('UPDATE journals SET thesis_id=20 WHERE id=9')
        self.assertIsNone(history(self.db,1,100,{})['journals'][0]['trade_id'])

    def test_private_delivery_uses_authenticated_recipient_and_disables_mentions(self):
        client=Mock(); client.post.side_effect=[NS(status_code=200,json=lambda:{'id':'dm'}),NS(status_code=200),NS(status_code=200)]
        with patch.dict(os.environ,{'DISCORD_TOKEN':'test-only'}), patch('httpx.Client') as factory:
            factory.return_value.__enter__.return_value=client
            r=send_history(self.db,1,200,{'user_id':100,'recipient_id':100})
        self.assertTrue(r['ok']); self.assertEqual(r['sent_count'],2)
        self.assertEqual(client.post.call_args_list[0].kwargs['json']['recipient_id'],'200')
        for call in client.post.call_args_list[1:]:
            self.assertEqual(call.kwargs['json']['allowed_mentions'],{'parse':[]})
            self.assertNotIn('owner first',call.kwargs['json']['content'])

    def test_delivery_partial_failure_is_not_false_success(self):
        client=Mock(); client.post.side_effect=[NS(status_code=200,json=lambda:{'id':'dm'}),NS(status_code=200),NS(status_code=403)]
        with patch.dict(os.environ,{'DISCORD_TOKEN':'test-only'}), patch('httpx.Client') as factory:
            factory.return_value.__enter__.return_value=client
            r=send_history(self.db,1,200,{})
        self.assertFalse(r['ok']); self.assertEqual(r['sent_count'],1)
        self.assertEqual(r['journal_count'],1)

    def test_no_delivery_config_retains_retrieved_records(self):
        with patch.dict(os.environ,{'DISCORD_TOKEN':''}): r=send_history(self.db,1,200,{})
        self.assertFalse(r['ok']); self.assertEqual(r['sent_count'],0)
        self.assertEqual(r['journals'][0]['summary'],'member private')

    def test_long_saved_journal_is_chunked_without_silent_truncation(self):
        self.conn.execute('UPDATE journals SET description=? WHERE id=7',('x'*6000,))
        out=messages(history(self.db,1,200,{}))
        self.assertTrue(all(len(s)<=1800 for s in out))
        self.assertEqual(''.join(out).count('x'),6000)

    def test_browser_package_import_follows_path_bootstrap(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[1]/'gbop_voice_web/server.py').read_text()
        self.assertLess(source.index('sys.path.insert('), source.index('from gbop_voice_web.journal_recall import'))

    def test_new_tool_has_no_recipient_or_owner_override(self):
        props=JOURNAL_RECALL_TOOLS[0]['parameters']['properties']
        self.assertEqual(set(props),{'limit','offset','delivery_action'})

    def test_both_platforms_use_shared_member_scoped_recall(self):
        for path,name in [('bot.py','ai_get_journal_history'),('gbop_voice_web/server.py','tool_get_journal_history')]:
            fn=function(path,name,dict(recall_journal_history=history,db=self.db,GTOP_GUILD_ID=1))
            self.assertEqual(fn(200,{})['journals'][0]['summary'],'member private')

    def test_both_platforms_recheck_access_before_private_delivery(self):
        for path,name,owner in [('bot.py','ai_execute_tool','GTOP_OWNER_USER_ID'),('gbop_voice_web/server.py','run_tool','OWNER_USER_ID')]:
            sender=Mock(); checker=Mock(return_value='Access revoked')
            ns=dict(db=self.db,GTOP_GUILD_ID=1,member_access_error=checker,send_journal_history=sender,**{owner:100})
            fn=function(path,name,ns)
            self.assertFalse(fn(200,'send_journal_history',{})['ok']); sender.assert_not_called()
            checker.return_value=None; sender.return_value={'ok':True}
            self.assertTrue(fn(200,'send_journal_history',{'user_id':100})['ok'])
            self.assertEqual(sender.call_args.args[1:3],(1,200))


class RecoverySafetyTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        return NS(_voice_turn_count=1,_recovery_active=True)

    async def test_recovery_allows_reads_but_blocks_record_mutations(self):
        for name in ('open_trade','close_trade','delete_trade','delete_journal','save_journal_entry','save_risk_profile'):
            fn=AsyncMock(); r=await guarded_voice_tool(self.session(),name,{},name,fn)
            self.assertFalse(r['ok']); fn.assert_not_awaited()
        fn=AsyncMock(return_value={'ok':True,'journal_count':2})
        r=await guarded_voice_tool(self.session(),'get_journal_history',{},'read',fn)
        self.assertEqual(r['journal_count'],2); fn.assert_awaited_once()

    async def test_private_delivery_is_not_duplicated_under_new_call_id(self):
        session=self.session(); fn=AsyncMock(return_value={'ok':True,'sent_count':2})
        for call in ('a','b','b'):
            r=await guarded_voice_tool(session,'send_journal_history',{'limit':5},call,fn)
            self.assertEqual(r['sent_count'],2)
        fn.assert_awaited_once()
        session._voice_turn_count=2
        await guarded_voice_tool(session,'send_journal_history',{'limit':5},'c',fn)
        self.assertEqual(fn.await_count,2)

    async def test_duplicate_write_call_id_is_at_most_once_on_normal_turn(self):
        session=self.session(); session._recovery_active=False
        fn=AsyncMock(return_value={'ok':True,'saved':True})
        await guarded_voice_tool(session,'open_trade',{},'a',fn)
        await guarded_voice_tool(session,'open_trade',{},'a',fn)
        fn.assert_awaited_once()

    async def test_timeout_private_delivery_is_not_blindly_repeated(self):
        session=self.session(); fn=AsyncMock(side_effect=RuntimeError('transport uncertainty'))
        with self.assertRaises(RuntimeError): await guarded_voice_tool(session,'send_trade_photos',{},'a',fn)
        r=await guarded_voice_tool(session,'send_trade_photos',{},'b',fn)
        self.assertFalse(r['ok']); self.assertIn('uncertain',r['error']); fn.assert_awaited_once()

    async def test_access_is_rechecked_even_for_cached_reply(self):
        session=self.session(); session.authorize_tool=AsyncMock(side_effect=[None,'Access revoked'])
        fn=AsyncMock(return_value={'ok':True,'journals':['private']})
        await guarded_voice_tool(session,'get_journal_history',{},'a',fn)
        r=await guarded_voice_tool(session,'get_journal_history',{},'a',fn)
        self.assertEqual(r,{'ok':False,'error':'Access revoked'}); fn.assert_awaited_once()

    async def test_retry_exposes_only_approved_tools_preserving_budget(self):
        session=NS(closed=False,websocket=object(),_voice_turn_count=1,member=NS(send=AsyncMock()),send_event=AsyncMock(),last_error=None,
                   recovery_tools=[dict(name=n,type='function',strict=True) for n in ('get_journal_history','send_journal_history','open_trade','unknown')],
                   _last_response_options={'max_output_tokens':2200})
        recovery=VoiceRateLimitRecovery(session)
        with patch('gbop_voice_web.voice_runtime.asyncio.sleep',new_callable=AsyncMock):
            recovery.failed({'code':'rate_limit_exceeded'}); await recovery.task
        result=session.send_event.await_args.args[0]['response']
        self.assertEqual(result['tool_choice'],'auto')
        self.assertEqual({t['name'] for t in result['tools']},{'get_journal_history','send_journal_history'})
        self.assertTrue(all('strict' not in t for t in result['tools']))
        self.assertEqual(result['max_output_tokens'],2200)
        self.assertTrue(session._recovery_active)

    async def test_equivalent_delivery_defaults_do_not_duplicate(self):
        session=self.session(); fn=AsyncMock(return_value={'ok':True,'sent_count':1})
        await guarded_voice_tool(session,'send_journal_history',{},'a',fn)
        await guarded_voice_tool(session,'send_journal_history',{'limit':None,'offset':None},'b',fn)
        await guarded_voice_tool(session,'send_journal_history',{'limit':5,'offset':0,'user_id':999},'c',fn)
        fn.assert_awaited_once()

    async def test_post_read_followup_stays_recovery_restricted(self):
        async def events():
            yield json.dumps({'type':'response.done','response':{'status':'completed'}})
        session=NS(websocket=events(),member='test',_voice_turn_count=1,_recovery_active=True,rate_limit_recovery=Mock(),
                   tool_output_pending=True,_tool_response_options={},recovery_tools=[{'name':'get_journal_history','type':'function'}],send_event=AsyncMock())
        await method('receiver_loop',dict(json=json))(session)
        options=session.send_event.await_args.args[0]['response']
        self.assertEqual(options['tool_choice'],'auto')
        self.assertEqual([t['name'] for t in options['tools']],['get_journal_history'])


if __name__=='__main__': unittest.main()
