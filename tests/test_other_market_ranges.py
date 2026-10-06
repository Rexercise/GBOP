"""Actual market replay plus synthetic conversation receipts; no member data/ASR."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
import threading

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.market_data import MARKET_TOOLS, session_review
from gbop_voice_web.market_prefetch import prefetch_market_evidence
from gbop_voice_web.shift_synopsis import build_other_ranges
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS
from test_voice_payload_budget import expanded


DAY = {'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day'}
BARS = json.loads((Path(__file__).parent / 'fixtures/market_replays/nas100_2026_10_02_0700_1200_m1.json').read_text())['bars']
INITIAL = '9ate8 failed before either objective. Young Lefty also failed before either objective.'


def ny(hour, day='2026-10-02'):
    return f'{day}T{hour}:00-04:00'


def provider(name, args):
    if name != 'review_market_session':
        raise AssertionError(name)
    offset = (parse_time(ny('07:00', args['date_ny'])) - parse_time(ny('07:00'))
              + (12 * 3600 if args['shift'] == 'night' else 0))
    bars = [{**bar, 'time': bar['time'] + offset} for bar in BARS]
    return {'ok': True, 'asset': args['asset'],
            'review': session_review(bars, args['date_ny'], args['shift'], 60)}


class OtherRangeReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = provider('review_market_session', DAY)['review']

    def test_actual_failed_nineateeight_and_early_young_lefty_remain_initially_relevant(self):
        synopsis = self.review['shift_synopsis']
        named = {row['play']: row for row in synopsis['ranges'] if row['play']}
        for play in ('9ate8', 'Young Lefty'):
            self.assertEqual(named[play]['verdict'], 'failed')
            self.assertEqual(named[play]['outcome'], 'failed_before_objectives')
            self.assertIn(play, synopsis['spoken_summary'])
        self.assertEqual(named['Young Lefty']['first_purge_interval']['bar_open_ny'], ny('08:30'))
        self.assertEqual(named['Young Lefty']['invalidated_at_ny'], ny('09:00'))
        young = next(row['evidence'] for row in self.review['observations'] if row['play'] == 'Young Lefty')
        self.assertEqual(young['anchor']['high'], 30772.83)
        purge = next(row for row in young['events'] if row['kind'] == 'buy_side_purge')
        self.assertEqual(purge['bar_open_ny'], ny('08:30'))
        self.assertEqual(purge['level'], 30772.83)
        self.assertEqual(purge['observed_price'], 30875.98)
        self.assertEqual(named['9ate8']['invalidated_at_ny'], ny('10:00'))

    def test_other_ranges_keep_unbranded_nine_ten_failure_and_eleven_cutoff(self):
        before = deepcopy(self.review)
        result = build_other_ranges(self.review, 'NAS100', [ny('07:00'), ny('08:00')])
        self.assertEqual(self.review, before)
        nine, ten, eleven = result['ranges']
        self.assertEqual([row['anchor_start_ny'] for row in result['ranges']], [ny('09:00'), ny('10:00'), ny('11:00')])
        self.assertTrue(all(row['play'] is None for row in result['ranges']))
        self.assertEqual((nine['direction'], nine['outcome']), ('bearish', 'opposing_liquidity_delivered'))
        self.assertEqual([item['code'] for item in nine['variant']['labels']], ['V1'])
        self.assertEqual(nine['midpoint']['source_interval']['bar_open_ny'], ny('11:03'))
        self.assertEqual(nine['opposing_liquidity']['source_interval']['bar_open_ny'], ny('11:12'))
        self.assertEqual((ten['role'], ten['direction'], ten['verdict']), ('independent_range_context', 'bullish', 'failed'))
        self.assertEqual(ten['outcome'], 'failed_before_objectives')
        self.assertEqual(ten['invalidated_at_ny'], ny('12:00'))
        self.assertEqual(eleven['observation_status'], 'no_post_close_evidence_at_cutoff')
        self.assertNotIn('9ate8', result['spoken_summary'])
        self.assertNotIn('Young Lefty', result['spoken_summary'])
        self.assertIn('independent range', result['spoken_summary'])
        self.assertIn('no later setup or delivery evidence', result['spoken_summary'])
        for row in result['ranges']:
            self.assertEqual(row['detail_request']['args']['through_ny'], ny('12:00'))
        self.assertEqual(len(self.review['shift_story']['range_transitions']), 2)
        self.assertEqual(eleven['role'], 'selected_range')
        self.assertIsNone(eleven['direction'])


class OtherRangeConversationTests(unittest.TestCase):
    def setUp(self):
        self.context = MarketConversation(('test-guild', 'test-member', 'test-session'))
        self.context.begin_turn()
        self.initial = self.context.run('review_market_session', DAY, provider)
        self.assertTrue(self.initial['ok'], self.initial)

    def other(self, text='Were there any other relevant GTOP plays or ranges?'):
        self.context.begin_turn(text)
        request = self.context.required_evidence_request()
        self.assertEqual(request['tool'], 'review_other_market_ranges')
        return self.context.run(request['tool'], request['args'], provider)

    def anchors(self, result):
        self.assertTrue(result['ok'], result)
        return [row['anchor_start_ny'] for row in result['review']['other_range_followup']['ranges']]

    def test_tool_retrieval_does_not_mean_discussed(self):
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], [])
        self.assertEqual(self.anchors(self.other()), [ny(f'{h:02}:00') for h in range(7, 12)])
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], [])

    def test_completed_short_response_excludes_only_spoken_named_failures(self):
        self.assertEqual(self.context.complete_response(INITIAL, generation=self.context.generation), 2)
        result = self.other()
        self.assertEqual(self.anchors(result), [ny('09:00'), ny('10:00'), ny('11:00')])
        self.assertIn('retrieval_is_not_discussion', result['market_context']['discussion_context'])

    def test_completed_full_synopsis_excludes_every_range_actually_spoken(self):
        text = self.initial['review']['shift_synopsis']['spoken_summary']
        # The completed synopsis now actually explains the independent ten
        # failure; retrieval alone and earlier shorter transcripts stay unchanged.
        self.assertIn('Independent 10:00 AM H1 range: bullish', text)
        self.assertIn('failed before 50%/buy-side', text)
        self.assertEqual(self.context.complete_response(text), 4)
        self.assertEqual(self.anchors(self.other()), [ny('11:00')])

    def test_cancelled_partial_and_stale_receipts_do_not_advance(self):
        generation = self.context.generation
        self.assertEqual(self.context.complete_response(INITIAL, completed=False), 0)
        self.context.begin_turn('Other relevant ranges?')
        self.assertEqual(self.context.complete_response(INITIAL, generation=generation), 0)
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], [])
        self.context.invalidate()
        self.assertEqual(self.context.complete_response(INITIAL, generation=self.context.generation), 0)

    def test_response_identity_is_idempotent_and_does_not_mark_all_retrieved(self):
        self.assertEqual(self.context.complete_response('9ate8 failed.', response_id='synthetic-response'), 1)
        self.assertEqual(self.context.complete_response(INITIAL, response_id='synthetic-response'), 0)
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], [ny('08:00')])

    def test_source_candle_time_is_not_a_spoken_range_identity(self):
        self.assertEqual(self.context.complete_response('The objective was in the 9:00 AM M1 candle.'), 0)
        self.assertEqual(self.context.complete_response('The nine AM H1 range completed its objectives.'), 1)
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], [ny('09:00')])

    def test_natural_h1_shorthand_identifies_verified_ranges(self):
        for label in ('nine H1 range', '9H1 range', 'H1 range at nine', 'nine hourly CRT'):
            with self.subTest(label=label):
                context = MarketConversation()
                context.begin_turn()
                context.run('review_market_session', DAY, provider)
                self.assertEqual(context.complete_response(f'The {label} delivered both objectives.'), 1)
                self.assertEqual(context.discussion_context()['discussed_anchors'], [ny('09:00')])

    def test_meta_mentions_and_future_promises_do_not_mark_discussion(self):
        for text in ("I haven't discussed 9ate8 or Young Lefty yet.",
                     'I will review the nine H1 range and its objectives.',
                     "Let's discuss Young Lefty and 9ate8.",
                     '9ate8 and Young Lefty.',
                     'I need to explain the 10 H1 range failure.'):
            with self.subTest(text=text):
                self.assertEqual(self.context.complete_response(text), 0)
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], [])
        self.assertEqual(self.context.complete_response('9ate8 did not deliver either objective.'), 1)

    def test_text_route_cannot_be_bypassed_by_repeated_default_recap(self):
        self.context.complete_response(INITIAL)
        self.context.begin_turn('What other ranges were relevant?')
        result = self.context.run('review_market_session', DAY, provider)
        self.assertEqual(result['status'], 'market_other_ranges_required')
        self.assertEqual(result['next_tool'], 'review_other_market_ranges')
        self.assertEqual(self.anchors(self.context.run(result['next_tool'], result['next_arguments'], provider)),
                         [ny('09:00'), ny('10:00'), ny('11:00')])

    def test_other_range_evidence_is_prefetched_before_a_no_tool_model_reply(self):
        self.context.complete_response(INITIAL)
        generation = self.context.begin_turn('Any other relevant GTOP plays or ranges?')
        supplied = prefetch_market_evidence(self.context, provider, generation)
        payload = json.loads(supplied[supplied.index('{'):])
        self.assertEqual(payload['voice_view']['kind'], 'other_range_followup')
        self.assertEqual(self.anchors(payload), [ny('09:00'), ny('10:00'), ny('11:00')])
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], [ny('07:00'), ny('08:00')])

    def test_text_scope_overrides_generated_other_range_arguments(self):
        self.context.complete_response(INITIAL)
        self.context.begin_turn('Other relevant GTOP plays?')
        result = self.context.run('review_other_market_ranges',
            {'asset': 'XAUUSD', 'date_ny': '2026-10-01', 'shift': 'night', 'context_action': 'switch'}, provider)
        self.assertEqual(result['asset'], 'NAS100')
        self.assertEqual(self.anchors(result), [ny('09:00'), ny('10:00'), ny('11:00')])

    def test_voice_explicit_tool_routes_without_a_synthetic_transcript(self):
        self.context.complete_response(INITIAL)
        self.context.begin_turn()
        self.assertIsNone(self.context.intent)
        self.assertIsNone(self.context.required_evidence_request())
        result = self.context.run('review_other_market_ranges', {'context_action': 'continue'}, provider)
        self.assertEqual(self.anchors(result), [ny('09:00'), ny('10:00'), ny('11:00')])
        self.assertIsNone(self.context._client_text)
        payload = voice_tool_payload('review_other_market_ranges', result)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['voice_view']['kind'], 'other_range_followup')
        self.assertEqual(expanded(payload, payload['review']['other_range_followup']), result['review']['other_range_followup'])
        self.assertLess(len(json.dumps(payload, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)

    def test_voice_continue_cannot_switch_scope(self):
        self.context.begin_turn()
        result = self.context.run('review_other_market_ranges', {**DAY, 'asset': 'XAUUSD', 'context_action': 'continue'}, provider)
        self.assertEqual(result['status'], 'market_context_mismatch')

    def test_other_ranges_cannot_silently_expand_a_frozen_cutoff(self):
        self.context.selected['through_ny'] = ny('11:00')
        self.context.requested = deepcopy(self.context.selected)
        self.context.begin_turn()
        result = self.context.run('review_other_market_ranges', {'context_action': 'continue'}, provider)
        self.assertEqual(result['status'], 'market_context_mismatch')

    def test_wrong_returned_cutoff_does_not_replace_context(self):
        self.context.begin_turn()
        previous = deepcopy(self.context.evidence)
        def wrong(name, args):
            result = provider(name, args)
            result['review']['shift_story']['end_ny'] = ny('13:00')
            return result
        result = self.context.run('review_other_market_ranges', {'context_action': 'continue'}, wrong)
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(self.context.evidence, previous)

    def test_cancelled_other_range_result_cannot_replace_newer_scope(self):
        entered, release = threading.Event(), threading.Event()
        self.context.begin_turn()
        def blocked(name, args):
            entered.set()
            self.assertTrue(release.wait(3))
            return provider(name, args)
        with ThreadPoolExecutor() as pool:
            old = pool.submit(self.context.run, 'review_other_market_ranges', {'context_action': 'continue'}, blocked)
            self.assertTrue(entered.wait(3))
            self.context.begin_turn()
            newer = self.context.run('review_other_market_ranges', {**DAY, 'asset': 'XAUUSD', 'context_action': 'switch'}, provider)
            release.set()
            self.assertEqual(old.result(3)['status'], 'stale_market_context')
        self.assertTrue(newer['ok'])
        self.assertEqual(self.context.selected['asset'], 'XAUUSD')

    def test_switch_away_return_and_explicit_reset_have_separate_semantics(self):
        self.context.complete_response(INITIAL)
        self.context.begin_turn()
        other = self.context.run('review_other_market_ranges', {**DAY, 'asset': 'XAUUSD', 'context_action': 'switch'}, provider)
        self.assertIn(ny('07:00'), self.anchors(other))
        self.context.begin_turn()
        returned = self.context.run('review_other_market_ranges', {**DAY, 'context_action': 'switch'}, provider)
        self.assertEqual(self.anchors(returned), [ny('09:00'), ny('10:00'), ny('11:00')])
        self.context.begin_turn('Start this review over. Other relevant ranges?')
        reset = self.context.run('review_other_market_ranges', {**DAY, 'context_action': 'continue'}, provider)
        self.assertIn(ny('07:00'), self.anchors(reset))

    def test_date_shift_member_and_session_do_not_share_discussion(self):
        self.context.complete_response(INITIAL)
        for scope in ({**DAY, 'date_ny': '2026-10-01'}, {**DAY, 'shift': 'night'}):
            self.context.begin_turn()
            result = self.context.run('review_other_market_ranges', {**scope, 'context_action': 'switch'}, provider)
            self.assertEqual(len(self.anchors(result)), 5)
        for owner in (self.context.owner, ('test-guild', 'another-member', 'test-session')):
            fresh = MarketConversation(owner)
            fresh.begin_turn()
            result = fresh.run('review_other_market_ranges', {**DAY, 'context_action': 'switch'}, provider)
            self.assertEqual(len(self.anchors(result)), 5)

    def test_finished_other_response_advances_and_exhaustion_never_repeats(self):
        self.context.complete_response(INITIAL)
        result = self.other()
        self.assertEqual(self.context.complete_response(result['review']['other_range_followup']['spoken_summary']), 3)
        exhausted = self.other()
        self.assertEqual(self.anchors(exhausted), [])
        self.assertTrue(exhausted['review']['other_range_followup']['all_ranges_discussed'])

    def test_tool_contract_and_definition_detail_requests_are_distinct(self):
        tools = contextual_tools(MARKET_TOOLS)
        tool = next(t for t in tools if t['name'] == 'review_other_market_ranges')
        self.assertIn('reset', tool['parameters']['properties']['context_action']['enum'])
        self.assertIn('retrieved evidence', tool['description'])
        self.context.begin_turn('Was there another Model 1?')
        self.assertFalse(self.context._required_other)
        self.assertEqual(self.context.required_evidence_request()['tool'], 'review_market_crt')
        self.context.begin_turn('Were there other Model 1 setups?')
        self.assertFalse(self.context._required_other)
        self.context.begin_turn('What other GTOP plays exist in general?')
        self.assertFalse(self.context._required_other)

    def test_voice_explicit_reset_starts_only_this_scope_over(self):
        self.context.complete_response(INITIAL)
        self.context.begin_turn()
        result = self.context.run('review_other_market_ranges', {**DAY, 'context_action': 'reset'}, provider)
        self.assertEqual(len(self.anchors(result)), 5)


if __name__ == '__main__':
    unittest.main()
