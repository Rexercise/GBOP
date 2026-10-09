"""Synthetic same-parent chronology; no member/private market records."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.market_data import ASSETS, attach_lifecycle, session_review
from gbop_voice_web.current_market import _range_fact
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from gbop_voice_web.voice_detail import _outcome_first
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS

START = parse_time('2026-06-11T07:00:00-04:00')


def sequence(start=START, scale=1, shift=0):
    def price(i):
        if i < 60: return (100, 110, 90, 104)
        if i < 89: return (104, 108, 102, 104)
        if i == 89: return (104, 112, 103, 109)
        if i == 90: return (109, 109, 99, 101)
        if i < 112: return (101, 103, 95, 98)
        if i == 112: return (98, 99, 88, 89)
        if i == 113: return (89, 96, 89, 94)
        if i < 126: return (96, 98, 92, 96)
        if i == 126: return (96, 103, 95, 101)
        if i < 169: return (102, 105, 99, 102)
        if i == 169: return (105, 111, 104, 109)
        if i == 171: return (109, 114, 109, 111)
        if i < 180: return (109, 111, 106, 109)
        if i < 191: return (109, 112, 107, 109)
        if i == 191: return (109, 110, 99, 101)
        if i < 200: return (109, 112, 107, 109)
        if i == 200: return (109, 115, 108, 112)
        if i == 201: return (112, 122, 111, 115)
        if i < 225: return (105, 110, 104, 105)
        if i < 240: return (103, 106, 102, 103)
        if i == 240: return (103, 104, 98, 102)
        if i < 254: return (102, 104, 101, 102)
        if i == 254: return (101, 102, 91, 95)
        if i == 255: return (95, 96, 87, 89)
        if i < 299: return (90, 95, 88, 90)
        return (90, 91, 86, 86)
    return [dict(time=start+i*60, **{k: v*scale+shift for k, v in
                zip(('open','high','low','close'), price(i))}) for i in range(300)]


def review(rows=None, minutes=300):
    rows = sequence() if rows is None else rows
    start = rows[0]['time']
    end = start + minutes*60
    return attach_lifecycle(crt_review(rows, start, end, 'H1', 60), rows, end, 60)


class YoungDeliveryTests(unittest.TestCase):
    def test_three_alternating_legs_and_later_invalidation(self):
        result = review()
        context = result['young_lefty_context']
        self.assertIsNone(context['selected_direction'])
        delivery = context['delivery_recap']
        self.assertEqual(delivery['original']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(delivery['original']['variant']['labels'][0]['code'], 'V2')
        double = delivery['double_purge']
        self.assertEqual(double['confirmed_at_ny'], '2026-06-11T09:00:00-04:00')
        self.assertEqual(double['reversal_thesis']['status'], 'original_side_delivered')
        third, = delivery['continuation']['legs']
        self.assertEqual(third['leg_index'], 3)
        self.assertEqual(third['direction'], 'bearish')
        self.assertEqual(third['confirmation']['known_at_ny'], '2026-06-11T10:00:00-04:00')
        self.assertEqual(third['status'], 'opposing_liquidity_delivered')
        self.assertEqual(third['objectives']['midpoint']['evidence']['bar_open_ny'], '2026-06-11T10:11:00-04:00')
        self.assertEqual(third['objectives']['opposing_liquidity']['evidence']['bar_open_ny'], '2026-06-11T11:15:00-04:00')
        self.assertEqual(delivery['continuation']['next_boundary']['confirmation_status'],
                         'not_confirmed_before_range_invalidation')
        self.assertEqual(result['invalidated_at_ny'], '2026-06-11T12:00:00-04:00')

    def test_midpoint_manner_is_frozen_before_later_v6(self):
        third = review()['young_lefty_context']['delivery_recap']['continuation']['legs'][0]
        self.assertEqual(third['variant']['code'], 'V6')
        self.assertEqual(third['variant']['known_at_ny'], '2026-06-11T11:00:00-04:00')
        self.assertNotIn('primary_code', third['objectives']['midpoint']['delivery_manner'])
        self.assertEqual(third['objectives']['opposing_liquidity']['delivery_manner']['primary_code'], 'V6')

    def test_recap_exposes_complete_chronology_without_profit_or_selection(self):
        result = review()
        text = result['recap']['spoken_summary']
        for token in ('initial bearish V2', 'Double purge developed simultaneously', 'Triple purge', '10:11 AM',
                      'later V6 re-soup', '11:15 AM', 'earlier delivery remains recorded',
                      'HTF trade direction and execution remain unselected'):
            self.assertIn(token, text)
        self.assertLess(text.index('10:11 AM'), text.index('later V6 re-soup'))
        self.assertNotIn('profit', text)
        self.assertEqual(_outcome_first(result)['directional_outcome']['spoken_summary'], text)

    def test_asof_confirmation_has_no_future_reverse_delivery(self):
        result = review(minutes=120)
        context = result['young_lefty_context']
        self.assertEqual(context['delivery_recap']['double_purge']['reversal_thesis']['status'], 'pending_at_review_cutoff')
        self.assertEqual(context['delivery_recap']['continuation']['legs'], [])
        self.assertNotIn('9:49', result['recap']['spoken_summary'])
        result = review(minutes=180)
        third = result['young_lefty_context']['delivery_recap']['continuation']['legs'][0]
        self.assertEqual(third['status'], 'pending_at_review_cutoff')
        self.assertIsNone(third['objectives']['midpoint']['evidence'])
        self.assertNotIn('variant', third)

    def test_before_h1_return_does_not_promote_lower_timeframe_development(self):
        result = review(minutes=115)
        self.assertNotIn('young_lefty_context', result)
        self.assertFalse(result['double_purge']['observed'])
        self.assertTrue(result['double_purge']['developing'])

    def test_same_source_dual_sweep_keeps_context_unselected(self):
        rows = sequence()
        rows[89].update(low=88)
        result = review(rows)
        context = result['young_lefty_context']
        self.assertNotIn('delivery_recap', context)
        self.assertFalse(result['double_purge']['observed'])
        self.assertIn('HTF/narrative-dependent', result['recap']['spoken_summary'])

    def test_missing_sequence_bars_blocks_confirmed_recap(self):
        for missing in (61, 100, 113):
            with self.subTest(missing=missing):
                rows = sequence(); del rows[missing]
                self.assertFalse(review(rows)['double_purge']['observed'])

    def test_gap_before_third_return_blocks_continuation_not_first_two_legs(self):
        rows = sequence(); del rows[175]
        result = review(rows)
        delivery = result['young_lefty_context']['delivery_recap']
        self.assertEqual(delivery['original']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(delivery['double_purge']['reversal_thesis']['status'], 'original_side_delivered')
        self.assertEqual(delivery['continuation']['legs'], [])
        self.assertIn('unverified', delivery['continuation']['next_boundary']['confirmation_status'])

    def test_later_gap_cannot_erase_completed_third_delivery(self):
        rows = sequence(); del rows[270]
        third = review(rows)['young_lefty_context']['delivery_recap']['continuation']['legs'][0]
        self.assertEqual(third['status'], 'opposing_liquidity_delivered')
        self.assertFalse(third['coverage_complete'])

    def test_boundary_touch_without_strict_purge_never_adds_leg(self):
        rows = sequence()
        for b in rows[169:]:
            b['high'] = min(b['high'], 110); b['open'] = min(b['open'],110); b['close'] = min(b['close'],110)
        delivery = review(rows)['young_lefty_context']['delivery_recap']
        self.assertEqual(delivery['double_purge']['reversal_thesis']['status'], 'original_side_delivered')
        self.assertEqual(delivery['continuation']['legs'], [])

    def test_all_asset_price_scales_and_night_symmetry(self):
        for index, asset in enumerate(sorted(ASSETS)):
            for hours in (0, 12):
                with self.subTest(asset=asset, hours=hours):
                    rows = sequence(START+hours*3600, scale=(index+1)/100, shift=index*1000)
                    result = review(rows)
                    delivery = result['young_lefty_context']['delivery_recap']
                    self.assertEqual(delivery['continuation']['legs'][0]['status'], 'opposing_liquidity_delivered')
                    self.assertIsNone(result['young_lefty_context']['selected_direction'])

    def test_current_followup_preserves_delivery_and_no_selected_thesis(self):
        result = review()
        result['anchor'].update(forming=False, status='closed', observed_through_ny='2026-06-11T08:00:00-04:00')
        fact = _range_fact(result,'selected_range','Young Lefty','NAS100',START+18000,'H1','M5')
        retained = MarketConversation._evidence('review_current_market',
            {'review': {'ranges':[fact], 'mode':'current_market'}}, {'asset':'NAS100'})
        saved = retained['range_outcomes'][0]
        self.assertEqual(saved['direction_scope'], 'observed_price_path_not_selected_thesis')
        self.assertEqual(saved['outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(saved['young_lefty_context']['delivery_recap']['continuation']['legs'][0]['leg_index'], 3)
        self.assertIsNone(saved['young_lefty_context']['selected_direction'])

    def test_shift_leads_with_acting_parent_role_and_keeps_independent_detail(self):
        session = session_review(sequence(),'2026-06-11','day',60)
        synopsis = build_shift_synopsis(session,'NAS100')
        text = synopsis['spoken_summary']
        self.assertIn('10:00 AM H1 candle souped buy-side', text)
        self.assertNotIn('Independent 10:00 AM H1 range: bullish', text)
        self.assertIn('simultaneous', text)
        self.assertIn('9ate8', text)
        self.assertIn('V1 Textbook', text)
        ten = next(r for r in synopsis['ranges'] if r['anchor_start_ny']=='2026-06-11T10:00:00-04:00')
        self.assertEqual(ten['outcome'],'failed_before_objectives')
        self.assertEqual(ten['presentation_priority'],'parent_role_before_secondary_own_range')

    def test_full_shift_voice_remains_within_unchanged_budget(self):
        session = session_review(sequence(),'2026-06-11','day',60)
        before = deepcopy(session)
        packet = voice_tool_payload('review_market_session', {'ok':True,'asset':'NAS100','review':session})
        self.assertEqual(session,before)
        self.assertTrue(packet['ok'], packet.get('status'))
        self.assertLessEqual(len(json.dumps(packet,separators=(',',':'))),SHIFT_SYNOPSIS_TARGET_CHARS)
        self.assertIn('Triple purge', packet['review']['shift_synopsis']['spoken_summary'])

class DeliveryBoundaryRegressions(unittest.TestCase):
    def test_subsequent_purges_apply_to_other_parent_hours_too(self):
        for hours in (1, 2, 13, 14):
            rows = sequence(START + hours*3600)
            result = review(rows)
            self.assertNotIn('young_lefty_context', result)
            third = result['double_purge']['continuation']['legs'][0]
            self.assertEqual(third['status'], 'opposing_liquidity_delivered')
            self.assertIn('triple purge', result['recap']['spoken_summary'])
            self.assertIn('triple purge', _outcome_first(result)['directional_outcome']['spoken_summary'])

    def test_v6_midpoint_manner_uses_leg_direction_on_both_sides(self):
        from gbop_voice_web.range_delivery_sequence import _resoup_manner
        rows = sequence()
        result = review(rows)
        third = result['young_lefty_context']['delivery_recap']['continuation']['legs'][0]
        target = deepcopy(third['objectives']['opposing_liquidity'])
        target['liquidity_side'] = '50% (midpoint)'
        for mirrored in (False, True):
            source = rows
            anchor = result['anchor']
            confirmation = third['confirmation']
            direction = 'bearish'
            if mirrored:
                source = [{**b, 'open':200-b['open'], 'high':200-b['low'],
                           'low':200-b['high'], 'close':200-b['close']} for b in rows]
                anchor = {**anchor, 'high':200-anchor['low'], 'low':200-anchor['high']}
                confirmation = {**confirmation, 'close':200-confirmation['close']}
                direction = 'bullish'
            self.assertEqual(_resoup_manner(anchor, source, confirmation, target, direction,60)['code'],'V6')

    def test_acting_parent_role_does_not_bridge_a_missing_validity_prefix(self):
        rows = sequence(); del rows[175]
        synopsis = build_shift_synopsis(session_review(rows,'2026-06-11','day',60),'NAS100')
        self.assertNotIn('10:00 AM H1 candle souped', synopsis['spoken_summary'])

    def test_post_shift_one_candle_v2_delivery_precedes_h1_confirmation(self):
        for hours in (0,12):
            start = START+(4+hours)*3600
            rows = [dict(time=start+i*60,open=100,high=110 if i<60 else 108,
                         low=90 if i<60 else 92,close=100) for i in range(120)]
            rows[61].update(open=95,high=97,low=88,close=89)
            for i in range(62,70): rows[i].update(open=89,high=90,low=88,close=89)
            rows[70].update(open=89,high=96,low=89,close=94)
            rows[77].update(high=111)
            for cutoff in (start+78*60,start+120*60):
                result = attach_lifecycle(crt_review(rows,start,cutoff,'H1',60),rows,cutoff,60)
                self.assertEqual(result['directional_outcome']['status'],'opposing_liquidity_delivered')
                milestone = result['variant_evidence']['delivery_milestones']['opposing_liquidity']
                self.assertEqual(milestone['manner']['primary_code'],'V2')
                self.assertEqual(parse_time(milestone['source_interval']['bar_open_ny']),start+77*60)
                if cutoff < start+120*60:
                    result['anchor'].update(forming=False, status='closed', observed_through_ny=result['anchor']['end_ny'])
                    fact = _range_fact(result,'selected_range',None,'NAS100',cutoff,'H1','M5')
                    self.assertEqual(fact['opposing_liquidity']['delivery_manner']['primary_code'],'V2')
                    self.assertEqual(fact['variant']['labels'],[])
                    self.assertEqual(result['variant_evidence']['labels'],[])
                    self.assertFalse(result['double_purge']['observed'])
                else:
                    self.assertEqual(result['variant_evidence']['labels'][0]['code'],'V2')
                    self.assertEqual(parse_time(result['double_purge']['confirmed_at_ny']),start+120*60)


class ContinuationDevelopmentTests(unittest.TestCase):
    def test_earlier_physical_delivery_never_becomes_post_confirmation_delivery(self):
        rows = sequence()
        rows[175].update(low=89)
        for bar in rows[180:299]:
            bar['low'] = max(bar['low'],91)
            bar['open'] = max(bar['open'],91)
            bar['close'] = max(bar['close'],91)
        result = review(rows)
        third = result['double_purge']['continuation']['legs'][0]
        development = third['pre_confirmation_development']
        self.assertEqual(development['status'],'pre_confirmation_only')
        self.assertEqual(development['objectives']['original_side']['status'],'observed_after_return')
        self.assertNotEqual(third['status'],'opposing_liquidity_delivered')
        self.assertIn('Before own-timeframe confirmation, physical delivery',result['recap']['spoken_summary'])
        self.assertIn('separate from post-confirmation delivery',result['recap']['spoken_summary'])


if __name__ == '__main__':
    unittest.main()
