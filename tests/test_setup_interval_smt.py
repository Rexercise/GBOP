"""Owner rule: qualification follows the setup interval, never minute races."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time, summarize
from gbop_voice_web.market_context import enrich_smt
from gbop_voice_web.smt_evidence import compare_ranges
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from test_smt_reference import fixture, START, END, review, bearish


class SetupIntervalRuleTests(unittest.TestCase):
    def test_same_hour_catchup_after_early_target_still_disqualifies_boneless(self):
        pair = fixture()
        pair[1]['bars'][21].update(high=201)  # 09:45, after 09:25 own low touch.
        result = review(pair)
        event = bearish(result)
        self.assertEqual(event['objective_status']['XAUUSD']['opposing_liquidity']['status'], 'objective_complete_while_range_valid')
        self.assertFalse(event['setup_interval']['qualified_smt'])
        self.assertIsNone(event['boneless_asset'])
        self.assertNotIn('boneless_reference', event['paired_model1'])
        self.assertNotIn('Observed bearish', result['spoken_summary'])
        # The distinct bullish setup in this synthetic sample is unaffected.
        self.assertTrue(any(e['direction']=='bullish' and e['setup_interval']['qualified_smt'] for e in result['events']))

    def test_later_distinct_hour_catchup_preserves_completed_qualifying_setup(self):
        pair = fixture()
        pair[1]['bars'][25].update(high=201)  # 10:05, a different H1.
        event = bearish(review(pair))
        self.assertTrue(event['setup_interval']['qualified_smt'])
        self.assertEqual(event['setup_interval']['end_ny'], '2026-10-02T10:00:00-04:00')
        self.assertEqual(event['boneless_asset'], 'XAUUSD')
        self.assertEqual(event['paired_model1']['status'], 'identified')
        ref = event['paired_model1']['boneless_reference']
        self.assertEqual(ref['bar_open_ny'], '2026-10-02T09:15:00-04:00')
        self.assertEqual(ref['smt_qualified_at_ny'], '2026-10-02T10:00:00-04:00')

    def test_selected_non_nine_hour_is_evaluated_on_its_own_h1(self):
        pair = fixture()
        offset = 2 * 3600
        for item in pair:
            for bar in item['bars']:
                bar['time'] += offset
        pair[1]['bars'][21].update(high=201)
        result = enrich_smt(compare_ranges(*pair, START+offset, START+offset+3600, END+offset))
        event = bearish(result)
        self.assertEqual(event['setup_interval']['start_ny'], '2026-10-02T11:00:00-04:00')
        self.assertFalse(event['setup_interval']['qualified_smt'])

    def test_custom_anchor_timeframe_uses_its_boundary_not_wallclock_hour(self):
        start = parse_time('2026-10-02T07:30:00-04:00')
        end = start + 8*3600
        left = [dict(time=t,open=95,high=100,low=90,close=95) for t in range(start,end,300)]
        right = [dict(time=t,open=195,high=200,low=190,close=195) for t in range(start,end,300)]
        left[50].update(high=102,close=101)  # 11:40
        right[72].update(high=202,close=201)  # 13:30; same custom H4 setup.
        pair = [dict(asset='XAGUSD',symbol='XAGUSD',bars=left,step=300),
                dict(asset='XAUUSD',symbol='XAUUSD',bars=right,step=300)]
        result = enrich_smt(compare_ranges(*pair,start,start+4*3600,end,'H4'))
        event = bearish(result)
        self.assertEqual(event['setup_interval']['start_ny'], '2026-10-02T11:30:00-04:00')
        self.assertEqual(event['setup_interval']['end_ny'], '2026-10-02T15:30:00-04:00')
        self.assertFalse(event['setup_interval']['qualified_smt'])


class RetainedNASNightSetupTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parent/'fixtures/market_replays/thursday_2026_10_01_indices_night.json'
        self.fixture = json.loads(path.read_text())
        self.conn = sqlite3.connect(':memory:'); self.conn.row_factory = sqlite3.Row
        self.conn.execute(market.HISTORY_SQL); self.addCleanup(self.conn.close)
        self.data = {}; self.symbols = {}
        for item in self.fixture['instruments']:
            asset,step = item['asset'],item['step']; self.symbols[asset]=item['symbol']
            bars = [dict(zip(self.fixture['provenance']['fields'], values)) for values in item['candles']]
            self.data[asset,step] = bars
            days={}
            for bar in bars: days.setdefault(bar['time']//86400*86400,[]).append(bar)
            for day, values in days.items():
                self.conn.execute('INSERT INTO gbop_market_history VALUES(?,?,?,?,?)',
                                  (asset,item['symbol'],step,day,json.dumps(values)))
        feed = patch.object(market,'read_feed',side_effect=lambda db,asset: dict(
            ok=True,asset=market.asset_name(asset),symbol=self.symbols[market.asset_name(asset)],
            status='historical_replay',is_live=False,bars=[],bars_m1=[]))
        feed.start(); self.addCleanup(feed.stop)

    @contextmanager
    def db(self): yield self.conn

    def tool(self):
        result = market.market_tool(self.db,'review_market_session',
                                   dict(asset='NAS100',date_ny='2026-10-01',shift='night'))
        self.assertTrue(result['ok'],result)
        return result

    def test_actual_fixture_date_integrity_and_both_native_timeframes(self):
        provenance = self.fixture['provenance']
        digest = hashlib.sha256(json.dumps(self.fixture['instruments'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.assertEqual(digest,provenance['instruments_sha256'])
        self.assertEqual(provenance['window_start_ny'],'2026-10-01T19:00:00-04:00')
        start=parse_time('2026-10-01T20:00:00-04:00')
        for step in (60,300):
            for asset,high,low,nine_close in [('NAS100',30693.58,30583.34,30638.09),
                                            ('SPX',7691.21,7680.7,7687.45)]:
                anchor=summarize(self.data[asset,step],start,start+3600,step)
                nine=summarize(self.data[asset,step],start+3600,start+7200,step)
                self.assertTrue(anchor['complete'] and nine['complete'])
                self.assertEqual((anchor['high'],anchor['low'],nine['close']),(high,low,nine_close))
                self.assertLess(nine['low'],low)
                self.assertTrue(low<nine_close<high)

    def test_staggered_nas_spx_nine_hour_purges_are_not_boneless(self):
        result=self.tool(); paired=result['review']['paired_smt']
        event=next(e for e in paired['events'] if e['side']=='sell_side')
        self.assertEqual(event['bar_open_ny'],'2026-10-01T21:03:00-04:00')
        self.assertEqual(event['peer_later_swept_at_ny'],'2026-10-01T21:21:00-04:00')
        self.assertFalse(event['setup_interval']['qualified_smt'])
        self.assertEqual(event['setup_interval']['own_purge_observed'],{'NAS100':True,'SPX':True})
        self.assertIsNone(event['boneless_asset'])
        self.assertNotIn('boneless_reference',event['paired_model1'])
        self.assertEqual(paired['spoken_summary'],'')
        self.assertFalse(paired['divergence_confirmed'])
        recap=result['review']['shift_story']['recap']
        self.assertNotIn('SMT-supported 9ate8',recap['spoken_summary'])
        for record in recap.get('paired_interpretation',[]):
            self.assertNotEqual(record['play_context'],'9ate8')
        compact=compact_voice_tool_result('review_market_session',result)
        self.assertEqual(compact['review']['paired_smt'],paired)
        self.assertEqual(compact['review']['date_ny'],'2026-10-01')

    def test_nas_own_selected_eight_range_midpoint_is_preserved(self):
        review=self.tool()['review']; story=review['shift_story']
        row=next(r for r in story['ranges'] if r['anchor_start_ny']=='2026-10-01T20:00:00-04:00')
        self.assertEqual(row['role'],'selected_range')
        self.assertIsNone(row['invalidated_at_ny'])
        self.assertEqual(row['objectives'][0]['evidence']['bar_open_ny'],'2026-10-01T21:59:00-04:00')
        self.assertEqual(row['objectives'][0]['level'],30638.46)
        self.assertNotEqual(row['objectives'][1]['status'],'observed_after_purge')


if __name__=='__main__': unittest.main()
