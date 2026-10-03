"""Synthetic sequels: assert candle occurrence separately from outcome and execution."""
import unittest
from gbop_voice_web.candle_evidence import parse_time, stamp
from gbop_voice_web.purge_lifecycle import attach_lifecycles

T = parse_time('2026-10-02T10:00:00-04:00')
def bar(i, o, h, l, c, unit=300):
    return dict(time=T+i*unit, open=o, high=h, low=l, close=c)

def evaluate(seq, end=None, invalid=None, side='buy', step=300, mapped='M5'):
    candle = dict(identity='Model 1 candle', timeframe=mapped,
        bar_open_ny=stamp(T), bar_close_ny=stamp(T+300),
        open=99 if side=='buy' else 91, high=103 if side=='buy' else 92,
        low=98 if side=='buy' else 87, close=102 if side=='buy' else 88,
        purged_side=side)
    anchor=dict(complete=True, start_ny=stamp(T-3600),end_ny=stamp(T),
                open=95,high=100,low=90,close=95,midpoint=95)
    result=dict(candles=[candle], csd_status='not_assessed', super_soup_status='not_assessed')
    all_bars=[bar(0,candle['open'],candle['high'],candle['low'],candle['close'])]+seq
    attach_lifecycles(all_bars,anchor,mapped,end or T+(len(seq)+1)*300,step,result,invalid)
    return result

class PurgeLifecycleTests(unittest.TestCase):
    def test_next_candle_wick_soups_model1_then_csd_and_delivery(self):
        r=evaluate([bar(1,102,104,100,101),bar(2,101,102,97,98),bar(3,98,99,89,91)])
        e=r['lifecycles'][0]
        self.assertEqual(e['super_soup']['status'],'super_soup_observed')
        self.assertEqual(e['super_soup']['sweep']['form'],'wick_only')
        self.assertTrue(e['super_soup']['sweep']['immediate_next_assigned_candle'])
        self.assertEqual(e['csd']['evidence']['confirmed_at_ny'],stamp(T+900))
        self.assertEqual(e['objectives_after_csd_close']['opposing_liquidity']['status'],'delivered_before_range_invalidation')
        self.assertEqual(e['execution_status'],'not_assessed')
    def test_body_purge_without_rejection_not_a_successful_reversal(self):
        e=evaluate([bar(1,102,106,101,105)])['lifecycles'][0]
        self.assertEqual(e['super_soup']['sweep']['form'],'body_cross_and_close')
        self.assertEqual(e['super_soup']['status'],'body_purge_observed_rejection_unverified')
        self.assertEqual(e['csd']['status'],'not_observed_in_complete_window')
    def test_body_purge_then_later_return_is_preserved(self):
        e=evaluate([bar(1,102,106,101,105),bar(2,105,106,100,101)])['lifecycles'][0]
        self.assertEqual(e['super_soup']['status'],'body_purge_then_return_observed')
    def test_no_super_soup_when_csd_without_new_extreme(self):
        e=evaluate([bar(1,102,103,97,98),bar(2,98,105,96,97)])['lifecycles'][0]
        self.assertEqual(e['super_soup']['status'],'not_observed_in_complete_pre_csd_window')
        self.assertEqual(e['csd']['status'],'observed')
    def test_equal_extreme_is_not_a_sweep(self):
        e=evaluate([bar(1,102,103,100,101)])['lifecycles'][0]
        self.assertIsNone(e['super_soup']['sweep'])
    def test_same_candle_sweep_and_csd_is_explicit(self):
        e=evaluate([bar(1,102,104,96,97)])['lifecycles'][0]
        self.assertEqual(e['super_soup']['status'],'super_soup_observed')
        self.assertEqual(e['csd']['evidence']['confirmed_at_ny'],stamp(T+600))
        self.assertTrue(e['super_soup']['sweep']['before_csd_close_verified'])
    def test_later_candle_can_soup_without_immediate_soup(self):
        e=evaluate([bar(1,102,103,100,101),bar(2,101,104,100,102)])['lifecycles'][0]
        self.assertFalse(e['super_soup']['sweep']['immediate_next_assigned_candle'])
    def test_gap_does_not_prove_no_soup_or_pre_csd_order(self):
        e=evaluate([bar(2,102,104,100,101)],end=T+900)['lifecycles'][0]
        self.assertEqual(e['super_soup']['status'],'sweep_observed_pre_csd_order_unverified')
        self.assertFalse(e['super_soup']['pre_csd_window_complete'])
    def test_no_followup_yet_does_not_mean_no_soup(self):
        r=evaluate([])
        self.assertEqual(r['lifecycles'][0]['super_soup']['status'],'no_subsequent_closed_candle')
        self.assertEqual(r['csd_status'],'not_assessed')
    def test_partial_assigned_candle_not_used(self):
        e=evaluate([bar(1,102,104,100,101)],end=T+450)['lifecycles'][0]
        self.assertIsNone(e['next_assigned_candle'])
        self.assertIsNone(e['super_soup']['sweep'])
    def test_bullish_mirror(self):
        e=evaluate([bar(1,88,90,86,89),bar(2,89,94,88,93),bar(3,93,101,92,99)],side='sell')['lifecycles'][0]
        self.assertEqual(e['super_soup']['status'],'super_soup_observed')
        self.assertEqual(e['csd']['status'],'observed')
        self.assertEqual(e['objectives_after_csd_close']['opposing_liquidity']['level'],100)
    def test_after_invalidation_touch_is_not_success(self):
        e=evaluate([bar(1,102,104,100,101),bar(2,101,102,100,101),bar(3,101,102,89,91)],invalid=T+900)['lifecycles'][0]
        o=e['super_soup']['objectives_after_sweep_candle_close']['opposing_liquidity']
        self.assertEqual(o['status'],'range_invalidated_before_verified_delivery')
        self.assertIsNotNone(o['later_touch_after_range_invalidation'])
    def test_touch_in_invalidating_bar_is_not_ordered_delivery(self):
        e=evaluate([bar(1,102,104,100,101),bar(2,101,106,89,105)],invalid=T+900)['lifecycles'][0]
        o=e['super_soup']['objectives_after_sweep_candle_close']['opposing_liquidity']
        self.assertEqual(o['status'],'touch_in_invalidating_source_bar_order_unresolved')
    def test_identity_is_immutable_when_later_events_arrive(self):
        before=evaluate([])['candles']
        after=evaluate([bar(1,102,104,100,101)])['candles']
        self.assertEqual(before,after)
    def test_retest_and_adverse_close_are_not_stopouts(self):
        e=evaluate([bar(1,102,104,100,101),bar(2,101,102,97,98),bar(3,98,100,97,99),bar(4,99,106,98,105)])['lifecycles'][0]
        self.assertEqual(e['first_body_open_retest_after_csd']['start_ny'],stamp(T+900))
        self.assertIsNotNone(e['later_adverse_close_beyond_model1_extreme'])
        self.assertEqual(e['stopout_status'],'requires_member_stop_rule')
    def test_csd_equality_is_not_confirmation(self):
        e=evaluate([bar(1,102,103,98,99)])['lifecycles'][0]
        self.assertEqual(e['csd']['status'],'not_observed_in_complete_window')
    def test_wick_soup_not_relabelled_model1(self):
        r=evaluate([bar(1,99,103,96,99)])
        self.assertEqual(r['wick_soups']['candles'][0]['identity'],'Turtle Wick Soup')
        self.assertFalse(r['wick_soups']['candles'][0]['is_model1_body_candle'])
    def test_coverage_gap_prevents_validity_claim_on_later_touch(self):
        e=evaluate([bar(2,102,104,100,101),bar(3,101,102,89,91)],end=T+1200)['lifecycles'][0]
        o=e['super_soup']['objectives_after_sweep_candle_close']['opposing_liquidity']
        self.assertEqual(o['status'],'touch_observed_range_validity_unverified')

