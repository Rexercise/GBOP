import contextlib
import copy
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch
from gbop_voice_web import candle_evidence as ev
from gbop_voice_web import market_data as market


def bars(start, minutes, step=60, high=110, low=90):
    return [dict(time=start+i*step, open=100, high=high, low=low, close=100)
            for i in range(minutes*60//step)]


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.start = ev.parse_time('2026-10-02T08:00:00')

    def test_high_formation_and_later_purge_are_distinct(self):
        data = bars(self.start, 180)
        data[17]['high'] = 112
        data[73]['high'] = 113
        r = ev.crt_review(data, self.start, self.start+10800, 'H1', 60)
        self.assertIn('08:17', r['anchor']['high_first_seen']['bar_open_ny'])
        purge = next(e for e in r['events'] if e['kind']=='buy_side_purge')
        self.assertIn('09:13', purge['bar_open_ny'])
        self.assertEqual(purge['precision_seconds'], 60)
        self.assertFalse(purge['exact_tick_time_known'])

    def test_invalidation_is_close_time_and_ignores_wicks(self):
        data = bars(self.start, 180)
        data[70]['high'] = 120
        data[179].update(high=121, close=120)
        r = ev.crt_review(data, self.start, self.start+10800, 'H1', 60)
        self.assertEqual(r['status'], 'invalidated_by_close')
        self.assertIn('11:00', r['invalidated_at_ny'])
        self.assertEqual(len([e for e in r['events'] if e['kind']=='range_invalidated']), 1)

    def test_target_same_bar_does_not_prove_order(self):
        data = bars(self.start, 120)
        data[70].update(high=115, low=90)
        r = ev.crt_review(data, self.start, self.start+7200, 'H1', 60)
        target = next(e for e in r['events'] if e['kind']=='opposing_liquidity_observed')
        self.assertFalse(target['order_after_purge_known'])

    def test_two_sided_same_bar_has_no_direction(self):
        data = bars(self.start, 120)
        data[70].update(high=115, low=85)
        r = ev.crt_review(data, self.start, self.start+7200, 'H1', 60)
        self.assertEqual(r['sweep_order'], 'unknown_within_same_bar')
        self.assertNotIn('observed_direction', r)

    def test_partial_anchor_does_not_become_valid_setup(self):
        data = bars(self.start, 120); del data[30]
        r = ev.crt_review(data, self.start, self.start+7200, 'H1', 60)
        self.assertEqual(r['status'], 'insufficient_closed_candles')
        self.assertFalse(r['anchor']['complete'])
        self.assertEqual(r['events'], [])

    def test_partial_observation_does_not_claim_no_setup(self):
        data = bars(self.start, 90)
        r = ev.crt_review(data, self.start, self.start+7200, 'H1', 60)
        self.assertEqual(r['status'], 'incomplete_observation_window')

    def test_m1_supports_intermediate_timeframes_m5_cannot(self):
        data = bars(self.start, 60)
        for tf in ('M2', 'M3', 'M4', 'M15'):
            self.assertTrue(ev.candle_query(data,self.start,self.start+3600,tf,60)['candles'][0]['complete'])
        self.assertEqual(ev.candle_query(data[::5], self.start, self.start+3600, 'M2',300)['status'], 'resolution_unavailable')

    def test_all_canonical_mappings(self):
        for tf, mapped in ev.ASSIGNED.items():
            start = ev.parse_time('2026-10-01T00:00:00')
            r = ev.crt_review([], start, ev.next_boundary(start, tf), tf,300)
            self.assertEqual(r['assigned_timeframe'], mapped)

    def test_custom_h4_anchor_and_15_minute_purge_candle(self):
        start = ev.parse_time('2026-10-02T01:00:00')
        data = bars(start, 360)
        data[267]['high'] = 115
        r = ev.crt_review(data,start,start+21600,'H4',60)
        self.assertEqual(r['assigned_timeframe'],'M15')
        candle = next(c for c in r['assigned_candles']['candles'] if c.get('high')==115)
        self.assertIn('05:15',candle['start_ny'])
        self.assertIn('05:27',next(e for e in r['events'] if e['kind']=='buy_side_purge')['bar_open_ny'])

    def test_dst_calendar_days_and_ambiguous_times(self):
        start=ev.parse_time('2026-11-01T00:00:00')
        self.assertEqual(ev.next_boundary(start,'D1')-start,25*3600)
        with self.assertRaises(ValueError): ev.parse_time('2026-11-01T01:30:00')
        with self.assertRaises(ValueError): ev.parse_time('2026-03-08T02:30:00')
        self.assertIsInstance(ev.parse_time('2026-11-01T01:30:00-04:00'),int)

    def test_extreme_ties_and_missing_history(self):
        data=bars(self.start,60)
        r=ev.summarize(data,self.start,self.start+3600,60)
        self.assertEqual(r['high_occurrences'],60)
        r=ev.summarize(data,self.start-86400,self.start,60)
        self.assertFalse(r['complete']); self.assertNotIn('low',r)

    def test_night_invalidation_crosses_date(self):
        start=ev.parse_time('2026-10-02T20:00:00')
        data=bars(start,240)
        data[-1].update(high=120,close=120)
        r=ev.crt_review(data,start,start+14400,'H1',60)
        self.assertTrue(r['invalidated_at_ny'].startswith('2026-10-03T00:00'))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:'); self.conn.row_factory=sqlite3.Row
        self.conn.execute(market.CREATE_SQL); self.conn.execute(market.HISTORY_SQL)
        @contextlib.contextmanager
        def db():
            with self.conn: yield self.conn
        self.db=db
        self.start=ev.parse_time('2026-10-02T08:00:00'); self.now=self.start+10800
        self.data=dict(captured_at=self.now,instruments=[dict(asset='NAS100',symbol='USTECm',bid=100,ask=101,tick_time=self.now,bars=bars(self.start,180,300),bars_m1=bars(self.start,180))])

    def tearDown(self): self.conn.close()

    def test_incremental_upload_preserves_earlier_history(self):
        market.ingest(self.db,self.data,self.now)
        self.data['captured_at']+=60
        self.data['instruments'][0]['bars']=[]
        self.data['instruments'][0]['bars_m1']=bars(self.now,1)
        market.ingest(self.db,self.data,self.now+60)
        feed=market.read_feed(self.db,'NAS',self.now+60)
        data,step=market.history_bars(self.db,feed,self.start,self.now+60)
        self.assertEqual(step,60); self.assertEqual(len(data),181)

    def test_partial_m1_does_not_hide_legacy_m5_history(self):
        self.data['instruments'][0]['bars_m1']=bars(self.start+7200,60)
        market.ingest(self.db,self.data,self.now)
        feed=market.read_feed(self.db,'NAS',self.now)
        _,step=market.history_bars(self.db,feed,self.start,self.now)
        self.assertEqual(step,300)

    def test_structural_tool_returns_evidence_without_quote(self):
        self.data['instruments'][0]['bars_m1'][75]['high']=115
        market.ingest(self.db,self.data,self.now)
        with patch.object(market.time,'time',return_value=self.now):
            result=market.market_tool(self.db,'review_market_session',dict(asset='NAS',date_ny='2026-10-02',shift='day'))
        self.assertNotIn('bid',result); self.assertNotIn('bars',result)
        self.assertIn('evidence',result['review']['observations'][0])

    def test_symbol_change_never_mixes_old_broker_prices(self):
        market.ingest(self.db,self.data,self.now)
        self.data['captured_at']+=60
        item=self.data['instruments'][0]; item['symbol']='USTECnew'; item['bars']=[]; item['bars_m1']=bars(self.now,1)
        market.ingest(self.db,self.data,self.now+60)
        feed=market.read_feed(self.db,'NAS',self.now+60)
        data,_=market.history_bars(self.db,feed,self.start,self.now+60)
        self.assertEqual(len(data),1)

    def test_m1_validation_is_atomic_and_closed_only(self):
        self.data['instruments'][0]['bars_m1'].append(bars(self.now,1)[0])
        with self.assertRaises(ValueError): market.ingest(self.db,self.data,self.now)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM gbop_market_feed').fetchone()[0],0)

if __name__ == '__main__': unittest.main()
