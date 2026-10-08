"""Synthetic Oct 7 event-time regression; no live/account data or model calls.

Prices are invented. Timing mirrors the verified closed-M1 sequence: 9:37
sell-side purge, 9:58 midpoint, 10:25 full delivery, later 11 AM H1 invalidation.
"""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import parse_time, crt_review
from gbop_voice_web.market_data import session_review, attach_lifecycle
from gbop_voice_web.shift_narrative import directional_outcome
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from gbop_voice_web.voice_payload import voice_tool_payload
from gbop_voice_web.voice_detail import crt_voice_detail
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from test_voice_payload_budget import expanded

DAY = '2026-10-07'


def ny(clock):
    return DAY + 'T' + clock + ':00-04:00'


def synthetic_sequence():
    start = parse_time(ny('07:00'))
    hours = [(100, 130, 70, 100), (100, 110, 90, 100),
             (95, 99, 92, 95), (104, 109, 101, 104), (108, 109, 104, 108)]
    bars = [dict(time=start + hour * 3600 + minute * 60,
                 open=o, high=h, low=l, close=c)
            for hour, (o, h, l, c) in enumerate(hours) for minute in range(60)]
    index = {bar['time']: bar for bar in bars}
    for minute in range(37, 54):
        index[parse_time(ny(f'09:{minute:02}'))].update(open=89, high=89, low=88, close=89)
    index[parse_time(ny('09:54'))].update(open=89, high=95, low=89, close=95)
    index[parse_time(ny('09:58'))]['high'] = 101
    index[parse_time(ny('10:25'))]['high'] = 111
    index[parse_time(ny('11:13'))]['high'] = 112
    index[parse_time(ny('11:59'))].update(high=116, close=115)
    return bars


def review(bars=None):
    return session_review(synthetic_sequence() if bars is None else bars, DAY, 'day', 60)


def detail_result(bars=None):
    bars = synthetic_sequence() if bars is None else bars
    start, end = parse_time(ny('08:00')), parse_time(ny('12:00'))
    source = attach_lifecycle(crt_review(bars, start, end, 'H1', 60), bars, end, 60)
    return {'ok': True, 'asset': 'NAS100', 'review': source,
            'voice_detail_selection': {'through_ny': ny('12:00')}}


