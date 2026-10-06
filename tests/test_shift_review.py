import unittest
from gbop_voice_web.shift_review import review_shift
from gbop_voice_web.candle_evidence import parse_time, crt_review
from gbop_voice_web.market_data import session_review
from gbop_voice_web.voice_runtime import compact_voice_tool_result


def fixture(day='2026-10-02', night=False, step=300):
    start = parse_time(day + ('T20:00:00' if night else 'T08:00:00'))
    rows = []
    # 9 closes below 8. 10 sweeps 9's low and returns, 11 delivers 9's high.
    for hour, (o, h, l, c) in enumerate([(100, 110, 90, 100), (95, 100, 80, 85),
                                       (85, 95, 82, 90), (90, 99, 85, 95)]):
        rows.extend(dict(time=start+hour*3600+n*step, open=o, high=h, low=l, close=c)
                    for n in range(3600//step))
    purge = 2*3600//step
    rows[purge].update(open=85, high=87, low=78, close=82)
    rows[purge+1].update(open=82, high=89, low=81, close=88)
    rows[3*3600//step+1]['high'] = 101
    return rows


class ShiftTests(unittest.TestCase):
    def review(self, data=None, day='2026-10-02', shift='day', step=300):
        return review_shift(fixture(day, shift=='night', step) if data is None else data, day, shift, step)

    def test_failed_8_promotes_9_and_later_setup_delivers(self):
        r = self.review()
        self.assertEqual(len(r['range_transitions']), 2)
        self.assertIn('11:00', r['active_anchor_ny'])
        self.assertEqual(r['range_transitions'][1]['reason'], 'opposing_objective_completed')
        self.assertIn('12:00', r['range_transitions'][1]['confirmed_at_ny'])
        self.assertFalse(r['range_transitions'][1]['crt_established_by_handoff'])
        self.assertIn('10:00', r['range_transitions'][0]['confirmed_at_ny'])
        later = r['ranges'][1]
        self.assertEqual(later['role'], 'selected_range')
        self.assertEqual(later['direction_observed'], 'bullish')
        self.assertEqual([x['status'] for x in later['objectives']], ['observed_after_purge']*2)
        self.assertEqual(later['objectives'][1]['level'], 100)
        self.assertFalse(later['entry_confirmed'])
        self.assertIn('10:10', later['m5_body_evidence'][0]['confirmed_at_ny'])
        self.assertEqual(r['hourly_progression'][1]['status'], 'sweep_and_close_back_inside')

    def test_repeat_purge_and_return_are_timestamped(self):
        data=fixture(); data[27]['low']=77
        r=self.review(data)['ranges'][1]
        detail=next(x for x in r['sweep_detail'] if x['side']=='sell')
        self.assertEqual(detail['excursions_in_available_bars'],2)
        self.assertIn('10:05',detail['first_source_close_back_inside_ny'])

    def test_second_failure_promotes_10_at_11(self):
        data=fixture(); data[35].update(low=75, close=76)
        r=self.review(data)
        self.assertEqual(len(r['range_transitions']), 2)
        self.assertIn('10:00', r['active_anchor_ny'])
        self.assertIn('11:00', r['ranges'][2]['selected_at_ny'])

    def test_valid_8_is_not_silently_replaced_by_9(self):
        data=fixture()
        for b in data[12:]: b.update(open=100, high=109, low=91, close=100)
        r=self.review(data)
        self.assertEqual(r['range_transitions'], [])
        self.assertIn('08:00', r['active_anchor_ny'])
        self.assertEqual(r['ranges'][1]['role'], 'independent_range_context')

    def test_inside_hour_can_be_followed_by_later_purge_of_8(self):
        data=fixture()
        for b in data[12:]: b.update(open=100, high=109, low=91, close=100)
        data[26]['high']=112
        r=self.review(data)
        self.assertEqual(r['hourly_progression'][0]['status'], 'inside_range')
        self.assertEqual(r['hourly_progression'][1]['status'], 'sweep_and_close_back_inside')
        self.assertEqual(r['ranges'][0]['direction_observed'], 'bearish')

    def test_missing_hour_blocks_selection_but_preserves_independent_evidence(self):
        data=fixture(); del data[15]
        r=self.review(data)
        self.assertFalse(r['progression_complete']); self.assertIsNone(r['active_anchor_ny'])
        self.assertEqual(r['range_transitions'], [])
        self.assertEqual(r['ranges'][1]['status'], 'insufficient_closed_candles')

    def test_partial_current_hour_does_not_confirm_close(self):
        r=self.review(fixture()[:18])
        self.assertEqual(r['range_transitions'], [])
        self.assertFalse(r['progression_complete'])
        self.assertFalse(r['coverage']['complete'])

    def test_empty_feed_is_unknown(self):
        r=self.review([])
        self.assertFalse(r['coverage']['complete'])
        self.assertTrue(all(x['status']=='insufficient_closed_candles' for x in r['ranges']))

    def test_target_before_later_invalidation_is_preserved(self):
        data=fixture(); data[-1].update(high=105, close=104)
        r=self.review(data)['ranges'][1]
        self.assertEqual(r['status'], 'invalidated_by_close')
        self.assertTrue(all(o['status']=='observed_after_purge' for o in r['objectives']))
        self.assertIn('12:00', r['invalidated_at_ny'])

    def test_same_bar_two_sided_sweep_does_not_invent_direction(self):
        data=fixture(); data[24]['high']=102
        r=self.review(data)['ranges'][1]
        self.assertIsNone(r['direction_observed'])
        self.assertEqual(r['m5_body_evidence'], [])
        self.assertFalse(r['entry_confirmed'])

    def test_same_bar_target_is_not_ordered_delivery(self):
        data=fixture(); data[24]['high']=100
        r=self.review(data)['ranges'][1]
        self.assertEqual(r['objectives'][1]['status'], 'same_bar_order_unknown')

    def test_no_events_after_invalidation(self):
        data=fixture()
        # 8 fails at 10, and its buy side is only purged at 11.
        data[36]['high']=120
        r=crt_review(data,data[0]['time'],data[0]['time']+14400,'H1',300)
        self.assertFalse(any(e['kind']=='buy_side_purge' for e in r['events']))

    def test_cutoff_excludes_noon_hour(self):
        data=fixture(); r=self.review(data)
        data.append(dict(time=data[0]['time']+14400,open=100,high=200,low=1,close=2))
        self.assertEqual(self.review(data),r)

    def test_night_midnight_and_dst_offsets(self):
        for day, offset in [('2026-10-02','-04:00'),('2026-12-02','-05:00')]:
            r=self.review(day=day,shift='night')
            self.assertTrue(r['end_ny'].endswith('T00:00:00'+offset))
            self.assertIn('23:00', r['active_anchor_ny'])
            self.assertEqual(r['range_transitions'][-1]['confirmed_at_ny'], r['end_ny'])
            self.assertFalse(r['range_transitions'][-1]['crt_established_by_handoff'])
            self.assertEqual(r['ranges'][1]['objectives'][1]['status'],'observed_after_purge')

    def test_m1_and_m5_agree_on_progression_and_body_cross(self):
        coarse=self.review()
        data=fixture(step=60)
        # Equivalent M5 OHLC for the first two bars of 10.
        for b in data[120:125]: b.update(open=85,high=87,low=78,close=82)
        for b in data[125:130]: b.update(open=82,high=89,low=81,close=88)
        fine=self.review(data,step=60)
        self.assertEqual(fine['range_transitions'], coarse['range_transitions'])
        self.assertEqual(fine['ranges'][1]['m5_body_evidence'],coarse['ranges'][1]['m5_body_evidence'])

    def test_session_and_voice_include_complete_story(self):
        r=session_review(fixture(),'2026-10-02','day')
        self.assertEqual(len(r['observations']),2)
        compact=compact_voice_tool_result('review_market_session', {'review':r})
        story=compact['review']['shift_story']
        for key in ('hourly_progression','range_transitions','active_anchor_ny','coverage','limits'):
            self.assertEqual(story[key],r['shift_story'][key])
        for actual, original in zip(story['ranges'], r['shift_story']['ranges']):
            for key in ('events','objectives','m5_body_evidence','sweep_detail','status','role','entry_confirmed'):
                self.assertEqual(actual[key], original[key])
            self.assertEqual(actual['observation_coverage']['complete'],original['observation_coverage']['complete'])
        self.assertIn('evidence',r['observations'][0])  # original payload unchanged
        self.assertNotIn('evidence',compact['review']['observations'][0])
        self.assertIn('evidence_ref',compact['review']['observations'][0])
        import json
        self.assertLess(len(json.dumps(compact)),len(json.dumps({'review':r})) * .65)


if __name__=='__main__': unittest.main()
