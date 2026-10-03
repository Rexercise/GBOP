"""Selected-candle open chronology, independent of Model 1 and member trades."""
import unittest
from gbop_voice_web.candle_evidence import crt_review, parse_time, stamp, summarize
from gbop_voice_web.blessed_thief_evidence import blessed_thief_review, PAGE_SIZE

T = parse_time('2026-10-02T09:00:00-04:00')
STEP = 300


def bar(t, o=100, h=104, l=98, c=100):
    return dict(time=t, open=o, high=h, low=l, close=c)


def fixture(hours=3):
    bars = [bar(T+i*STEP, 100, 110, 90, 100) for i in range(12)]
    bars += [bar(T+3600+i*STEP) for i in range(hours*12)]
    bars[12:15] = [bar(T+3600,104,113,103,112), bar(T+3900,112,114,108,110),
                   bar(T+4200,110,111,102,103)]
    if hours > 1:
        bars[24:26] = [bar(T+7200,103,106,102,105), bar(T+7500,105,106,102,102)]
    if hours > 2:
        bars[36:38] = [bar(T+10800,102,105,101,104), bar(T+11100,104,105,99,100)]
    return bars


def review(bars=None, **kwargs):
    bars = fixture() if bars is None else bars
    end = kwargs.pop('end', bars[-1]['time'] + STEP)
    return crt_review(bars, T, end, 'H1', STEP, **kwargs)['blessed_thief']


