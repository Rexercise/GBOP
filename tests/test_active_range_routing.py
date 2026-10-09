"""Bounded active-story continuation with retained replay data, no ASR or writes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import threading
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.market_data import MARKET_TOOLS
from gbop_voice_web.market_prefetch import prefetch_market_evidence
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS
from test_other_market_ranges import BARS, DAY, ny, provider


ACTIVE = {'context_action': 'continue', 'followup_mode': 'continue_active_range'}


class ReplayProvider:
    def __init__(self):
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, deepcopy(args)))
        if name == 'review_market_crt':
            return {'ok': True, 'asset': args['asset'], 'review': crt_review(
                BARS, parse_time(args['anchor_start_ny']), parse_time(args['through_ny']),
                args['anchor_timeframe'], 60)}
        return provider(name, args)


class ActiveRangeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.context = MarketConversation(('test-guild', 'test-member', 'test-session'))
        self.provider = ReplayProvider()
        self.context.begin_turn()
        self.initial = self.context.run('review_market_session', DAY, self.provider)
        self.assertTrue(self.initial['ok'], self.initial)

    def detail(self, hour):
        self.context.begin_turn()
        result = self.context.run('review_market_crt', {
            'asset': 'NAS100', 'anchor_start_ny': ny(hour), 'anchor_timeframe': 'H1',
            'through_ny': ny('12:00'), 'context_action': 'continue'}, self.provider)
        self.assertTrue(result['ok'], result)
        return result

    def continue_story(self, text='What happened next?'):
        self.context.begin_turn(text)
        request = self.context.required_evidence_request()
        self.assertEqual(request['tool'], 'review_other_market_ranges')
        self.assertEqual(request['query_purpose'], 'continue_active_range')
        self.assertEqual(request['args']['followup_mode'], 'continue_active_range')
        return self.context.run(request['tool'], request['args'], self.provider)

    def active(self, result):
        self.assertTrue(result['ok'], result)
        followup = result['review']['other_range_followup']
        self.assertEqual(followup['mode'], 'continue_active_range')
        self.assertEqual(followup['ranges'], [])
        return followup['active_range_context']

    def test_whole_shift_followup_selects_final_range_under_review_after_completion(self):
        self.assertEqual(self.context.selected['anchor_start_ny'], ny('08:00'))
        result = self.continue_story()
        self.assertEqual(self.active(result)['anchor_start_ny'], ny('11:00'))
        self.assertEqual(result['market_context']['selection']['anchor_start_ny'], ny('11:00'))
        self.assertEqual(self.active(result)['selected_at_ny'], ny('12:00'))
        self.assertEqual(self.active(result)['hourly_development'], [])
        self.assertEqual(self.active(result)['selection_status'], 'range_under_review')
        self.assertEqual(self.active(result)['variant'], {'status': 'unverified', 'labels': []})
        self.assertIsNone(self.active(result)['direction'])
        self.assertEqual(self.context.selected['through_ny'], ny('12:00'))
        self.assertEqual(self.context.pending, self.context.selected)
        self.assertEqual(self.provider.calls[-1], ('review_market_session', DAY))

    def test_common_active_followups_are_not_other_opportunities(self):
        for text in ('What happened next?', 'Continue that range.', 'How did it finish?',
                     'How did the active range end?', 'What did that range do next?',
                     'Then what happened?', 'Resume the story.', 'Continue the active range.'):
            with self.subTest(text=text):
                result = self.continue_story(text)
                self.assertEqual(self.active(result)['anchor_start_ny'], ny('11:00'))
                self.assertFalse(self.context._required_other)

    def test_discussed_active_range_can_continue_without_becoming_another_play(self):
        self.context.complete_response(self.initial['review']['shift_synopsis']['spoken_summary'])
        self.assertIn(ny('09:00'), self.context.discussion_context()['discussed_anchors'])
        before = deepcopy(self.context.discussion_context()['discussed_anchors'])
        result = self.continue_story()
        self.assertEqual(self.active(result)['anchor_start_ny'], ny('11:00'))
        self.assertEqual(self.context.discussion_context()['discussed_anchors'], before)

    def test_text_prefetch_supplies_active_story_for_no_tool_model_reply(self):
        generation = self.context.begin_turn('How did it finish?')
        supplied = prefetch_market_evidence(self.context, self.provider, generation)
        payload = json.loads(supplied[supplied.index('{'):])
        self.assertEqual(self.active(payload)['anchor_start_ny'], ny('11:00'))
        self.assertEqual(payload['market_context']['selection']['through_ny'], ny('12:00'))
        self.assertLess(len(json.dumps(payload, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)

    def test_text_intent_overrides_generated_scope_and_other_mode(self):
        self.context.begin_turn('Continue that range.')
        result = self.context.run('review_other_market_ranges', {
            'asset': 'XAUUSD', 'date_ny': '2026-10-01', 'shift': 'night',
            'context_action': 'switch', 'followup_mode': 'other_ranges'}, self.provider)
        self.assertEqual(self.active(result)['anchor_start_ny'], ny('11:00'))
        self.assertEqual(result['asset'], 'NAS100')
        self.assertEqual(self.provider.calls[-1], ('review_market_session', DAY))

    def test_text_active_route_cannot_be_bypassed_by_overview_or_next_hour(self):
        self.context.begin_turn('What happened next?')
        before = len(self.provider.calls)
        for name in ('review_market_session', 'review_market_crt', 'review_current_market'):
            result = self.context.run(name, {**DAY, 'anchor_start_ny': ny('10:00')}, self.provider)
            self.assertEqual(result['status'], 'market_active_range_required')
            self.assertEqual(result['next_arguments']['followup_mode'], 'continue_active_range')
        self.assertEqual(len(self.provider.calls), before)

    def test_audio_explicit_mode_has_no_synthetic_transcript(self):
        self.context.begin_turn()
        self.assertIsNone(self.context.required_evidence_request())
        result = self.context.run('review_other_market_ranges', ACTIVE, self.provider)
        self.assertEqual(self.active(result)['anchor_start_ny'], ny('11:00'))
        self.assertIsNone(self.context._client_text)
        self.assertIsNone(self.context.intent)
        payload = voice_tool_payload('review_other_market_ranges', result)
        self.assertEqual(self.active(payload), self.active(result))

    def test_explicit_nine_detail_remains_nine_through_repeated_continuation(self):
        self.detail('09:00')
        for text in ('Continue that range.', 'What happened next?', 'How did it finish?'):
            result = self.continue_story(text)
            self.assertEqual(self.active(result)['anchor_start_ny'], ny('09:00'))
            self.assertEqual(self.context.selected['anchor_start_ny'], ny('09:00'))
            self.assertEqual(self.context.selected['through_ny'], ny('12:00'))

    def test_explicit_older_selected_range_remains_selected(self):
        self.detail('08:00')
        result = self.continue_story()
        self.assertEqual(self.active(result)['anchor_start_ny'], ny('08:00'))
        self.assertEqual(self.context.selected['anchor_start_ny'], ny('08:00'))

    def test_explicit_independent_range_routes_to_its_detail_without_selection_reset(self):
        self.detail('10:00')
        result = self.continue_story()
        self.assertEqual(result['status'], 'selected_range_requires_detail')
        self.assertEqual(result['next_tool'], 'review_market_crt')
        self.assertEqual(result['next_arguments']['anchor_start_ny'], ny('10:00'))
        detailed = self.context.run(result['next_tool'], result['next_arguments'], self.provider)
        self.assertTrue(detailed['ok'], detailed)
        self.assertEqual(self.context.selected['anchor_start_ny'], ny('10:00'))

    def test_explicit_young_lefty_continues_its_own_detail(self):
        self.detail('07:00')
        result = self.continue_story()
        self.assertEqual(result['status'], 'selected_range_requires_detail')
        self.assertEqual(result['next_arguments']['anchor_start_ny'], ny('07:00'))
        self.assertEqual(self.context.selected['anchor_start_ny'], ny('07:00'))

    def test_genuine_other_request_cannot_be_replaced_by_active_mode(self):
        spoken = self.initial['review']['shift_synopsis']['spoken_summary']
        self.assertIn('10:00 AM H1 candle souped buy-side', spoken)
        self.context.complete_response(spoken)
        self.context.begin_turn('What other ranges were relevant?')
        result = self.context.run('review_other_market_ranges', ACTIVE, self.provider)
        self.assertTrue(result['ok'], result)
        followup = result['review']['other_range_followup']
        self.assertNotEqual(followup.get('mode'), 'continue_active_range')
        # Ten's acting-candle role is spoken; its independent own-range outcome
        # remains unspoken and must still be available with eleven.
        self.assertEqual([r['anchor_start_ny'] for r in followup['ranges']], [ny('10:00'), ny('11:00')])

    def test_other_and_focused_detail_phrases_stay_distinct(self):
        for text in ('Any other relevant ranges?', 'What is the next range?', 'More GTOP plays?'):
            with self.subTest(text=text):
                self.context.begin_turn(text)
                self.assertFalse(self.context._required_active)
                self.assertTrue(self.context._required_other)
        for text in ('How did the Model 1 finish?', 'Continue the Super Soup.',
                     'What happened next with CISD?'):
            with self.subTest(text=text):
                self.context.begin_turn(text)
                self.assertFalse(self.context._required_active)
                self.assertEqual(self.context.required_evidence_request()['tool'], 'review_market_crt')

    def test_unknown_mode_is_rejected_without_provider_call(self):
        self.context.begin_turn()
        before = len(self.provider.calls)
        result = self.context.run('review_other_market_ranges', {'followup_mode': 'new_hour'}, self.provider)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.provider.calls), before)

    def test_active_mode_cannot_switch_asset_date_shift_anchor_timeframe_or_cutoff(self):
        for args in ({'asset': 'XAUUSD'}, {'date_ny': '2026-10-01'}, {'shift': 'night'},
                     {'anchor_start_ny': ny('10:00')}, {'anchor_timeframe': 'H4'},
                     {'through_ny': ny('13:00')}, {'context_action': 'switch'},
                     {'context_action': 'reset'}):
            with self.subTest(args=args):
                self.context.begin_turn()
                before = deepcopy(self.context.selected), len(self.provider.calls)
                result = self.context.run('review_other_market_ranges', {**ACTIVE, **args}, self.provider)
                self.assertFalse(result['ok'])
                self.assertEqual(self.context.selected, before[0])
                self.assertEqual(len(self.provider.calls), before[1])

    def test_text_scope_change_requires_new_verified_range(self):
        self.context.begin_turn('Continue that range on XAUUSD.')
        request = self.context.required_evidence_request()
        self.assertIsNone(request['args'])
        self.assertEqual(request['status'], 'market_active_scope_required')
        before = len(self.provider.calls)
        result = self.context.run('review_other_market_ranges', ACTIVE, self.provider)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.provider.calls), before)
        self.assertEqual(self.context.selected['asset'], 'NAS100')

    def test_frozen_partial_cutoff_cannot_expand_to_whole_shift(self):
        self.context.selected['through_ny'] = ny('11:00')
        self.context.requested = deepcopy(self.context.selected)
        self.context.begin_turn('Continue that range.')
        before = len(self.provider.calls)
        result = self.context.run('review_other_market_ranges', ACTIVE, self.provider)
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(len(self.provider.calls), before)
        self.assertEqual(self.context.selected['through_ny'], ny('11:00'))

    def test_current_snapshot_cannot_be_replaced_by_historical_continuation(self):
        self.context.selected['review_mode'] = 'current_market'
        self.context.requested = deepcopy(self.context.selected)
        self.context.begin_turn('What happened next?')
        before = len(self.provider.calls)
        result = self.context.run('review_other_market_ranges', ACTIVE, self.provider)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.provider.calls), before)

    def test_wrong_returned_scope_or_cutoff_cannot_replace_verified_evidence(self):
        for key, value in (('asset', 'XAUUSD'), ('date_ny', '2026-10-01'),
                           ('shift', 'night'), ('end_ny', ny('13:00'))):
            with self.subTest(key=key):
                self.context.begin_turn('Continue that range.')
                before = deepcopy(self.context.selected), deepcopy(self.context.evidence)
                def wrong(name, args):
                    result = self.provider(name, args)
                    target = (result if key == 'asset' else result['review']['shift_story']
                              if key == 'end_ny' else result['review'])
                    target[key] = value
                    return result
                result = self.context.run('review_other_market_ranges', ACTIVE, wrong)
                self.assertEqual(result['status'], 'market_context_mismatch')
                self.assertEqual((self.context.selected, self.context.evidence), before)

    def test_missing_or_different_member_session_cannot_borrow_selection(self):
        for owner in (self.context.owner, ('test-guild', 'other-member', 'test-session')):
            fresh = MarketConversation(owner)
            fresh.begin_turn('Continue that range.')
            self.assertIsNone(fresh.required_evidence_request()['args'])
            before = len(self.provider.calls)
            result = fresh.run('review_other_market_ranges', {**DAY, **ACTIVE}, self.provider)
            self.assertEqual(result['status'], 'market_active_scope_required')
            self.assertEqual(len(self.provider.calls), before)
            self.assertIsNone(fresh.selected)

    def test_cancelled_inflight_continuation_cannot_replace_newer_scope(self):
        entered, release = threading.Event(), threading.Event()
        self.context.begin_turn('Continue that range.')
        def blocked(name, args):
            entered.set()
            self.assertTrue(release.wait(3))
            return provider(name, args)
        with ThreadPoolExecutor() as pool:
            old = pool.submit(self.context.run, 'review_other_market_ranges', ACTIVE, blocked)
            self.assertTrue(entered.wait(3))
            self.context.begin_turn()
            newer = self.context.run('review_other_market_ranges', {
                **DAY, 'asset': 'XAUUSD', 'context_action': 'switch'}, self.provider)
            release.set()
            self.assertEqual(old.result(3)['status'], 'stale_market_context')
        self.assertTrue(newer['ok'], newer)
        self.assertEqual(self.context.selected['asset'], 'XAUUSD')

    def test_closed_session_and_stale_prefetch_cannot_retrieve(self):
        generation = self.context.begin_turn('Continue that range.')
        self.context.close()
        before = len(self.provider.calls)
        self.assertIsNone(prefetch_market_evidence(self.context, self.provider, generation))
        result = self.context.run('review_other_market_ranges', ACTIVE, self.provider, generation=generation)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertEqual(len(self.provider.calls), before)

    def test_contextual_schema_is_explicit_without_changing_global_tools(self):
        before = deepcopy(MARKET_TOOLS)
        tool = next(tool for tool in contextual_tools(MARKET_TOOLS)
                    if tool['name'] == 'review_other_market_ranges')
        self.assertEqual(tool['parameters']['properties']['followup_mode']['enum'],
                         ['other_ranges', 'continue_active_range'])
        self.assertIn('followup_mode', tool['parameters']['required'])
        self.assertIn('a new hour does not select a new range', tool['description'])
        self.assertEqual(MARKET_TOOLS, before)


if __name__ == '__main__':
    unittest.main()
