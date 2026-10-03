import copy
import unittest
from unittest.mock import patch
from gbop_voice_web.candle_evidence import parse_time, stamp, model1_evidence, summarize
from gbop_voice_web.smt_evidence import compare_ranges
from gbop_voice_web.market_context import enrich_smt
from gbop_voice_web.smt_reference import closing_candle, reconcile_paired_recap

START = parse_time('2026-10-02T08:00:00-04:00')
END = START + 4 * 3600
def fixture(step=300, delivered=True):
    left = [dict(time=t, open=95, high=100, low=90, close=95) for t in range(START, END, step)]
    right = [dict(time=t, open=195, high=200, low=190, close=195) for t in range(START, END, step)]
    t = START + 75 * 60
    left[(t-START)//step].update(open=99, high=102, low=98, close=101)
    right[(t-START)//step].update(open=197, high=198, low=194, close=196)
    if delivered:
        right[(START+85*60-START)//step].update(open=195, high=196, low=189, close=192)
    return [dict(asset='XAGUSD', symbol='XAGUSD', bars=left, step=step),
            dict(asset='XAUUSD', symbol='XAUUSD', bars=right, step=step)]
def review(pair):
    return enrich_smt(compare_ranges(*pair, START, START+3600, END))
def bearish(result):
    return next(e for e in result['events'] if e['side'] == 'buy_side')

class ReferenceTests(unittest.TestCase):
    def test_hourly_name_is_open_not_close(self):
        c = closing_candle('2026-10-02T11:00:00-04:00')
        self.assertIn('10:00 AM H1 candle', c['spoken_label'])
        self.assertIn('11:00 AM', c['spoken_label'])
        self.assertNotIn('11:00 AM H1 candle', c['spoken_label'])
    def test_night_midnight_names_previous_candle(self):
        c = closing_candle('2026-10-03T00:00:00-04:00')
        self.assertEqual(c['candle_open_ny'], '2026-10-02T23:00:00-04:00')
    def test_five_minute_and_h4_names(self):
        self.assertIn('9:15 AM M5 candle', closing_candle('2026-10-02T09:20:00-04:00','M5')['spoken_label'])
        self.assertIn('9:00 AM H4 candle', closing_candle('2026-10-02T13:00:00-04:00','H4')['spoken_label'])
    def test_daily_boundary_respects_dst(self):
        c = closing_candle('2026-11-02T00:00:00-05:00','D1')
        self.assertEqual(c['candle_open_ny'],'2026-11-01T00:00:00-04:00')
    def test_boneless_reference_matches_time_but_keeps_own_prices(self):
        r = bearish(review(fixture()))['paired_model1']
        self.assertEqual(r['status'], 'identified')
        self.assertEqual(r['boneless_reference']['bar_open_ny'], '2026-10-02T09:15:00-04:00')
        self.assertEqual(r['origin_model1']['bar_open_ny'], r['boneless_reference']['bar_open_ny'])
        self.assertEqual(r['boneless_reference']['open'], 197)
        self.assertEqual(r['origin_model1']['open'], 99)
        self.assertFalse(r['boneless_reference']['local_purge_observed'])
        self.assertEqual(r['boneless_reference']['identity_basis'], 'smt_time_aligned')
        self.assertEqual(r['boneless_reference']['csd_status'],'not_assessed')
    def test_wick_only_divergence_does_not_fabricate_model1(self):
        p = fixture(); p[0]['bars'][15]['close'] = 99
        self.assertNotEqual(bearish(review(p))['paired_model1']['status'], 'identified')
    def test_missing_peer_bar_does_not_fabricate_reference(self):
        p = fixture(); del p[1]['bars'][15]
        r = review(p)
        self.assertFalse(r['paired_coverage_complete'])
        self.assertFalse(any(e.get('paired_model1',{}).get('status')=='identified' for e in r['events']))
    def test_m1_event_maps_to_actual_m5_open(self):
        p = fixture(60)
        p[0]['bars'][75].update(high=100,close=99)
        p[0]['bars'][77].update(high=102,close=101)
        p[0]['bars'][79].update(high=102,close=101)
        r = bearish(review(p))['paired_model1']
        self.assertEqual(r['status'],'identified')
        self.assertEqual(r['boneless_reference']['bar_open_ny'],'2026-10-02T09:15:00-04:00')
        self.assertEqual(r['boneless_reference']['timeframe'],'M5')
    def test_peer_catchup_before_body_close_prevents_new_reference(self):
        p = fixture(60)
        p[0]['bars'][75].update(high=100,close=99)
        p[0]['bars'][77].update(high=102,close=101)
        p[0]['bars'][79].update(high=102,close=101)
        p[1]['bars'][78].update(high=201)
        self.assertNotEqual(bearish(review(p))['paired_model1']['status'],'identified')
    def test_no_copy_of_partner_delivery(self):
        p = fixture(delivered=False)
        for bar in p[1]['bars']:
            if bar['time'] >= START + 3600:
                bar['low'] = max(bar['low'], 191)
        p[0]['bars'][17].update(low=89,close=92)
        e = bearish(review(p))
        self.assertEqual(e['objective_status']['XAGUSD']['opposing_liquidity']['status'],'objective_complete_while_range_valid')
        self.assertNotEqual(e['objective_status']['XAUUSD']['opposing_liquidity']['status'],'objective_complete_while_range_valid')
    def test_later_local_failure_does_not_replace_earlier_reference(self):
        p = fixture(); p[1]['bars'][35].update(low=180,close=185)
        r = review(p); e = bearish(r)
        self.assertEqual(e['paired_model1']['boneless_reference']['bar_open_ny'],'2026-10-02T09:15:00-04:00')
        self.assertEqual(e['objective_status']['XAUUSD']['opposing_liquidity']['status'],'objective_complete_while_range_valid')
        self.assertIn('10:00 AM H1 candle',r['invalidating_candles']['XAUUSD']['spoken_label'])
        original = 'Local Model 1 failed before its bullish objective.'
        session = {'paired_smt':r,'shift_story':{'recap':{'spoken_summary':original,'headline':'Failed'},'ranges':[]}}
        reconcile_paired_recap(session,'XAUUSD')
        recap = session['shift_story']['recap']
        self.assertIn('completed its own opposing',recap['headline'])
        self.assertEqual(recap['local_only_spoken_summary'],original)
        self.assertIn('SMT-inherited Model 1',recap['spoken_summary'])
    def test_direct_crt_path_includes_paired_identity(self):
        from gbop_voice_web.market_data import market_tool
        p = fixture(); by_asset = {item['asset']:item for item in p}
        def feed(db, asset):
            return dict(ok=True,asset=asset,symbol=asset)
        def history(db, item, start, end):
            return by_asset[item['asset']]['bars'],300
        with patch('gbop_voice_web.market_data.read_feed',side_effect=feed), patch('gbop_voice_web.market_data.history_bars',side_effect=history):
            result = market_tool(None,'review_market_crt',dict(asset='XAUUSD',anchor_start_ny=stamp(START),through_ny=stamp(END),anchor_timeframe='H1',confirmation_timeframe=None))
        self.assertTrue(result['ok'],result)
        self.assertEqual(bearish(result['review']['paired_smt'])['paired_model1']['status'],'identified')
        self.assertIn('paired_interpretation',result['review']['recap'])
    def test_repeat_reconciliation_does_not_duplicate_summary(self):
        session = {'paired_smt':review(fixture()),'shift_story':{'recap':{'spoken_summary':'local'},'ranges':[]}}
        reconcile_paired_recap(session,'XAUUSD')
        first = session['shift_story']['recap']['spoken_summary']
        reconcile_paired_recap(session,'XAUUSD')
        self.assertEqual(first,session['shift_story']['recap']['spoken_summary'])

    def test_bullish_reference_is_symmetric(self):
        p = fixture()
        for item in p:
            for bar in item['bars']:
                o,h,l,c = (bar[k] for k in ('open','high','low','close'))
                bar.update(open=1000-o,high=1000-l,low=1000-h,close=1000-c)
        e = next(e for e in review(p)['events'] if e['side']=='sell_side')
        self.assertEqual(e['paired_model1']['status'],'identified')
        self.assertEqual(e['paired_model1']['boneless_reference']['direction'],'bullish')
        self.assertEqual(e['paired_model1']['boneless_reference']['open'],803)
    def test_voice_retains_paired_identity_and_reconciled_recap(self):
        from gbop_voice_web.voice_runtime import compact_voice_tool_result
        session = {'paired_smt':review(fixture()),'shift_story':{'recap':{'spoken_summary':'Local-only failure'},'ranges':[]}}
        reconcile_paired_recap(session,'XAUUSD')
        result = compact_voice_tool_result('review_market_session',{'ok':True,'review':session})
        e = bearish(result['review']['paired_smt'])
        self.assertEqual(e['paired_model1']['boneless_reference']['bar_open_ny'],'2026-10-02T09:15:00-04:00')
        self.assertIn('SMT-inherited Model 1',result['review']['shift_recap']['spoken_summary'])