class BlessedThiefTests(unittest.TestCase):
    def test_every_later_hour_keeps_own_open_and_revisit(self):
        r = review()
        self.assertEqual(r['timeframe'], 'H1')
        self.assertEqual(r['tier'], 3)
        self.assertEqual([x['opening_price'] for x in r['candles']], [104,103,102])
        self.assertEqual([x['open_revisit']['directional_trigger']['known_at_ny'] for x in r['candles']],
                         [stamp(T+4500),stamp(T+7800),stamp(T+11400)])
        self.assertTrue(all(x['open_revisit']['status'] == 'directional_revisit_observed' for x in r['candles']))

    def test_default_does_not_inherit_model1_mapping(self):
        r = crt_review(fixture(), T, T+14400, 'H1', STEP)
        self.assertEqual(r['assigned_timeframe'], 'M5')
        self.assertEqual(r['blessed_thief']['timeframe'], 'H1')
        self.assertEqual(review(confirmation_tf='M15')['timeframe'], 'H1')

    def test_bullish_mirror(self):
        bars = [{**b, 'open':200-b['open'], 'close':200-b['close'],
                 'high':200-b['low'], 'low':200-b['high']} for b in fixture()]
        r = review(bars)
        self.assertEqual(r['direction'], 'bullish')
        self.assertEqual([x['opening_price'] for x in r['candles']], [96,97,98])
        self.assertEqual(r['objective']['level'],110)
        self.assertTrue(all(x['open_revisit']['status'] == 'directional_revisit_observed' for x in r['candles']))

    def test_selected_open_is_not_an_automatic_entry(self):
        r = review(fixture()[:13])
        self.assertIsNone(r['candles'][0]['open_revisit']['directional_trigger'])
        self.assertFalse(r['candles'][0]['automatic_entry_at_open'])
        self.assertEqual(r['execution_status'],'not_assessed')

    def test_same_source_manipulation_and_cross_unresolved(self):
        bars=fixture()[:13];bars[12]=bar(T+3600,104,113,103,103)
        f=review(bars)['candles'][0]['open_revisit']
        self.assertEqual(f['status'],'same_source_bar_order_unresolved')
        self.assertIsNone(f['directional_trigger'])

    def test_both_sides_in_purge_bar_never_invents_direction(self):
        bars=fixture();bars[12]=bar(T+3600,104,113,89,103)
        r=review(bars)
        self.assertIsNone(r['direction'])
        self.assertEqual(r['manipulation']['status'],'both_sides_same_source_bar_order_unresolved')
        self.assertTrue(all(x['open_revisit']['directional_trigger'] is None for x in r['candles']))

    def test_open_can_be_revisited_in_later_selected_candle(self):
        bars=fixture()
        bars[12:24]=[bar(T+3600+i*STEP,104 if i==0 else 106,113 if i==0 else 109,105 if i else 104,106) for i in range(12)]
        bars[24:26]=[bar(T+7200,106,107,105,106),bar(T+7500,106,107,103,103)]
        f=review(bars)['candles'][0]['open_revisit']
        self.assertEqual(f['directional_trigger']['evidence']['bar_open_ny'],stamp(T+7500))

    def test_return_from_distribution_side_is_recorded(self):
        bars=fixture()[:16]
        bars[14]=bar(T+4200,103,103,101,102) # gap through 104, no touch
        bars[15]=bar(T+4500,102,104,101,102)
        f=review(bars)['candles'][0]['open_revisit']
        self.assertEqual(f['directional_trigger']['mode'],'return_and_reject_toward_distribution')
        self.assertEqual(f['first_revisit']['bar_open_ny'],stamp(T+4500))

    def test_gap_through_price_is_not_a_fill_or_touch(self):
        bars=fixture()[:15];bars[14]=bar(T+4200,103,103,101,102)
        f=review(bars)['candles'][0]['open_revisit']
        self.assertEqual(f['status'],'gap_through_open_without_observed_price_touch')
        self.assertIsNone(f['directional_trigger'])

    def test_midpoint_does_not_end_sequence(self):
        r=review()
        self.assertEqual(r['total_candle_count'],3)
        self.assertEqual(r['status'],'review_cutoff')

    def test_opposing_objective_ends_sequence(self):
        bars=fixture();bars[30]=bar(T+9000,100,102,89,91)
        r=review(bars)
        self.assertEqual(r['status'],'objective_reached')
        self.assertEqual(r['total_candle_count'],2)
        self.assertEqual(r['window_end_ny'],stamp(T+9300))

    def test_trigger_and_objective_same_bar_cannot_establish_entry_first(self):
        bars=fixture();bars[14]=bar(T+4200,110,111,89,91)
        f=review(bars)['candles'][0]['open_revisit']
        self.assertEqual(f['status'],'trigger_and_objective_same_source_bar_order_unresolved')
        self.assertFalse(f['exact_fill_known'])

    def test_parent_invalidating_close_stops_new_levels(self):
        bars=fixture();bars[23]=bar(T+6900,108,113,107,112)
        r=review(bars)
        self.assertEqual(r['status'],'range_invalidated')
        self.assertEqual(r['total_candle_count'],1)
        self.assertEqual(r['candles'][0]['open_revisit']['status'],'directional_revisit_observed')
        self.assertEqual(r['range_invalidated_at_ny'],stamp(T+7200))

    def test_trigger_in_invalidating_close_bar_stays_uncertain(self):
        bars=fixture()[:12]+[bar(T+3600+i*STEP,122 if i==0 else 123,124,122,123) for i in range(12)]
        bars[-1]=bar(T+6900,123,124,117,118)
        r=review(bars)
        self.assertEqual(r['status'],'range_invalidated')
        self.assertEqual(r['candles'][0]['open_revisit']['status'],'trigger_in_invalidating_bar_order_unresolved')

    def test_h4_anchor_defaults_to_h4_opens_not_m15_model1(self):
        bars=[bar(T+i*STEP,100,110,90,100) for i in range(48)]
        bars += [bar(T+14400+i*STEP) for i in range(48)]
        bars[48]=bar(T+14400,104,113,103,112)
        r=crt_review(bars,T,T+28800,'H4',STEP)
        self.assertEqual(r['assigned_timeframe'],'M15')
        self.assertEqual(r['blessed_thief']['timeframe'],'H4')
        self.assertEqual(r['blessed_thief']['total_candle_count'],1)

    def test_target_and_invalidation_same_bar_order_explicit(self):
        bars=fixture();bars[23]=bar(T+6900,108,113,89,112)
        r=review(bars)
        self.assertEqual(r['objective']['status'],'objective_in_invalidating_bar_order_unresolved')

    def test_missing_selected_open_is_not_first_available_price(self):
        bars=fixture();del bars[24]
        r=review(bars);f=r['candles'][1]
        self.assertIsNone(f['opening_price'])
        self.assertEqual(f['open_revisit']['status'],'opening_price_unverified')
        self.assertEqual(r['candles'][2]['thesis_validity_at_open'],'unverified_after_source_gap')
        self.assertIsNone(r['candles'][2]['open_revisit']['directional_trigger'])

    def test_missing_bar_blocks_later_trigger_but_keeps_earlier_fact(self):
        bars=fixture();del bars[20]
        r=review(bars)
        self.assertEqual(r['candles'][0]['open_revisit']['status'],'directional_revisit_observed')
        self.assertEqual(r['candles'][1]['open_revisit']['status'],'unverified_after_source_gap')

    def test_gap_before_manipulation_cannot_invent_first_sweep(self):
        bars=fixture();del bars[12]
        r=review(bars)
        self.assertIsNone(r['manipulation'])
        self.assertTrue(all(x['open_revisit']['directional_trigger'] is None for x in r['candles']))

    def test_missing_anchor_unverified(self):
        self.assertEqual(review(fixture()[1:])['status'],'unverified_incomplete_anchor')

    def test_paging_keeps_original_context_and_all_later_opens(self):
        bars=fixture(5)
        a=review(bars,blessed_thief_tf='M5')
        self.assertEqual(len(a['candles']),PAGE_SIZE)
        self.assertEqual(a['total_candle_count'],60)
        pages=[a]
        while pages[-1]['next_candle_start_ny']:
            pages.append(review(bars,blessed_thief_tf='M5',blessed_thief_from=pages[-1]['next_candle_start_ny']))
        rows=[r for p in pages for r in p['candles']]
        self.assertEqual(len({r['bar_open_ny'] for r in rows}),60)
        self.assertIsNone(pages[-1]['next_candle_start_ny'])
        self.assertEqual(a['manipulation'],pages[-1]['manipulation'])
        self.assertEqual(a['anchor_start_ny'],pages[-1]['anchor_start_ny'])

    def test_invalid_page_boundary_rejected(self):
        with self.assertRaisesRegex(ValueError,'candle boundary'):
            review(blessed_thief_from=stamp(T+3900))

    def test_arbitrary_anchor_and_selected_timeframes(self):
        bars=fixture();anchor=summarize(bars,T,T+3600,STEP)
        r=blessed_thief_review(bars,anchor,'H6',T+14400,STEP,candle_tf='M30')
        self.assertEqual(r['timeframe'],'M30')
        self.assertEqual(r['anchor_timeframe'],'H6')
        self.assertEqual(r['total_candle_count'],6)

    def test_insufficient_resolution_not_reconstructed(self):
        self.assertEqual(review(blessed_thief_tf='M1')['status'],'resolution_unavailable')

    def test_cutoff_prevents_hindsight_trigger(self):
        r=review(end=T+4200)
        self.assertIsNone(r['candles'][0]['open_revisit']['directional_trigger'])
        self.assertTrue(r['candles'][0]['forming_at_cutoff'])

    def test_extreme_as_of_trigger_does_not_use_future_high(self):
        bars=fixture();bars[20]=bar(T+6000,100,118,98,100)
        f=review(bars)['candles'][0]['open_revisit']
        self.assertEqual(f['manipulation_extreme_as_of_trigger']['price'],114)
        self.assertEqual(f['manipulation_extreme_at_cutoff']['price'],118)
        self.assertTrue(f['manipulation_extreme_as_of_trigger']['not_a_member_stop'])
        self.assertEqual(f['adverse_open_excursion']['extreme_price_at_cutoff'],118)

    def test_no_manipulation_not_established(self):
        bars=fixture()[:12]+[bar(T+3600+i*STEP) for i in range(12)]
        self.assertEqual(review(bars)['status'],'no_manipulation_observed')

if __name__=='__main__':
    unittest.main()
