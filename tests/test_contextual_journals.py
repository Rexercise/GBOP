"""Synthetic member/session and SQLite-only journal regression tests. No live writes."""
import ast
from contextlib import contextmanager
from copy import deepcopy
import json
import math
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_coach as coach
from gbop_voice_web import journal_recall as recall
from gbop_voice_web.journal_context import JournalBinding, review_snapshot, merge_metadata, journal_transaction
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.trade_numbers import trade_number, trade_record_id
from gbop_voice_web.journal_numbers import journal_number

ROOT = Path(__file__).resolve().parents[1]
NY = lambda clock: '2026-10-02T' + clock + ':00-04:00'


def market_result(args, candles=1):
    facts = [{'bar_open_ny': NY('10:00' if n == 0 else '10:20'),
              'bar_close_ny': NY('10:05' if n == 0 else '10:25'), 'timeframe': 'M5',
              'purge_type': 'body_soup', 'direction': 'bearish', 'purged_side': 'buy',
              'open': 100, 'high': 110, 'low': 95, 'close': 105,
              'csd': {'status': 'confirmed', 'reference_level': 95, 'reference_boundary': 'full_candle_low',
                      'evidence': {'bar_open_ny': NY('11:00'), 'bar_close_ny': NY('11:05')}},
              'model1_crt_invalidating_close': {'bar_open_ny': NY('10:15'), 'bar_close_ny': NY('10:20')},
              'super_soup_structure': {'structure_status': 'observed', 'structural_quality': 'clean',
                  'local_crt_outcome': 'midpoint_delivered_then_invalidated',
                  'local_crt_invalidated_at_ny': NY('10:20'),
                  'local_function_objectives': {'opposing_liquidity': {'level': 95,
                    'source_interval': {'bar_open_ny': NY('10:59'), 'bar_close_ny': NY('11:00')},
                    'relative_to_model1_invalidation': 'after_model1_invalidation'}}}}
             for n in range(candles)]
    return {'ok': True, 'asset': args['asset'], 'review': {
        'anchor': {'start_ny': args['anchor_start_ny']}, 'anchor_timeframe': 'H1',
        'observed_direction': 'bearish', 'status': 'opposing_liquidity_observed',
        'candle_lifecycle': {'purge_candles': facts},
        'observation_coverage': {'complete': True}}}


class ContextualJournalTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE members(guild_id INTEGER,user_id INTEGER,username TEXT,display_name TEXT,
            activated INTEGER,leadership_ack INTEGER,revoked INTEGER,updated_at TEXT);
        INSERT INTO members VALUES(10,20,'member','Member',1,1,0,'auth-v1'),(10,30,'other','Other',1,1,0,'auth-v1');
        CREATE TABLE theses(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,asset TEXT,direction TEXT,
            play TEXT,session TEXT,crt_variant TEXT,htf_context TEXT,liquidity_purged TEXT,objective TEXT,
            thesis_invalidation TEXT,status TEXT,max_r REAL,created_at TEXT,final_result_r REAL,close_note TEXT,closed_at TEXT);
        CREATE TABLE thesis_executions(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,
            entry_model TEXT,tier INTEGER,risk_r REAL,entry_invalidation TEXT,note TEXT,created_at TEXT);
        CREATE TABLE thesis_events(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,
            event TEXT,details TEXT,result_r REAL,created_at TEXT);
        CREATE TABLE journals(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,description TEXT,
            rule_adherence TEXT,result_r REAL,study_note TEXT,created_at TEXT,thesis_id INTEGER);
        CREATE TABLE risk_flags(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,
            rule_code TEXT,message TEXT,created_at TEXT);
        ''')
        self.before_execute = None
        parent = self
        class Adapter:
            def execute(_, sql, params=()):
                if parent.before_execute:
                    parent.before_execute(sql, params)
                if any(token in sql for token in ('pg_advisory_xact_lock', 'ENABLE ROW LEVEL SECURITY', 'REVOKE ALL')):
                    return parent.conn.execute('SELECT 1')
                return parent.conn.execute(sql, params)
        @contextmanager
        def db():
            with self.conn:
                yield Adapter()
        self.db = db
        coach.init_coach(db)
        self.context = MarketConversation((10,20,'test-session'), auth_provider=(db,10,20))
        self.review()

    def tearDown(self):
        self.conn.close()

    def review(self, *, context=None, candles=1, exact=NY('10:00')):
        context = context or self.context
        context.begin_turn()
        result = context.run('review_market_crt', dict(asset='NAS100', date_ny='2026-10-02', shift='day',
            anchor_start_ny=NY('09:00'), anchor_timeframe='H1', through_ny=NY('12:00'),
            detail_candle_start_ny=exact, context_action='switch'),
            lambda name,args: market_result(args,candles))
        self.assertTrue(result['ok'], result)
        return result

    def save(self, text='That was my trade; I entered this candle and got stopped out.', **args):
        self.context.begin_turn(text)
        values = dict(description='Member reported a stopped trade.', result_r=None,
                      metadata_json=json.dumps({'kind':'trade','reported_outcome':'stopped_out'}))
        values.update(args)
        return self.context.run('save_journal_entry', values,
            lambda name,values: coach.coach_tool(self.db,10,20,name,values))

    def metadata(self):
        return json.loads(self.conn.execute('SELECT metadata FROM journal_details ORDER BY journal_id DESC LIMIT 1').fetchone()[0])

    def handlers(self, path):
        names = (['ai_choose_open_trade','ai_open_trade','ai_close_trade','ai_add_entry','ai_record_trade_event','ai_edit_journal','ensure_member_record']
                 if path=='bot.py' else ['choose_open_trade','tool_open_trade','tool_close_trade','tool_add_entry','tool_record_trade_event'])
        env = dict(db=self.db, GTOP_GUILD_ID=10, trade_number=trade_number, trade_record_id=trade_record_id,
            journal_number=journal_number, math=math, json=json, discord=NS(Member=object),
            trade_number_for_id=lambda user,id:trade_number(self.db,10,user,id),
            trade_id_from_number=lambda user,n:trade_record_id(self.db,10,user,n),
            thesis_used_r=lambda _:0.25, tier_used_r=lambda *a:0.25, now=lambda:'2026-10-03T19:00:00+00:00',
            now_iso=lambda:'2026-10-03T19:00:00+00:00', get_profile=lambda *a:{},
            ai_infer_tier=lambda *a:1,infer_tier=lambda *a:1,member_tier_limit=lambda *a:1,tier_limit=lambda *a:1,
            table_columns=lambda table:[r['name'] for r in self.conn.execute('PRAGMA table_info('+table+')')])
        nodes = [n for n in ast.parse((ROOT/path).read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),path,'exec'),env)
        return env

    def test_selected_scope_and_candle_are_durable_without_invented_result(self):
        result = self.save()
        self.assertTrue(result['saved'],result)
        self.assertIsNone(result['result_r'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0],0)
        meta = self.metadata()
        self.assertEqual((meta['asset'],meta['trade_date'],meta['session']),('NAS100','2026-10-02','day'))
        self.assertNotIn('reported_entry_at',meta)
        candle=meta['market_review']['selected_candle']
        self.assertEqual(candle['bar_open_ny'],NY('10:00'))
        self.assertEqual(candle['csd']['reference_level'],95)
        self.assertEqual(candle['super_soup_structure']['local_crt_invalidated_at_ny'],NY('10:20'))
        self.assertEqual(candle['super_soup_structure']['local_function_objectives']['opposing_liquidity']['source_interval']['bar_open_ny'],NY('10:59'))
        self.context.close()
        row=recall.history(self.db,10,20,{})['journals'][0]
        self.assertEqual(row['metadata']['market_review']['evidence_id'],meta['market_review']['evidence_id'])
        self.assertEqual(recall.history(self.db,10,30,{})['journals'],[])

    def test_reported_entry_and_exit_are_separate_from_candle_and_logging(self):
        result=self.save(metadata_json=json.dumps({'kind':'trade','reported_entry_at':NY('10:03'),
            'reported_exit_at':NY('10:21'),'reported_outcome':'stopped_out'}))
        self.assertTrue(result['ok'],result)
        row=self.conn.execute('SELECT * FROM journals').fetchone();meta=self.metadata()
        self.assertNotEqual(row['created_at'],meta['reported_entry_at'])
        self.assertEqual(meta['market_review']['selected_candle']['bar_open_ny'],NY('10:00'))
        self.assertEqual(meta['reported_entry_at'],NY('10:03'))
        self.assertEqual(meta['reported_exit_at'],NY('10:21'))
        self.assertIsNone(row['result_r'])

    def test_no_review_cannot_bind_or_fake_private_binding(self):
        self.context=MarketConversation((10,20,'fresh'),auth_provider=(self.db,10,20))
        result=self.save(_journal_binding={'review':{'asset':'NAS100'}})
        self.assertEqual(result['status'],'journal_review_required')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_metadata_cannot_forge_server_review(self):
        result=self.save(metadata_json='{"market_review":{"scope_id":"fake"}}')
        self.assertFalse(result['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_missing_and_ambiguous_candle_require_only_identity(self):
        for n in (0,2):
            self.review(candles=n,exact=None)
            result=self.save()
            self.assertEqual(result['status'],'journal_candle_required',result)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_unretrieved_scope_switch_does_not_attach_old_review(self):
        self.context.begin_turn('Now BTC night shift')
        result=self.save()
        self.assertEqual(result['status'],'journal_review_required')

    def test_unrelated_standalone_keeps_unknown_trade_date(self):
        result=self.save(text='Save a standalone unrelated journal.',market_reference='none',metadata_json='{"kind":"trade"}')
        self.assertTrue(result['ok'],result)
        self.assertNotIn('market_review',self.metadata())
        self.assertIsNone(coach.journal_rows(self.db,10,20)[0]['metadata']['trade_date'])

    def test_member_corrections_preserve_source_and_provenance(self):
        self.save(result_r=2)
        original=self.metadata()['market_review']
        changed=self.save(text='Correct journal one: result was unknown and entry was BTC.',journal_number=1,
            metadata_json='{"asset":"BTCUSD","reported_outcome":"unknown"}',clear_result=True)
        self.assertTrue(changed['updated'],changed)
        meta=self.metadata()
        self.assertEqual(meta['market_review'],original)
        self.assertEqual(meta['asset'],'BTCUSD')
        self.assertEqual(meta['provenance']['association_status'],'member_corrected_scope')
        self.assertEqual(meta['provenance']['corrections'][-1]['fields']['result_r'],{'before':2.0,'after':None})
        self.assertIsNone(changed['result_r'])

    def test_same_browser_turn_and_same_action_retry_is_idempotent(self):
        text='That was my trade; journal it.'
        args={'description':'Reported trade','metadata_json':'{"kind":"trade"}'}
        first=self.context.begin_turn(text,client_turn=7)
        result=self.context.run('save_journal_entry',args,lambda n,a:coach.coach_tool(self.db,10,20,n,a),generation=first)
        retry=self.context.begin_turn(text,client_turn=7)
        again=self.context.run('save_journal_entry',args,lambda n,a:coach.coach_tool(self.db,10,20,n,a),generation=retry)
        self.assertEqual(first,retry)
        self.assertEqual(result,again)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)

    def test_uncertain_postcommit_result_is_not_blindly_retried(self):
        self.context.begin_turn('That was my trade, save it.')
        args={'description':'Member trade'}
        def uncertain(n,a):
            coach.coach_tool(self.db,10,20,n,a)
            raise RuntimeError('transport lost after commit')
        with self.assertRaises(RuntimeError):
            self.context.run('save_journal_entry',args,uncertain)
        result=self.context.run('save_journal_entry',args,uncertain)
        self.assertEqual(result['status'],'journal_outcome_uncertain')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)

    def test_final_transaction_fence_rejects_cancellation_and_revocation_after_lock_wait(self):
        for action in ('cancel','revoke'):
            with self.subTest(action=action):
                self.conn.execute("UPDATE members SET revoked=0,updated_at='auth-v1' WHERE user_id=20")
                self.review()
                fired=[]
                def hook(sql,params):
                    if 'pg_advisory_xact_lock(?)' in sql and not fired:
                        fired.append(True)
                        if action=='cancel': self.context.invalidate()
                        else:self.conn.execute('UPDATE members SET revoked=1 WHERE user_id=20')
                self.before_execute=hook
                result=self.save()
                self.assertFalse(result['ok'],result)
                self.before_execute=None
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_cross_member_binding_fails_at_final_guard(self):
        args={'description':'Do not leak','_journal_binding':JournalBinding(self.context,self.context.generation,self.context._journal_review)}
        result=coach.coach_tool(self.db,10,30,'save_journal_entry',args)
        self.assertFalse(result['ok'],result)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_auth_rotation_clears_review_but_regular_text_refresh_does_not(self):
        env=self.handlers('bot.py')
        member=NS(id=20,display_name='New display')
        first=self.context._journal_review['evidence_id']
        env['ensure_member_record'](member)
        env['ensure_member_record'](member)
        self.assertEqual(self.conn.execute('SELECT updated_at FROM members WHERE user_id=20').fetchone()[0],'auth-v1')
        self.assertTrue(self.save()['ok'])
        self.assertEqual(self.metadata()['market_review']['evidence_id'],first)
        self.conn.execute("UPDATE members SET revoked=0,updated_at='revoke-then-restore-v2' WHERE user_id=20")
        result=self.save()
        self.assertIn(result.get('status'),('stale_market_context','journal_review_required'))
        self.assertIsNone(self.context._journal_review)

    def test_timestamps_validate_offset_and_order_without_inventing_r(self):
        for meta in ({'reported_entry_at':'2026-10-02T10:00:00'}, {'reported_entry_at':123},
                     {'reported_entry_at':NY('10:30'),'reported_exit_at':NY('10:20')}):
            result=self.save(metadata_json=json.dumps(meta))
            self.assertFalse(result['ok'],result)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_both_open_close_flows_preserve_session_metadata_and_link(self):
        for path,opening,closing in [('bot.py','ai_open_trade','ai_close_trade'),
                                     ('gbop_voice_web/server.py','tool_open_trade','tool_close_trade')]:
            with self.subTest(path=path):
                env=self.handlers(path)
                self.context.begin_turn('That is my trade; I entered this candle.')
                opened=self.context.run('open_trade',dict(asset='NAS100',direction='Bearish',play='9ate8',
                    entry_model='Super Soup',risk_r=.25,reported_entry_at=NY('10:03')),
                    lambda n,a:env[opening](20,a))
                self.assertTrue(opened['ok'],opened)
                trade_id=trade_record_id(self.db,10,20,opened['trade_id'])
                self.assertEqual(self.conn.execute('SELECT session FROM theses WHERE id=?',(trade_id,)).fetchone()[0],'day')
                self.context.begin_turn('Close my open trade; stopped out at 10:21.')
                closed=self.context.run('close_trade',dict(trade_id=opened['trade_id'],final_result_r=None,
                    summary='Reported stopout',rule_adherence='',study_note='',reported_exit_at=NY('10:21'),reported_outcome='stopped_out'),
                    lambda n,a:env[closing](20,a))
                self.assertTrue(closed['ok'],closed)
                journal=self.conn.execute('SELECT * FROM journals WHERE id=?',(closed['journal_id'],)).fetchone()
                self.assertEqual(journal['thesis_id'],trade_id)
                meta=json.loads(self.conn.execute('SELECT metadata FROM journal_details WHERE journal_id=?',(journal['id'],)).fetchone()[0])
                self.assertEqual(meta['reported_entry_at'],NY('10:03'))
                self.assertEqual(meta['reported_exit_at'],NY('10:21'))
                self.assertIsNone(journal['result_r'])
                corrected=coach.coach_tool(self.db,10,20,'save_journal_entry',dict(journal_number=closed['journal_number'],result_r=-.25))
                self.assertTrue(corrected['ok'],corrected)
                self.assertEqual(self.conn.execute('SELECT final_result_r FROM theses WHERE id=?',(trade_id,)).fetchone()[0],-.25)
                if path=='bot.py':
                    changed=env['ai_edit_journal'](20,dict(journal_id=journal['id'],result_r=.5,summary='Corrected result'))
                    self.assertTrue(changed['ok'],changed)
                    self.assertEqual(self.conn.execute('SELECT final_result_r FROM theses WHERE id=?',(trade_id,)).fetchone()[0],.5)
                    self.assertIn('result_r',self.metadata()['provenance']['corrections'][-1]['fields'])

    def test_close_does_not_attach_unrelated_existing_trade(self):
        env=self.handlers('gbop_voice_web/server.py')
        opened=env['tool_open_trade'](20,dict(asset='NAS100',direction='Bearish',play='9ate8',entry_model='Super Soup',risk_r=.25))
        self.context.begin_turn('That was my trade; close this trade.')
        with self.assertRaisesRegex(ValueError,'not linked'):
            self.context.run('close_trade',dict(trade_id=opened['trade_id'],summary='Unrelated',rule_adherence='',study_note=''),
                lambda n,a:env['tool_close_trade'](20,a))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT description FROM journals').fetchone()[0],'NAS100 Bearish · 9ate8')
        self.assertEqual(self.conn.execute('SELECT status FROM theses').fetchone()[0],'OPEN')

    def test_existing_journal_correction_never_acquires_current_review(self):
        result=self.save(text='Save a standalone journal.',market_reference='none',metadata_json='{"kind":"trade","asset":"XAUUSD"}')
        selected=deepcopy(self.context.selected)
        corrected=self.save(text='Correct this journal date to 2026-10-01 and asset to BTC.',
            journal_number=result['journal_number'],market_reference='none',metadata_json='{"trade_date":"2026-10-01","asset":"BTCUSD"}')
        self.assertTrue(corrected['ok'],corrected)
        self.assertNotIn('market_review',self.metadata())
        self.assertEqual(self.metadata()['asset'],'BTCUSD')
        self.assertEqual(self.context.selected,selected)
        self.assertEqual(self.context.requested,selected)
        self.assertIsNone(self.context.required_evidence_request())

    def test_new_context_attached_records_reject_conflicting_asset_but_accept_alias(self):
        result=self.save(metadata_json='{"kind":"trade","asset":"XAUUSD"}')
        self.assertFalse(result['ok'],result)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)
        for path,opening in [('bot.py','ai_open_trade'),('gbop_voice_web/server.py','tool_open_trade')]:
            env=self.handlers(path)
            self.context.begin_turn('That is my trade; I entered this candle.')
            with self.assertRaisesRegex(ValueError,'asset does not match'):
                self.context.run('open_trade',dict(asset='XAUUSD',direction='Bullish',play='9ate8',entry_model='Super Soup',risk_r=.25),
                    lambda n,a:env[opening](20,a))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0],0)
        self.assertTrue(self.save(metadata_json='{"kind":"trade","asset":"NAS"}')['ok'])
        self.assertEqual(self.metadata()['asset'],'NAS100')

    def test_retained_nas_snapshot_preserves_own_invalidity_vs_later_function(self):
        from test_directional_candidate_evidence import DirectionalCandidateEvidenceTests as Replay
        Replay.setUpClass()
        snapshot=review_snapshot({'review':Replay.review}, {'asset':'NAS100'},
            {'scope_id':'synthetic-scope','evidence_id':'synthetic-evidence','source_tool':'review_market_crt'},
            {'detail_candle_start_ny':NY('10:00')},'synthetic-conversation',1)
        candle=snapshot['selected_candle']
        self.assertEqual(candle['model1_crt_invalidating_close']['bar_close_ny'],NY('10:20'))
        own=candle['super_soup_structure']['local_function_objectives']['opposing_liquidity']
        self.assertEqual(own['evidence']['bar_open_ny'],NY('10:59'))
        self.assertEqual(own['relative_to_model1_invalidation'],'after_model1_invalidation')
        self.assertLess(len(json.dumps(snapshot)),16000)
        self.assertNotIn('result_r',snapshot)

    def test_edit_uses_stable_id_when_target_is_deleted_before_save(self):
        first=self.save(text='Save a standalone journal.',market_reference='none',description='First')
        second=self.save(text='Save another standalone journal.',market_reference='none',description='Second')
        raw_id=self.conn.execute("SELECT id FROM journals WHERE description='First'").fetchone()[0]
        original=coach.save_entry
        def delete_before_save(db,guild,user,args):
            self.conn.execute('DELETE FROM journal_details WHERE journal_id=?',(raw_id,))
            self.conn.execute('DELETE FROM journals WHERE id=?',(raw_id,))
            return original(db,guild,user,args)
        with patch.object(coach,'save_entry',side_effect=delete_before_save):
            result=self.handlers('bot.py')['ai_edit_journal'](20,dict(journal_id=raw_id,summary='Must not recreate or rebind'))
        self.assertFalse(result['ok'],result)
        rows=self.conn.execute('SELECT description FROM journals').fetchall()
        self.assertEqual([r[0] for r in rows],['Second'])

    def test_commit_finishes_before_context_guard_unlocks(self):
        observations=[]
        @contextmanager
        def db_probe():
            with self.db() as conn:
                yield conn
                def probe():
                    obtained=self.context._lock.acquire(blocking=False)
                    observations.append(obtained)
                    if obtained:self.context._lock.release()
                thread=threading.Thread(target=probe)
                thread.start();thread.join(timeout=1)
        args={'_journal_binding':JournalBinding(self.context,self.context.generation)}
        with journal_transaction(db_probe,args,10,20) as conn:
            conn.execute('SELECT 1')
        self.assertEqual(observations,[False])
        self.assertTrue(self.context._lock.acquire(blocking=False))
        self.context._lock.release()

    def test_trade_changed_while_waiting_for_close_lock_is_not_closed_twice(self):
        for path,opening,closing in [('bot.py','ai_open_trade','ai_close_trade'),
                                     ('gbop_voice_web/server.py','tool_open_trade','tool_close_trade')]:
            env=self.handlers(path)
            opened=env[opening](20,dict(asset='NAS100',direction='Bearish',play='9ate8',entry_model='Super Soup',risk_r=.25))
            raw=trade_record_id(self.db,10,20,opened['trade_id'])
            def raced(sql,params):
                if 'pg_advisory_xact_lock(?)' in sql:
                    self.conn.execute("UPDATE theses SET status='CLOSED' WHERE id=?",(raw,))
            self.before_execute=raced
            result=env[closing](20,dict(trade_id=opened['trade_id'],summary='close',rule_adherence='',study_note=''))
            self.before_execute=None
            self.assertFalse(result['ok'],result)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],2)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM journals WHERE description='close'").fetchone()[0],0)

    def test_same_scope_other_candle_cannot_rebind_open_trade(self):
        env=self.handlers('gbop_voice_web/server.py')
        self.context.begin_turn('That was my trade on this candle.')
        opened=self.context.run('open_trade',dict(asset='NAS100',direction='Bearish',play='9ate8',entry_model='Super Soup',risk_r=.25),
            lambda n,a:env['tool_open_trade'](20,a))
        self.review(candles=2,exact=NY('10:20'))
        self.context.begin_turn('That was my trade; close this trade.')
        with self.assertRaisesRegex(ValueError,'not linked'):
            self.context.run('close_trade',dict(trade_id=opened['trade_id'],summary='Other candle',rule_adherence='',study_note=''),
                lambda n,a:env['tool_close_trade'](20,a))
        self.assertEqual(self.conn.execute('SELECT status FROM theses').fetchone()[0],'OPEN')

    def test_audio_selected_candle_requires_verified_identity(self):
        self.review(candles=0,exact=None)
        self.context.begin_turn()
        result=self.context.run('save_journal_entry',dict(description='Reported candle entry',market_reference='selected_candle'),
            lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertEqual(result['status'],'journal_candle_required')

    def test_cancelled_session_binding_cannot_write(self):
        args={'description':'Old session','_journal_binding':JournalBinding(self.context,self.context.generation,self.context._journal_review)}
        self.context.close()
        replacement=MarketConversation((10,20,'replacement'),auth_provider=(self.db,10,20))
        self.review(context=replacement)
        result=coach.coach_tool(self.db,10,20,'save_journal_entry',args)
        self.assertFalse(result['ok'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0],0)

    def test_tool_schemas_expose_reference_and_reported_fields_without_private_capability(self):
        tools=contextual_tools([{'name':'open_trade','description':'Open','parameters':{'properties':{},'required':[]}},
                                next(t for t in coach.COACH_TOOLS if t['name']=='save_journal_entry')])
        self.assertIn('reported_entry_at',tools[0]['parameters']['properties'])
        self.assertIn('market_reference',tools[1]['parameters']['properties'])
        self.assertNotIn('_journal_binding',json.dumps(tools))

if __name__=='__main__':unittest.main()