class LifecycleIntegrationTests(unittest.TestCase):
    def test_real_crt_review_exposes_lifecycle(self):
        from gbop_voice_web.candle_evidence import crt_review
        data=[bar(i,95,100,90,95) for i in range(-12,0)]
        data += [bar(0,99,103,98,102),bar(1,102,104,100,101),bar(2,101,102,97,98)]
        r=crt_review(data,T-3600,T+900,'H1',300)
        self.assertFalse(r['entry_confirmed'])
        self.assertEqual(r['model1']['lifecycles'][0]['super_soup']['status'],'super_soup_observed')
    def test_shift_review_carries_the_same_evidence(self):
        from gbop_voice_web.shift_review import review_shift
        data={b['time']:b for b in [bar(i,95,100,90,95) for i in range(-24,24)]}
        for b in [bar(0,99,103,98,102),bar(1,102,104,100,101),bar(2,101,102,97,98)]:
            data[b['time']]=b
        r=review_shift(sorted(data.values(),key=lambda x:x['time']),'2026-10-02','day',300)
        selected=next(x for x in r['ranges'] if x['anchor_start_ny']==stamp(T-3600))
        self.assertEqual(selected['model1']['lifecycles'][0]['csd']['status'],'observed')
    def test_voice_compaction_keeps_lifecycle_timestamps(self):
        from gbop_voice_web.voice_runtime import compact_voice_tool_result
        r={'review':{'model1':evaluate([bar(1,102,104,100,101)])}}
        out=compact_voice_tool_result('review_market_crt',r)
        self.assertEqual(out['review']['model1']['lifecycles'][0]['super_soup']['sweep']['candle']['start_ny'],stamp(T+300))
    def test_shared_canon_loads_lifecycle_rules(self):
        from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, KNOWLEDGE_FILES
        self.assertIn('gtop_lifecycle.txt',KNOWLEDGE_FILES)
        self.assertIn('model1.lifecycles',CANONICAL_KNOWLEDGE)
        self.assertIn('never claim to be monitoring',CANONICAL_KNOWLEDGE)
    def test_h4_uses_m15_for_both_model1_and_super_soup(self):
        from gbop_voice_web.candle_evidence import crt_review
        start=T-4*3600
        data=[dict(time=t,open=95,high=100,low=90,close=95) for t in range(start,T,900)]
        data += [bar(0,99,103,98,102,900),bar(1,102,104,100,101,900),bar(2,101,102,97,98,900)]
        r=crt_review(data,start,T+2700,'H4',900)
        self.assertEqual(r['model1']['candles'][0]['timeframe'],'M15')
        self.assertEqual(r['model1']['lifecycles'][0]['super_soup']['sweep']['candle']['start_ny'],stamp(T+900))

if __name__=='__main__': unittest.main()
