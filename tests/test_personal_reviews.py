"""Read-only private review evidence. Synthetic data only, no production sends."""
import ast
import asyncio
import contextlib
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace as NS, ModuleType
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from gbop_voice_web import snapshots as snap
from tests.test_snapshots import SnapshotTests

NY = ZoneInfo('America/New_York')
SOURCE = Path(__file__).resolve().parents[1] / 'bot.py'


class PersonalReviewTests(SnapshotTests):
    def setUp(self):
        super().setUp()
        self.conn.executescript('''
        CREATE TABLE journal_details(journal_id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,metadata TEXT);
        CREATE TABLE thesis_events(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,event TEXT,details TEXT);
        CREATE TABLE gbop_shift_plans(guild_id INTEGER,user_id INTEGER,session_date TEXT,shift TEXT,plan TEXT);
        ''')

    def metadata(self, journal, **values):
        self.conn.execute('INSERT OR REPLACE INTO journal_details VALUES(?,10,20,?)', (journal, json.dumps(values)))

    def review(self, period=None, user=20):
        return snap.collect_snapshot(self.db, 10, user, period or snap.daily_period(date(2026,10,1), NY))

    def test_missing_data_is_unknown_not_zero_or_nonadherence(self):
        stats = self.review(user=30)
        self.assertIsNone(stats['performance']['net_r'])
        self.assertIsNone(stats['execution']['risk_r'])
        self.assertIsNone(stats['process']['full_adherence_rate'])
        self.assertEqual(stats['process']['violated'], 0)
        self.assertIn('missing records do not establish', '\n'.join(stats['patterns']))
        self.assertNotIn('Process is carrying', stats['recommendation'])
        self.assertNotIn('9AM', stats['recommendation'])
        self.assertIn('unverified', stats['market_context']['message'])

    def test_unknown_result_and_risk_never_render_zero_r(self):
        self.conn.execute('UPDATE theses SET final_result_r=NULL')
        self.conn.execute('UPDATE journals SET result_r=NULL,rule_adherence=NULL')
        self.conn.execute('UPDATE thesis_executions SET risk_r=NULL')
        self.conn.execute('DELETE FROM risk_flags')
        stats = self.review()
        self.assertEqual(stats['performance']['unscored_trades'], 2)
        self.assertIsNone(stats['by_play'][0]['net_r'])
        self.assertNotIn('+0.00R', snap.format_trade_breakdown(stats['by_play']))
        self.assertNotIn('+0.00R', snap.format_execution_breakdown(stats['execution']['by_model']))
        self.assertEqual(stats['execution']['unknown_risk_count'], 3)
        self.assertEqual(stats['process']['unknown'], 2)

    def test_late_journal_uses_reported_date_not_log_date(self):
        self.metadata(1, kind='trade', trade_date='2026-09-30', session='day')
        stats = self.review()
        self.assertEqual(stats['performance']['closed_trades'], 1)
        historical = self.review(snap.daily_period(date(2026,9,30), NY))
        self.assertEqual(historical['performance']['net_r'], 3)
        self.assertEqual(historical['coverage']['date_bases'], {'reported_date':1})
        self.assertEqual(historical['execution']['count'], 0)  # execution log stays a log

    def test_reported_timezone_timestamp_overrides_late_log(self):
        self.metadata(1, kind='trade', reported_exit_at='2026-10-01T08:30:00-05:00')
        stats = self.review(snap.shift_period(date(2026,10,1), 'day', NY))
        self.assertEqual(stats['performance']['net_r'], 3)
        self.assertEqual(stats['coverage']['date_bases'], {'reported_time':1})
        self.metadata(1, kind='trade', reported_exit_at='2026-10-01T12:00:00-04:00')
        self.assertEqual(self.review(snap.shift_period(date(2026,10,1), 'day', NY))['performance']['closed_trades'], 0)

    def test_undated_import_and_study_cannot_inflate_performance(self):
        self.conn.execute("UPDATE theses SET status='JOURNALED' WHERE id=1")
        self.metadata(1, kind='trade', emotion='calm')
        self.metadata(2, kind='study', trade_date='2026-10-01')
        stats = self.review()
        self.assertEqual(stats['performance']['closed_trades'], 0)
        self.assertEqual(stats['coverage']['excluded']['undated'], 1)
        self.assertEqual(stats['coverage']['excluded']['study_or_reflection'], 1)
        self.assertEqual(stats['reflections']['reported_trades'], 0)
        self.assertEqual(stats['process']['risk_flags'], 0)

    def test_duplicate_legacy_conflicts_unknown_then_canonical_wins(self):
        self.conn.execute("INSERT INTO journals VALUES(3,10,20,'Violated',-2,'old','2026-10-01T16:00:00+00:00',1)")
        stats = self.review()
        self.assertEqual(stats['performance']['closed_trades'], 2)
        self.assertEqual(stats['performance']['net_r'], -1)
        self.assertEqual(stats['performance']['unscored_trades'], 1)
        self.assertEqual(stats['process']['unknown'], 1)
        self.conn.execute("INSERT INTO thesis_events VALUES(1,1,10,20,'journal_canonical_v1','{\"journal_id\":1}')")
        stats = self.review()
        self.assertEqual(stats['performance']['net_r'], 2)
        self.assertEqual(stats['process']['followed'], 1)

    def test_removed_canonical_never_promotes_legacy_history(self):
        self.conn.execute("INSERT INTO thesis_events VALUES(1,1,10,20,'journal_canonical_v1','{\"journal_id\":99}')")
        stats = self.review()
        self.assertEqual(stats['performance']['unscored_trades'], 1)
        self.assertEqual(stats['performance']['net_r'], -1)
        self.assertEqual(stats['process']['followed'], 0)

    def test_no_journal_reassignment_or_cross_guild_user_data(self):
        self.conn.execute("INSERT INTO journals VALUES(8,10,20,'Followed',900,'foreign','2026-10-01T16:00:00+00:00',88)")
        self.conn.execute("INSERT INTO theses VALUES(88,11,20,'FOREIGN','Bullish','9ate8','Day Shift','CLOSED',900,'2026-10-01T13:00:00+00:00','2026-10-01T14:00:00+00:00')")
        self.conn.execute("INSERT INTO journal_details VALUES(1,10,30,'{\"emotion\":\"private-other-user\"}')")
        stats = self.review()
        self.assertEqual(stats['performance']['net_r'], 2)
        self.assertNotIn('private-other-user', repr(stats))
        self.assertNotIn('FOREIGN', repr(stats))

    def test_no_reply_and_no_flags_do_not_prove_adherence(self):
        self.conn.execute('DELETE FROM risk_flags')
        self.conn.execute('UPDATE journals SET rule_adherence=NULL')
        self.conn.execute('UPDATE post_shift_checkins SET response=NULL')
        stats = self.review()
        self.assertIsNone(stats['process']['full_adherence_rate'])
        self.assertEqual(stats['process']['violated'], 0)
        self.assertIn('cannot establish process quality', stats['recommendation'])
        self.assertIn('Missing check-ins do not mean nonadherence', snap.format_coverage(stats))

    def test_adherence_negation_is_not_a_positive_signal(self):
        self.assertEqual(snap._adherence_bucket('did not follow my plan'), 'violated')
        self.assertEqual(snap._adherence_bucket('no violations'), 'unknown')
        self.assertEqual(snap._adherence_bucket('not followed'), 'violated')

    def test_date_only_shift_requires_explicit_member_session(self):
        self.metadata(1, kind='trade', trade_date='2026-10-01', session='night')
        day = self.review(snap.shift_period(date(2026,10,1), 'day', NY))
        night = self.review(snap.shift_period(date(2026,10,1), 'night', NY))
        self.assertEqual(day['performance']['closed_trades'], 0)
        self.assertEqual(night['performance']['closed_trades'], 2)
        self.assertEqual(night['process']['checkins_expected'], 1)

    def test_shift_and_dst_bounds(self):
        for day, hours in ((date(2026,3,8),23), (date(2026,11,1),25)):
            daily = snap.daily_period(day, NY)
            self.assertEqual((snap._timestamp(daily['end_utc'])-snap._timestamp(daily['start_utc'])).total_seconds(), hours*3600)
            for shift in ('day','night'):
                period = snap.shift_period(day, shift, NY)
                self.assertEqual((snap._timestamp(period['end_utc'])-snap._timestamp(period['start_utc'])).total_seconds(), 3*3600)
                self.assertEqual(snap._timestamp(period['start_utc']).astimezone(NY).hour, 9 if shift=='day' else 21)
        period = snap.shift_period(date(2026,10,1),'night',NY)
        self.assertEqual(period['end_utc'],'2026-10-02T04:00:00+00:00')

    def test_feeling_corrections_and_grades_are_optional_canonical_reports(self):
        feelings = ModuleType('gbop_voice_web.trade_feelings')
        feelings.effective_history = Mock(return_value=[{'stage':'close','feeling':'calm'}])
        grades = ModuleType('gbop_voice_web.trade_self_grades')
        grades.self_grade_summary = Mock(return_value=None)
        grades.self_grade_counts = Mock(return_value={'counts':{'type1':0,'type2':0,'type3':0,'type4':1}})
        with patch.dict(sys.modules, {'gbop_voice_web.trade_feelings':feelings, 'gbop_voice_web.trade_self_grades':grades}):
            stats = self.review()
        self.assertEqual(stats['reflections']['feelings'][0]['trades'], 2)
        self.assertEqual(stats['reflections']['off_plan_grades'], 1)
        self.assertEqual(len(grades.self_grade_counts.call_args.args[0]), 2)
        self.assertIn('Type 4: 1', snap.format_reflections(stats['reflections']))

    def test_saved_plan_is_member_scoped_reference_not_adherence(self):
        self.conn.execute("INSERT INTO gbop_shift_plans VALUES(10,20,'2026-10-01','day','Wait for my chosen setup')")
        self.conn.execute("INSERT INTO gbop_shift_plans VALUES(10,30,'2026-10-01','day','Private plan')")
        self.conn.execute('UPDATE journals SET rule_adherence=NULL')
        stats = self.review()
        self.assertEqual(len(stats['plans']), 1)
        self.assertIsNone(stats['process']['full_adherence_rate'])
        self.assertNotIn('Private plan', repr(stats))

    def test_real_optional_reflection_modules_and_weekly_trend(self):
        try:
            from gbop_voice_web.trade_self_grades import self_grade_summary
            from gbop_voice_web.trade_feelings import effective_history
        except ImportError:
            self.skipTest('Optional reflection modules are integration dependencies.')
        grade = dict(version=1, type='type4', member_reported=True, source='member_reported',
                     adherence='off_plan', outcome='profit', predefined_stop=None,
                     off_plan_reason='fomo', note=None)
        history = [dict(id=1,stage='close',feeling='rushed',correction_of=None,reported_at=None),
                   dict(id=2,stage='close',feeling='calm',correction_of=1,reported_at=None)]
        self.metadata(1, kind='trade',trade_date='2026-10-01',session='day',self_grade=grade,feeling_history=history)
        self.conn.execute('UPDATE journals SET rule_adherence=NULL WHERE id=1')
        self.conn.execute("UPDATE theses SET final_result_r=2,status='CLOSED',closed_at='2026-09-24T16:00:00+00:00' WHERE id=4")
        self.conn.execute("INSERT INTO journals VALUES(7,10,20,NULL,2,'prior','2026-09-24T16:00:00+00:00',4)")
        self.metadata(7,kind='trade',trade_date='2026-09-24',session='day',self_grade=grade)
        stats = self.review(snap.weekly_period(date(2026,10,2), NY))
        self.assertEqual(stats['reflections']['grade_counts'][4],1)
        self.assertEqual(stats['process']['violated'],1)
        self.assertEqual(stats['reflections']['feelings'][0]['feeling'],'calm')
        self.assertNotIn('rushed',snap.format_reflections(stats['reflections']))
        self.assertIn('1/1 this week, 1/1 prior week',snap.format_comparison(stats))
        # A later contradictory journal correction cannot silently keep Type 4.
        self.conn.execute('UPDATE journals SET result_r=-2 WHERE id=1')
        stats = self.review()
        self.assertEqual(stats['reflections']['graded_trades'],0)
        self.assertEqual(stats['process']['violated'],0)

    def test_collection_is_read_only(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        self.review()
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','GRANT','REVOKE')) for sql in statements), statements)

    def test_actual_market_candles_with_explicit_full_and_partial_coverage(self):
        self.metadata(1, kind='trade', trade_date='2026-10-01', session='day', asset='XAUUSD')
        self.metadata(2, kind='trade', trade_date='2026-10-01', session='night', asset='NAS100')
        self.conn.executescript('''CREATE TABLE gbop_market_feed(asset TEXT,captured_at INTEGER,received_at INTEGER,payload TEXT);
            CREATE TABLE gbop_market_history(asset TEXT,symbol TEXT,step INTEGER,day_utc INTEGER,payload TEXT);''')
        from gbop_voice_web.shift_availability import shift_bounds
        for asset, shift, count in (('XAUUSD','day',48),('NAS100','night',18)):
            start,end = shift_bounds('2026-10-01',shift)
            bars = [{'time':start-3600+i*300,'open':100+i,'high':102+i,'low':99+i,'close':101+i} for i in range(count)]
            payload = dict(symbol=asset,bid=100,ask=101,tick_time=end,bars=bars)
            self.conn.execute('INSERT INTO gbop_market_feed VALUES(?,?,?,?)',(asset,end,end,json.dumps(payload)))
        stats = self.review()
        windows = {w['asset']:w for w in stats['market_context']['windows']}
        self.assertEqual(windows['XAUUSD']['status'],'full')
        self.assertEqual(windows['XAUUSD']['coverage']['closed_bar_count'],36)
        self.assertEqual(windows['NAS100']['status'],'partial')
        self.assertEqual(windows['NAS100']['coverage']['closed_bar_count'],6)
        self.assertEqual(stats['performance']['net_r'],2)  # market move is not a fill
        text = snap.format_market_context(stats['market_context'])
        self.assertIn('36/36',text)
        self.assertIn('6/36',text)
        self.assertIn('does not establish',text)


class ScheduledReviewTests(unittest.IsolatedAsyncioTestCase):
    def functions(self, names, **values):
        nodes = [n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        env = dict(asyncio=asyncio, datetime=datetime,timedelta=timedelta,GBOP_EASTERN_TZ=NY,
                   daily_period=snap.daily_period,weekly_period=snap.weekly_period,shift_period=snap.shift_period,
                   GTOP_GUILD_ID=10,GTOP_OWNER_USER_ID=999,logger=Mock(),GBOP_CHECKIN_PROMPT='CHECKIN',GBOP_CHECKIN_REPLY_HINT=' REPLY HINT', **values)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),env)
        return env

    def delivery_db(self):
        conn=sqlite3.connect(':memory:')
        conn.row_factory=lambda c,r: dict(zip([d[0] for d in c.description],r))
        conn.executescript('''CREATE TABLE gbop_shift_deliveries(event_key TEXT,guild_id INTEGER,user_id INTEGER,discord_message_id TEXT,delivered_at TEXT,PRIMARY KEY(event_key,guild_id,user_id));
            CREATE TABLE gbop_shift_alerts(event_key TEXT PRIMARY KEY,sent_at TEXT);
            CREATE TABLE post_shift_checkins(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,shift_date TEXT,shift TEXT,prompt_sent_at TEXT);''')
        @contextlib.contextmanager
        def db():
            yield conn
        self.addCleanup(conn.close)
        return db,conn

    async def test_fresh_membership_consent_and_revocation_fail_closed(self):
        member=NS(id=20,bot=False)
        guild=NS(fetch_member=AsyncMock(return_value=member))
        env=self.functions({'_authorized_scheduled_member'}, client=NS(get_guild=Mock(return_value=guild)),
                           has_member_role=Mock(return_value=True),is_owner=Mock(return_value=False),
                           member_access_error=Mock(return_value=None),db=Mock())
        self.assertIs(await env['_authorized_scheduled_member'](20),member)
        env['member_access_error'].return_value='revoked'
        self.assertIsNone(await env['_authorized_scheduled_member'](20))
        env['member_access_error'].return_value=None
        env['has_member_role'].return_value=False
        self.assertIsNone(await env['_authorized_scheduled_member'](20))
        guild.fetch_member.side_effect=RuntimeError('unavailable')
        self.assertIsNone(await env['_authorized_scheduled_member'](20))

    async def test_formation_one_send_shared_delivery_key_and_period(self):
        db,conn=self.delivery_db()
        member=NS(id=20,send=AsyncMock(return_value=NS(id=55)))
        env=self.functions({'_broadcast_gbop_dm'},db=db,now=lambda:'2026-10-01T17:00:00+00:00',
             _gbop_role_members=lambda:[member],_authorized_scheduled_member=AsyncMock(return_value=member),
             collect_snapshot=Mock(return_value={'recorded':'stats'}),_snapshot_embed=Mock(return_value='review'),
             discord=NS(Forbidden=ValueError,HTTPException=OSError))
        call=env['_broadcast_gbop_dm']
        args=dict(event_key='2026-10-01:day_formation',checkin_shift='day',shift_date='2026-10-01')
        self.assertEqual(await call('formation and question',**args),(1,0))
        self.assertEqual(await call('formation and question',**args),(0,0))
        member.send.assert_awaited_once_with('formation and question',embed='review')
        period=env['collect_snapshot'].call_args.args[-1]
        self.assertEqual(period['start_utc'],'2026-10-01T13:00:00+00:00')
        self.assertEqual(period['end_utc'],'2026-10-01T16:00:00+00:00')
        self.assertEqual(conn.execute('SELECT count(*) c FROM post_shift_checkins').fetchone()['c'],1)

    async def test_access_lost_during_review_blocks_send_and_receipt(self):
        db,conn=self.delivery_db()
        member=NS(id=20,send=AsyncMock())
        for function in ('_broadcast_gbop_dm','_send_snapshot_event'):
            with self.subTest(function=function):
                auth=AsyncMock(side_effect=[member,None])
                env=self.functions({function},db=db,now=lambda:'now',_gbop_role_members=lambda:[member],
                    _authorized_scheduled_member=auth,collect_snapshot=Mock(return_value={}),_snapshot_embed=Mock(),
                    _snapshot_delivery_exists=Mock(return_value=False),_record_snapshot_delivery=Mock(),
                    discord=NS(Forbidden=ValueError,HTTPException=OSError))
                if function=='_broadcast_gbop_dm':
                    await env[function]('prompt',event_key='key',checkin_shift='night',shift_date='2026-10-01')
                else:
                    await env[function]('key',snap.daily_period(date(2026,10,1),NY))
                member.send.assert_not_awaited()
                env['_record_snapshot_delivery'].assert_not_called()
                self.assertEqual(conn.execute('SELECT count(*) c FROM gbop_shift_deliveries').fetchone()['c'],0)

    async def test_denial_before_collection_reads_no_private_review(self):
        db,_=self.delivery_db()
        member=NS(id=20,send=AsyncMock())
        env=self.functions({'_send_snapshot_event'},db=db,now=lambda:'now',_gbop_role_members=lambda:[member],
            _snapshot_delivery_exists=Mock(return_value=False),collect_snapshot=Mock(),
            _authorized_scheduled_member=AsyncMock(return_value=None),discord=NS(Forbidden=ValueError,HTTPException=OSError))
        await env['_send_snapshot_event']('key',{})
        env['collect_snapshot'].assert_not_called()
        member.send.assert_not_awaited()

    async def test_review_failure_keeps_single_explicit_formation_prompt(self):
        db,_=self.delivery_db()
        member=NS(id=20,send=AsyncMock(return_value=NS(id=55)))
        env=self.functions({'_broadcast_gbop_dm'},db=db,now=lambda:'now',_gbop_role_members=lambda:[member],
            _authorized_scheduled_member=AsyncMock(return_value=member),collect_snapshot=Mock(side_effect=RuntimeError('data unavailable')),
            discord=NS(Forbidden=ValueError,HTTPException=OSError))
        await env['_broadcast_gbop_dm']('formation',event_key='key',checkin_shift='day',shift_date='2026-10-01')
        member.send.assert_awaited_once()
        self.assertIn('review is unavailable',member.send.call_args.args[0])
        self.assertIsNone(member.send.call_args.kwargs['embed'])

    async def test_embed_limits_and_weekly_optional_fields(self):
        class Embed:
            def __init__(self,**kwargs):
                self.__dict__.update(kwargs); self.fields=[]; self.footer=''
            def add_field(self,**kwargs):
                self.fields.append(NS(**kwargs))
            def set_field_at(self,index,**kwargs):
                self.fields[index]=NS(**kwargs)
            def set_footer(self,text):
                self.footer=text
            def __len__(self):
                return len(self.title)+len(self.description)+len(self.footer)+sum(len(f.name)+len(f.value) for f in self.fields)
        fixture=PersonalReviewTests()
        fixture.setUp()
        try:
            fixture.metadata(1,kind='trade',trade_date='2026-10-01',session='day',emotion='calm '*100)
            fixture.conn.execute("INSERT INTO gbop_shift_plans VALUES(10,20,'2026-10-01','day',?)",('Long plan '*1000,))
            stats=fixture.review(snap.weekly_period(date(2026,10,2),NY))
        finally:
            fixture.tearDown()
        env=self.functions({'_snapshot_embed'},discord=NS(Embed=Embed,Color=NS(blurple=lambda:0)),
            snapshot_r=snap.format_r,snapshot_percent=snap.format_percent,snapshot_profit_factor=snap.format_profit_factor,
            format_reflections=snap.format_reflections,format_coverage=snap.format_coverage,
            format_no_trade_checkins=snap.format_no_trade_checkins,
            format_comparison=snap.format_comparison,format_market_context=snap.format_market_context,
            format_trade_breakdown=snap.format_trade_breakdown,format_execution_breakdown=snap.format_execution_breakdown)
        embed=env['_snapshot_embed'](stats)
        self.assertLessEqual(len(embed),5900)
        self.assertLessEqual(len(embed.fields),25)
        self.assertTrue(all(len(f.value)<=1024 for f in embed.fields))
        self.assertIn('Weekly Review',embed.title)
        self.assertTrue(any('Coverage' in f.name for f in embed.fields))

    async def test_snapshot_existing_receipt_skips_collection_and_send(self):
        db,_=self.delivery_db()
        member=NS(id=20,send=AsyncMock())
        env=self.functions({'_send_snapshot_event'},db=db,now=lambda:'now',_gbop_role_members=lambda:[member],
            _snapshot_delivery_exists=Mock(return_value=True),collect_snapshot=Mock(),
            _authorized_scheduled_member=AsyncMock(),discord=NS(Forbidden=ValueError,HTTPException=OSError))
        await env['_send_snapshot_event']('key',{})
        env['collect_snapshot'].assert_not_called()
        env['_authorized_scheduled_member'].assert_not_awaited()
        member.send.assert_not_awaited()

    async def test_existing_daily_weekly_cadence_and_night_date_unchanged(self):
        env=self.functions({'_scheduled_snapshot_events','_scheduled_shift_events'})
        schedule=env['_scheduled_snapshot_events']
        self.assertEqual(schedule(datetime(2026,10,3,0,14,tzinfo=NY)),[])
        daily=schedule(datetime(2026,10,3,0,15,tzinfo=NY))
        self.assertEqual([e[0] for e in daily],['2026-10-02:daily_snapshot'])
        weekly=schedule(datetime(2026,10,3,0,20,tzinfo=NY))
        self.assertEqual([e[0] for e in weekly],['2026-10-02:daily_snapshot','2026-10-02:weekly_snapshot'])
        self.assertEqual(weekly[1][1]['start_day'],date(2026,9,28))
        night=env['_scheduled_shift_events'](datetime(2026,11,1,0,0,tzinfo=NY))[0]
        self.assertEqual((night[0],night[2],night[3]),('2026-11-01:night_formation','night','2026-10-31'))
        self.assertEqual(schedule(datetime(2026,10,3,3,20,tzinfo=NY)),[])


if __name__=='__main__':
    unittest.main()
