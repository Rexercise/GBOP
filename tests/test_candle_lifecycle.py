import unittest
from gbop_voice_web.candle_evidence import summarize, parse_time
from gbop_voice_web.candle_lifecycle import lifecycle_review

T = parse_time('2026-10-02T09:00:00-04:00')

def bar(t, o=100, h=105, l=95, c=100):
    return dict(time=t, open=o, high=h, low=l, close=c)

def fixture():
    anchor_bars = [bar(T+i*300,100,110,90,100) for i in range(12)]
    later = [bar(T+3600,108,115,107,112),
             bar(T+3900,112,116,110,111),
             bar(T+4200,111,112,106,107),
             bar(T+4500,107,109,103,104),
             bar(T+4800,104,105,99,100),
             bar(T+5100,100,101,89,91),
             bar(T+5400,91,114,90,113)]
    return anchor_bars+later

class CandleLifecycleTests(unittest.TestCase):
    def review(self, bars=None, invalid=None, end=None, tf='M5'):
        bars=fixture() if bars is None else bars
        return lifecycle_review(bars,summarize(bars,T,T+3600,300),tf,
                                end or max(b['time']+300 for b in bars),300,invalid)
    def body(self, result):
        return next(f for f in result['purge_candles'] if f['identity']=='Model 1 candle')
    def test_body_identity_ohlc_and_close_time(self):
        f=self.body(self.review());self.assertEqual(f['open'],108)
        self.assertEqual(f['identified_at_ny'],'2026-10-02T10:05:00-04:00')
        self.assertEqual(f['purged_level'],110)
    def test_unconfirmed_body_is_still_identified(self):
        f=self.body(self.review(fixture()[:13]))
        self.assertEqual(f['identity'],'Model 1 candle')
        self.assertEqual(f['csd']['status'],'not_observed_by_review_cutoff')
    def test_csd_uses_body_open_not_wick_low(self):
        f=self.body(self.review())
        self.assertEqual(f['csd']['reference_level'],108)
        self.assertEqual(f['csd']['evidence']['confirmed_at_ny'],'2026-10-02T10:15:00-04:00')
    def test_pre_csd_super_soup(self):
        f=self.body(self.review());self.assertEqual(f['super_soup']['status'],'observed_before_csd')
        self.assertEqual(f['super_soup']['evidence']['return_candle']['bar_close_ny'],'2026-10-02T10:10:00-04:00')
    def test_retest_and_disrespect_are_distinct_from_range_invalidation(self):
        f=self.body(self.review()); self.assertEqual(f['body_reference_retest']['evidence']['bar_open_ny'],'2026-10-02T10:15:00-04:00')
        self.assertEqual(f['body_disrespect_close']['evidence']['bar_close_ny'],'2026-10-02T10:35:00-04:00')
        self.assertTrue(f['body_disrespect_close']['evidence']['not_a_member_stop_or_parent_range_invalidation'])
    def test_own_midpoint_and_full_objective(self):
        f=self.body(self.review())
        self.assertEqual(f['objectives_after_csd']['midpoint']['level'],100)
        self.assertEqual(f['objectives_after_csd']['opposing_liquidity']['level'],90)
        self.assertEqual(f['objectives_after_csd']['opposing_liquidity']['status'],'observed_after_event')
    def test_delivery_survives_later_invalidation(self):
        f=self.body(self.review(invalid=T+5700))
        self.assertEqual(f['objectives_after_formation']['opposing_liquidity']['status'],'observed_after_event')
    def test_target_in_invalidating_bar_is_ambiguous(self):
        f=self.body(self.review(invalid=T+5400))
        self.assertEqual(f['objectives_after_formation']['opposing_liquidity']['status'],'touch_in_invalidating_bar_order_unresolved')
    def test_no_events_after_range_invalidation(self):
        f=self.body(self.review(invalid=T+4800))
        self.assertIsNone(f['body_disrespect_close']['evidence'])
        self.assertEqual(f['objectives_after_formation']['opposing_liquidity']['status'],'not_observed_before_range_invalidation')
    def test_missing_bar_does_not_become_no_csd_or_pre_csd_soup(self):
        bars=fixture();del bars[13]
        f=self.body(self.review(bars))
        self.assertEqual(f['csd']['status'],'unverified_incomplete_coverage')
        self.assertEqual(f['super_soup']['status'],'unverified_incomplete_coverage')
    def test_same_candle_super_soup_and_csd_not_claimed_pre_csd(self):
        bars=fixture();bars[13]=bar(T+3900,112,116,106,107)
        f=self.body(self.review(bars))
        self.assertEqual(f['super_soup']['status'],'same_candle_as_csd_order_unresolved')
    def test_wick_soup_is_not_a_body_model1(self):
        bars=fixture()[:12]+[bar(T+3600,108,114,107,109)]
        f=self.review(bars)['purge_candles'][0]
        self.assertEqual(f['purge_type'],'wick_soup');self.assertEqual(f['csd']['status'],'not_a_model1_body_candle')
    def test_close_exactly_at_range_boundary_not_body_soup(self):
        bars=fixture()[:12]+[bar(T+3600,108,114,107,110)]
        self.assertEqual(self.review(bars)['purge_candles'][0]['identity'],'Turtle Wick Soup')
    def test_gap_open_above_boundary_not_fabricated_body_cross(self):
        bars=fixture()[:12]+[bar(T+3600,111,114,111,113)]
        self.assertEqual(self.review(bars)['identified_count'],0)
    def test_forming_assigned_candle_not_identified(self):
        self.assertEqual(self.review(fixture(),end=T+3720)['identified_count'],0)
    def test_coarse_source_cannot_invent_fine_candles(self):
        self.assertEqual(self.review(tf='M1')['status'],'resolution_unavailable')
    def test_bullish_symmetry(self):
        bars=[{**b,'open':200-b['open'],'close':200-b['close'],
               'high':200-b['low'],'low':200-b['high']} for b in fixture()]
        f=self.body(self.review(bars));self.assertEqual(f['direction'],'bullish')
        self.assertEqual(f['csd']['status'],'confirmed');self.assertEqual(f['super_soup']['status'],'observed_before_csd')
        self.assertEqual(f['objectives_after_formation']['opposing_liquidity']['level'],110)
    def test_night_uses_correct_date_and_timezone(self):
        bars=[{**b,'time':b['time']+12*3600} for b in fixture()]
        a=summarize(bars,T+43200,T+46800,300)
        f=self.body(lifecycle_review(bars,a,'M5',T+48900,300))
        self.assertEqual(f['bar_open_ny'],'2026-10-02T22:00:00-04:00')
    def test_requested_cutoff_prevents_hindsight_confirmation(self):
        f=self.body(self.review(end=T+4200))
        self.assertNotEqual(f['csd']['status'],'confirmed')
    def test_two_sided_candle_records_order_warning(self):
        bars=fixture()[:12]+[bar(T+3600,108,114,89,112)]
        self.assertTrue(self.body(self.review(bars))['both_boundaries_pierced_in_same_candle'])
    def test_confirmation_candle_not_counted_as_later_retest(self):
        f=self.body(self.review(fixture()[:15]))
        self.assertIsNone(f['body_reference_retest']['evidence'])
    def test_midpoint_only_does_not_complete_opposing_objective(self):
        f=self.body(self.review(fixture()[:17]))
        self.assertEqual(f['objectives_after_csd']['midpoint']['status'],'observed_after_event')
        self.assertEqual(f['objectives_after_csd']['opposing_liquidity']['status'],'not_observed_by_review_cutoff')
    def test_lifecycle_never_asserts_member_execution(self):
        self.assertEqual(self.review()['execution_status'],'not_assessed')
    def test_summary_keeps_identity_and_confirmation_separate(self):
        text=self.review()['spoken_summary']
        self.assertIn('10:00 AM',text);self.assertIn('CSD confirmed on the closure of the 10:10 AM M5 candle',text)
    def test_missing_anchor_stays_unverified(self):
        self.assertEqual(self.review(fixture()[1:])['status'],'unverified_incomplete_anchor')

if __name__=='__main__': unittest.main()