class OutcomeFirstNarrationTests(unittest.TestCase):
    def test_verified_v1_completion_survives_later_structural_invalidation(self):
        source = review()
        row = source['shift_story']['ranges'][0]
        self.assertEqual(row['status'], 'invalidated_by_close')
        self.assertEqual(row['invalidated_at_ny'], ny('12:00'))
        self.assertEqual([v['code'] for v in row['variant_evidence']['labels']], ['V1'])
        outcome = directional_outcome(row)
        self.assertEqual(outcome['status'], 'opposing_liquidity_delivered')
        self.assertTrue(outcome['delivery_before_later_invalidation'])
        self.assertEqual(outcome['midpoint']['evidence']['bar_open_ny'], ny('09:58'))
        self.assertEqual(outcome['opposing_liquidity']['evidence']['bar_open_ny'], ny('10:25'))
        before = deepcopy(source)
        wire = voice_tool_payload('review_market_session', {'ok': True, 'asset': 'NAS100', 'review': source})
        self.assertTrue(wire['ok'])
        self.assertLessEqual(len(json.dumps(wire, separators=(',', ':'))), 12000)
        synopsis = wire['review']['shift_synopsis']
        lead = synopsis['ranges'][0]
        self.assertEqual((lead['verdict'], lead['outcome']), ('delivered', 'opposing_liquidity_delivered'))
        text = synopsis['spoken_summary']
        self.assertLess(text.index('buy-side delivered'), text.index('later invalidated'))
        self.assertLess(text.index('V1 Textbook'), text.index('later invalidated'))
        self.assertNotIn('failed bullish', text.split('Young Lefty')[0])
        self.assertEqual(source, before)

    def test_detail_projects_outcome_and_verified_variant_before_terminal_status(self):
        raw = detail_result()
        before = deepcopy(raw)
        wire = crt_voice_detail(raw)
        self.assertTrue(wire['ok'])
        self.assertEqual(next(iter(wire['review'])), 'directional_outcome')
        page = expanded(wire, wire)
        self.assertEqual(page['review']['status'], 'invalidated_by_close')
        lead = page['review']['directional_outcome']['spoken_summary']
        self.assertIn('completed its bullish buy-side objective', lead)
        self.assertLess(lead.index('V1 Textbook'), lead.index('later invalidated'))
        self.assertEqual(page['review']['variant_evidence'], raw['review']['variant_evidence'])
        self.assertEqual(raw, before)

    def test_full_delivery_manner_leads_before_later_structural_classification_close(self):
        bars=synthetic_sequence()
        end=parse_time(ny('10:26'))
        source=attach_lifecycle(crt_review(bars,parse_time(ny('08:00')),end,'H1',60),bars,end,60)
        self.assertEqual(source['variant_evidence']['labels'],[])
        self.assertEqual(source['variant_evidence']['delivery_milestones']['opposing_liquidity']['manner']['primary_code'],'V1')
        raw={'ok':True,'asset':'NAS100','review':source,'voice_detail_selection':{'through_ny':ny('10:26')}}
        before=deepcopy(raw)
        wire=crt_voice_detail(raw)
        self.assertTrue(wire['ok'])
        page=expanded(wire,wire)
        lead=page['review']['directional_outcome']['spoken_summary']
        self.assertIn('V1 Textbook manner',lead)
        self.assertEqual(page['review']['variant_evidence']['labels'],[])
        self.assertEqual(raw,before)

    def test_secondary_projection_keeps_full_selected_lifecycle_and_resolvable_refs(self):
        raw = detail_result()
        full = compact_voice_tool_result('review_market_crt', raw)
        # Synthetic envelope growth triggers the last bounded projection without
        # fabricating or changing any market evidence to force the regression.
        match = None
        for padding in range(8000, 16001, 100):
            raw['synthetic_transport_metadata'] = 'x' * padding
            wire = crt_voice_detail(raw)
            if wire.get('voice_detail_page', {}).get('secondary_reversal_detail_omitted'):
                match = wire
                break
        self.assertIsNotNone(match)
        self.assertTrue(match['ok'])
        self.assertLessEqual(len(json.dumps(match, separators=(',', ':'))), 32000)
        page = expanded(match, match)
        self.assertEqual(page['review']['candle_lifecycle']['purge_candles'],
                         full['review']['candle_lifecycle']['purge_candles'][:1])
        self.assertEqual(page['review']['events'], full['review']['events'])
        double = page['review']['double_purge']
        original = raw['review']['double_purge']
        self.assertEqual(double['confirmed_at_ny'], original['confirmed_at_ny'])
        self.assertEqual(double['reversal_thesis']['status'], original['reversal_thesis']['status'])
        self.assertEqual(double['reversal_development']['status'], original['reversal_development']['status'])
        self.assertIn('double_purge_detail_request', double)
        self.assertIn('not absence', double['detail_omissions'])

    def test_detail_overflow_retains_bounded_verified_parent_outcome_without_claiming_full_detail(self):
        raw = detail_result()
        raw['synthetic_transport_metadata'] = 'x' * 50000
        before = deepcopy(raw)
        wire = crt_voice_detail(raw)
        self.assertFalse(wire['ok'])
        self.assertEqual(wire['status'], 'voice_detail_budget_exceeded')
        self.assertNotIn('review', wire)
        summary = wire['range_outcome_summary']
        self.assertEqual(summary['status'], 'opposing_liquidity_delivered')
        self.assertEqual(summary['variant']['labels'], raw['review']['variant_evidence']['labels'])
        self.assertTrue(summary['delivery_before_later_invalidation'])
        self.assertIn('lifecycle is omitted', summary['detail_scope'])
        self.assertLessEqual(len(json.dumps(wire, separators=(',', ':'))), 32000)
        self.assertEqual(raw, before)

    def test_partial_midpoint_manner_is_preserved_separately_from_final_variant(self):
        # Projection-only supplied evidence: the classifier owns qualification.
        raw = detail_result()
        variant = raw['review']['variant_evidence']
        variant['labels'] = [{'code': 'V3', 'name': 'extended distribution'},
                             {'code': 'V6', 'name': 're-soup'}]
        variant['delivery_milestones'] = {
            'midpoint': {'status': 'observed_after_purge', 'is_full_completion': False,
                         'known_at_ny': ny('09:59'), 'manner': {'primary_code': 'V2'}},
            'opposing_liquidity': {'status': 'observed_after_purge', 'is_full_completion': True,
                                  'known_at_ny': ny('10:26'), 'manner': {'primary_code': 'V6'}}}
        before = deepcopy(raw)
        wire = crt_voice_detail(raw)
        page = expanded(wire, wire)
        lead = page['review']['directional_outcome']['spoken_summary']
        self.assertLess(lead.index('V6 re-soup'), lead.index('V3 extended distribution'))
        self.assertEqual(page['review']['variant_evidence']['delivery_milestones'],
                         variant['delivery_milestones'])
        raw['synthetic_transport_metadata'] = 'x' * 50000
        overflow = crt_voice_detail(raw)
        self.assertEqual(overflow['range_outcome_summary']['variant']['delivery_milestones'],
                         variant['delivery_milestones'])
        raw.pop('synthetic_transport_metadata')
        self.assertEqual(raw, before)

    def test_failed_or_unverified_overflow_cannot_inherit_a_success_headline(self):
        cases = []
        earlier = synthetic_sequence()
        next(b for b in earlier if b['time'] == parse_time(ny('09:59'))).update(low=85, close=85)
        cases.append(earlier)
        cases.append([b for b in synthetic_sequence() if b['time'] != parse_time(ny('10:10'))])
        same_bar = synthetic_sequence()
        next(b for b in same_bar if b['time'] == parse_time(ny('09:37'))).update(high=111)
        cases.append(same_bar)
        for bars in cases:
            with self.subTest(kind=bars[0]['time'], count=len(bars)):
                raw = detail_result(bars)
                raw['synthetic_transport_metadata'] = 'x' * 50000
                wire = crt_voice_detail(raw)
                self.assertFalse(wire['ok'])
                summary = wire['range_outcome_summary']
                self.assertNotEqual(summary['status'], 'opposing_liquidity_delivered')
                self.assertNotIn('completed its', summary['spoken_summary'])
                self.assertFalse(summary['delivery_before_later_invalidation'])

    def test_target_after_invalidation_never_becomes_completed_delivery(self):
        bars = synthetic_sequence()
        next(b for b in bars if b['time'] == parse_time(ny('09:59'))).update(low=85, close=85)
        source = review(bars)
        row = source['shift_story']['ranges'][0]
        outcome = directional_outcome(row)
        self.assertEqual(row['invalidated_at_ny'], ny('10:00'))
        self.assertNotEqual(outcome['status'], 'opposing_liquidity_delivered')
        self.assertFalse(outcome['delivery_before_later_invalidation'])
        self.assertNotIn('completed its', outcome['spoken_summary'])
        self.assertNotEqual(build_shift_synopsis(source)['ranges'][0]['verdict'], 'delivered')

    def test_purge_and_target_in_one_source_bar_keeps_order_unknown(self):
        bars = synthetic_sequence()
        next(b for b in bars if b['time'] == parse_time(ny('09:37'))).update(high=111)
        row = review(bars)['shift_story']['ranges'][0]
        outcome = directional_outcome(row)
        self.assertEqual(outcome['status'], 'unverified')
        self.assertFalse(outcome['delivery_before_later_invalidation'])
        self.assertNotIn('completed its', outcome['spoken_summary'])
        self.assertEqual(row['variant_evidence']['labels'], [])

    def test_target_in_invalidating_source_bar_is_not_verified_success(self):
        row = deepcopy(review()['shift_story']['ranges'][0])
        full = next(o for o in row['objectives'] if o['objective'] == 'opposing_liquidity')
        full['evidence']['bar_open_ny'] = ny('11:59')
        full['evidence']['bar_close_ny'] = ny('12:00')
        outcome = directional_outcome(row)
        self.assertEqual(outcome['opposing_liquidity']['status'], 'touch_in_invalidating_bar_order_unresolved')
        self.assertFalse(outcome['delivery_before_later_invalidation'])
        self.assertNotIn('completed its', outcome['spoken_summary'])

    def test_gap_before_delivery_remains_unverified(self):
        bars = [b for b in synthetic_sequence() if b['time'] != parse_time(ny('10:10'))]
        source = review(bars)
        row = source['shift_story']['ranges'][0]
        outcome = directional_outcome(row)
        self.assertNotEqual(outcome['status'], 'opposing_liquidity_delivered')
        self.assertNotIn('completed its', outcome['spoken_summary'])
        self.assertEqual(row['variant_evidence']['labels'], [])
        synopsis = build_shift_synopsis(source)
        self.assertIn('Missing or unfinished', synopsis['spoken_summary'])
        self.assertNotEqual(synopsis['ranges'][0]['verdict'], 'delivered')


if __name__ == '__main__':
    unittest.main()
