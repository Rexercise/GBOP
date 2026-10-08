"""Synthetic chronological main-recap regressions; no broker or member rows."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_data import session_review
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.shift_synopsis import build_shift_synopsis, build_other_ranges, _local_fact, _shared_opening_evidence, _sentence
from gbop_voice_web.voice_payload import voice_tool_payload
from test_chronological_transport import expand
from test_delivery_milestones import sample, review as milestone_review


def ny(clock):
    return '2026-10-05T' + clock + ':00-04:00'


def synthetic_review(*, night=False, through=None):
    start = parse_time(ny('07:00'))
    bars = [dict(time=t, open=100 if t < start + 7200 else 96,
                 high=101 if t < start + 7200 else 97,
                 low=99 if t < start + 7200 else 95,
                 close=100 if t < start + 7200 else 96)
            for t in range(start, start + 18000, 60)]

    def bar(clock, **values):
        t = parse_time(ny(clock))
        next(b for b in bars if b['time'] == t).update(values)

    bar('07:00', high=120, low=90)
    bar('08:00', high=110, low=95)
    bar('08:10', low=85)
    bar('08:37', high=107)
    for minute in range(35, 40):
        bar(f'09:{minute}', open=100 if minute == 35 else 84,
            high=102 if minute == 35 else 86, low=80, close=84)
    bar('09:40', open=89, high=100, low=86, close=95)
    bar('09:45', open=96, high=98, low=79, close=84)
    for minute in range(20, 25):
        bar(f'10:{minute}', open=96 if minute == 20 else 103,
            high=104, low=95 if minute == 20 else 102, close=103)
    bar('10:25', open=103, high=112, low=102, close=106)
    for minute in range(60):
        price = 106 if minute < 13 else 115
        bar(f'11:{minute:02}', open=price, high=price + 1, low=price - 1, close=price)
    bar('11:13', high=121)
    if through:
        bars = [b for b in bars if b['time'] + 60 <= parse_time(ny(through))]
    if night:
        bars = [{**b, 'time': b['time'] + 12 * 3600} for b in bars]
    return session_review(bars, '2026-10-05', 'night' if night else 'day', 60)


def opening_rows(review):
    opening = review['shift_story']['ranges'][0]
    young = next(o['evidence'] for o in review['observations'] if o['play'] == 'Young Lefty')
    return opening, young


def shared(review, cutoff=None):
    opening, young = opening_rows(review)
    return _shared_opening_evidence(opening, young, _local_fact(opening, '9ate8'),
        _local_fact(young, 'Young Lefty'), cutoff or review['shift_story']['end_ny'])


class ChronologicalMainRecapTests(unittest.TestCase):
    def test_exact_shared_body_soup_cisd_keeps_each_parent_delivery(self):
        review = synthetic_review()
        before = deepcopy(review)
        synopsis = build_shift_synopsis(review, 'NAS100')
        support = synopsis['shared_setup_evidence']
        self.assertEqual(support['parent_anchors_ny'], [ny('07:00'), ny('08:00')])
        self.assertEqual(support['model1']['bar_open_ny'], ny('09:35'))
        self.assertEqual(support['super_soup']['bar_open_ny'], ny('09:45'))
        self.assertEqual(support['super_soup']['preceding_inside_bar_ny'], ny('09:40'))
        self.assertEqual(support['strict_CISD']['bar_open_ny'], ny('10:20'))
        self.assertEqual(support['strict_CISD']['confirmed_at_ny'], ny('10:25'))
        self.assertFalse(support['member_execution_inferred'])
        lead, young = synopsis['ranges'][:2]
        self.assertEqual(lead['opposing_liquidity']['source_interval']['bar_open_ny'], ny('10:25'))
        self.assertEqual(young['opposing_liquidity']['source_interval']['bar_open_ny'], ny('11:13'))
        self.assertNotEqual(lead['opposing_liquidity']['level'], young['opposing_liquidity']['level'])
        self.assertEqual(young['midpoint']['delivery_manner']['primary_code'], 'V2')
        self.assertEqual(young['variant']['primary_code'], 'V6')
        text = synopsis['spoken_summary']
        self.assertTrue(text.startswith('Young Lefty (7:00 AM'))
        self.assertLess(text.index('11:13 AM'), text.index('9ate8 (8:00 AM'))
        self.assertLess(text.index('9ate8 (8:00 AM'), text.index('shared the 9:35 AM M5'))
        self.assertIn('V6 re-soup (V3 extended distribution timing)', text)
        self.assertIn('each parent keeps its own targets and delivery', text)
        self.assertEqual(review, before)

    def test_same_direction_without_exact_body_identity_never_joins(self):
        for key, changed in (('open', 99), ('timeframe', 'M15'),
                             ('bar_open_ny', ny('09:30')), ('source_resolution_seconds', 300)):
            with self.subTest(key=key):
                review = synthetic_review()
                _, young = opening_rows(review)
                # A consistent alternate candle within one parent is still a
                # different identity, even if its direction matches the other.
                young['model1']['candles'][0][key] = changed
                young['candle_lifecycle']['purge_candles'][0][key] = changed
                self.assertIsNone(shared(review))

    def test_missing_or_wrong_parent_link_and_prefix_do_not_join(self):
        for mutation in ('parent', 'complete', 'prefix'):
            with self.subTest(mutation=mutation):
                review = synthetic_review()
                _, young = opening_rows(review)
                if mutation == 'parent':
                    young['model1']['candles'][0]['purged_range_start_ny'] = ny('08:00')
                elif mutation == 'complete':
                    young['model1']['candles'][0]['complete'] = False
                else:
                    young['context_qualification']['evidence_through_ny'] = ny('09:30')
                self.assertIsNone(shared(review))

    def test_cutoff_never_leaks_later_shared_confirmations(self):
        review = synthetic_review()
        self.assertIsNone(shared(review, ny('09:39')))
        body = shared(review, ny('09:40'))
        self.assertNotIn('super_soup', body)
        self.assertNotIn('strict_CISD', body)
        soup = shared(review, ny('09:50'))
        self.assertIn('super_soup', soup)
        self.assertNotIn('strict_CISD', soup)
        self.assertIn('strict_CISD', shared(review, ny('10:25')))

    def test_retired_parent_does_not_share_later_support(self):
        review = synthetic_review()
        opening, young = opening_rows(review)
        lead, yl = _local_fact(opening, '9ate8'), _local_fact(young, 'Young Lefty')
        yl['invalidated_at_ny'] = ny('09:45')
        result = _shared_opening_evidence(opening, young, lead, yl, ny('12:00'))
        self.assertIn('model1', result)
        self.assertNotIn('super_soup', result)
        self.assertNotIn('strict_CISD', result)
        yl['invalidated_at_ny'] = ny('09:40')
        self.assertIsNone(_shared_opening_evidence(opening, young, lead, yl, ny('12:00')))
        yl['invalidated_at_ny'] = None
        lead['paired_setup'] = {'start_ny': ny('09:00')}
        self.assertIsNone(_shared_opening_evidence(opening, young, lead, yl, ny('12:00')))

    def test_failed_secondary_mechanics_are_brief_but_retrievable(self):
        review = synthetic_review()
        synopsis = build_shift_synopsis(review, 'NAS100')
        nine = next(f for f in synopsis['ranges'] if f['anchor_start_ny'] == ny('09:00'))
        self.assertEqual(nine['verdict'], 'failed')
        self.assertIn('first_purge_interval', nine)
        self.assertNotIn('formed a bearish context countertrend', synopsis['spoken_summary'])
        self.assertGreater(synopsis['chronological_context']['failed_secondary_relationships_omitted_count'], 0)
        self.assertTrue(review['shift_story']['chronological_context']['relationships'])
        target = next(i for i in synopsis['range_index'] if i['anchor_start_ny'] == ny('09:00'))
        self.assertEqual(target['detail_request']['args']['through_ny'], ny('12:00'))
        other = build_other_ranges(review, 'NAS100', discussed=[ny('07:00'), ny('08:00')])
        self.assertIn(ny('09:00'), [f['anchor_start_ny'] for f in other['ranges']])
        self.assertIn('No later evidence before the end', synopsis['spoken_summary'])
        self.assertIn('Pending full DOL', synopsis['spoken_summary'])

    def test_midpoint_only_structure_is_not_final_completion(self):
        fact = _local_fact(milestone_review(sample(), '11:00'))
        self.assertEqual(fact['variant']['status'], 'structure_observed')
        self.assertEqual(fact['outcome'], 'midpoint_only')
        self.assertEqual(fact['midpoint']['delivery_manner']['primary_code'], 'V2')
        self.assertNotIn('delivery_manner', fact['opposing_liquidity'])
        self.assertNotEqual(fact['verdict'], 'delivered')
        self.assertNotIn('completed in', _sentence(fact))

    def test_early_full_manner_precedes_pending_structural_confirmation(self):
        # Full source delivery is known before the enclosing H1 closes. Its
        # objective manner must not be reduced to a pending structural label.
        fact = _local_fact(milestone_review(sample(), '11:10'))
        before = deepcopy(fact)
        self.assertEqual(fact['outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(fact['opposing_liquidity']['delivery_manner']['primary_code'], 'V6')
        text = _sentence(fact)
        self.assertIn('completed in V6 re-soup manner', text)
        self.assertLess(text.index('completed in'), text.index('structural confirmation pending'))
        self.assertNotIn('opposing-liquidity delivery remains pending', text)
        self.assertNotIn('sell-side pending', text)
        self.assertEqual(fact, before)

        # Also cover the real engine's partial-session V1 candidate shape,
        # where there is no completed structural label yet.
        partial = synthetic_review(through='10:26')
        fact = build_shift_synopsis(partial, 'NAS100')['ranges'][0]
        self.assertEqual(fact['variant']['labels'], [])
        self.assertEqual(fact['opposing_liquidity']['delivery_manner']['primary_code'], 'V1')
        text = _sentence(fact)
        self.assertIn('completed in V1 Textbook manner', text)
        self.assertIn('structural confirmation pending: Complete 10:00 AM H1 evidence', text)
        self.assertNotIn('V1 Textbook pending', text)

    def test_night_clock_and_bounded_scoped_payload_round_trip(self):
        for night in (False, True):
            with self.subTest(night=night):
                review = synthetic_review(night=night)
                context = MarketConversation()
                context.begin_turn()
                source = context.run('review_market_session', dict(asset='NAS100',
                    date_ny='2026-10-05', shift='night' if night else 'day'),
                    lambda name, args: {'ok': True, 'asset': 'NAS100', 'review': deepcopy(review)})
                before = deepcopy(source)
                wire = voice_tool_payload('review_market_session', source)
                self.assertTrue(wire['ok'], wire)
                self.assertLessEqual(len(json.dumps(wire, separators=(',', ':'))), 12000)
                actual = expand(wire)['review']['shift_synopsis']
                expected = build_shift_synopsis(review, 'NAS100')
                for key in ('ranges', 'shared_setup_evidence', 'chronological_context', 'shift_end', 'spoken_summary'):
                    self.assertEqual(actual[key], expected[key], key)
                if night:
                    self.assertTrue(actual['spoken_summary'].startswith('Young Lefty (7:00 PM'))
                    self.assertIn('9:35 PM M5 Model 1', actual['spoken_summary'])
                    self.assertIn('12:00 AM New York', actual['spoken_summary'])
                self.assertEqual(source, before)


if __name__ == '__main__':
    unittest.main()
