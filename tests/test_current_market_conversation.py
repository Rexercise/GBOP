"""Current-read scope continuity uses synthetic bars and no external writes."""
from copy import deepcopy
import unittest

from gbop_voice_web import market_data as market
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.market_prefetch import prefetch_market_evidence
import test_current_market as fixture
from test_current_market import candles, ts


class CurrentConversationTests(unittest.TestCase):
    def setUp(self):
        self.h = fixture.CurrentMarketTests()
        self.h.setUp()
        self.addCleanup(self.h.tearDown)
        self.now = ts('08:50')
        self.h.seed(self.now, candles(ts('07:00'), ts('12:00')))
        self.context = MarketConversation((1, 2, 'current-test'))
        self.calls = []

    def runner(self, name, args):
        self.calls.append((name, deepcopy(args)))
        return market.market_tool(self.h.db, name, args, self.now)

    def current(self, text='What do you see on NAS?'):
        turn = self.context.begin_turn(text, now=self.now)
        request = self.context.required_evidence_request()
        self.assertIsNotNone(request)
        self.assertEqual(request['tool'], 'review_current_market')
        result = self.context.run(request['tool'], request['args'], self.runner, generation=turn)
        self.assertTrue(result['ok'], result)
        return result

    def test_text_prefetch_reads_current_without_completed_shift_catalog(self):
        turn = self.context.begin_turn('What do you see on NAS?', now=self.now)
        evidence = prefetch_market_evidence(self.context, self.runner, turn)
        self.assertIn('current_market', evidence)
        self.assertEqual([n for n, _ in self.calls], ['review_current_market'])
        self.assertEqual(self.context.selected['anchor_start_ny'], market.stamp(ts('07:00')))
        self.assertEqual(self.context.selected['through_ny'], market.stamp(self.now))
        self.assertEqual(self.context.selected['review_mode'], 'current_market')
        self.assertEqual(self.context._journal_review['selection']['review_mode'], 'current_market')

    def test_generated_asset_cannot_replace_text_asset_and_one_turn_reads_once(self):
        self.context.begin_turn('What do you see on NAS?', now=self.now)
        args = dict(asset='gold', anchor_start_ny=None, anchor_timeframe=None, confirmation_timeframe=None,
                    context_action='switch')
        first = self.context.run('review_current_market', args, self.runner)
        second = self.context.run('review_current_market', args, self.runner)
        self.assertTrue(first['ok'], first)
        self.assertEqual(first, second)
        self.assertEqual(self.calls[0][1]['asset'], 'NAS100')
        self.assertEqual(len(self.calls), 1)

    def test_current_followup_crt_freezes_cutoff_and_journal_mode(self):
        self.current()
        original = deepcopy(self.context.selected)
        self.now = ts('09:32')
        self.h.seed(self.now, candles(ts('07:00'), self.now))
        self.context.begin_turn('When did its CISD confirm?', now=self.now)
        request = self.context.required_evidence_request()
        self.assertEqual(request['tool'], 'review_market_crt')
        self.assertEqual(request['args']['through_ny'], original['through_ny'])
        self.assertEqual(request['args']['anchor_start_ny'], original['anchor_start_ny'])
        result = self.context.run(request['tool'], request['args'], self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['review_mode'], 'current_market')
        self.assertEqual(self.context.selected['as_of_ny'], original['as_of_ny'])
        self.assertEqual(self.context._journal_review['selection']['through_ny'], original['through_ny'])
        self.assertEqual([name for name, _ in self.calls], ['review_current_market', 'review_market_crt'])

    def test_only_a_new_current_request_refreshes(self):
        self.current()
        original = deepcopy(self.context.selected)
        self.now = ts('09:32')
        self.h.seed(self.now, candles(ts('07:00'), self.now))
        self.context.begin_turn('And what about that setup?', now=self.now)
        rejected = self.context.run('review_current_market', {'asset': 'NAS'}, self.runner)
        self.assertFalse(rejected['ok'])
        self.assertEqual(self.context.selected, original)
        refreshed = self.current('What do you see on NAS right now?')
        self.assertEqual(refreshed['review']['current_scope']['through_ny'], market.stamp(self.now))
        self.assertEqual(self.context.selected['anchor_start_ny'], market.stamp(ts('08:00')))

    def test_explicit_completed_recap_can_switch_from_current_mode(self):
        self.current()
        self.now = ts('12:30')
        self.h.seed(self.now, candles(ts('07:00'), ts('12:00')))
        self.context.begin_turn('Recap the 2026-10-02 day shift', now=self.now)
        self.assertIsNone(self.context.required_evidence_request())
        result = self.context.run('review_market_session',
            {'asset': 'NAS', 'date_ny': '2026-10-02', 'shift': 'day'}, self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['through_ny'], market.stamp(ts('12:00')))
        self.assertNotEqual(self.context.selected.get('review_mode'), 'current_market')

    def test_audio_requires_explicit_tool_and_keeps_subsequent_detail_frozen(self):
        self.context.begin_turn(None, now=self.now)
        self.assertIsNone(self.context.required_evidence_request())
        result = self.context.run('review_current_market', {'asset': 'NAS'}, self.runner)
        self.assertTrue(result['ok'], result)
        scope = deepcopy(self.context.selected)
        self.context.begin_turn(None, now=ts('09:32'))
        args = {key: scope[key] for key in ('asset', 'anchor_start_ny', 'anchor_timeframe', 'through_ny')}
        result = self.context.run('review_market_crt', args, self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['through_ny'], scope['through_ny'])

    def test_stale_current_evidence_cannot_bind_or_execute_invalid_detail(self):
        self.now = ts('14:30', '2026-10-03')
        self.h.seed(self.now, candles(ts('07:00'), ts('12:00')), capture=ts('12:00'), tick=ts('12:00'))
        self.current()
        self.assertIsNone(self.context.selected['through_ny'])
        self.assertIsNone(self.context._journal_review)
        self.context.begin_turn('When did its CISD confirm?', now=self.now)
        self.assertIsNone(self.context.required_evidence_request()['args'])
        self.context.begin_turn('Journal that trade', now=self.now)
        called = []
        result = self.context.run('save_journal_entry', {'market_reference': 'selected_review'},
                                  lambda *args: called.append(args) or {'ok': True})
        self.assertEqual(result['status'], 'journal_review_required')
        self.assertEqual(called, [])

    def test_cancellation_fences_current_result_and_retained_journal_snapshot(self):
        self.context.begin_turn('What do you see on NAS?', now=self.now)
        def cancelled(name, args):
            result = self.runner(name, args)
            self.context.invalidate()
            return result
        result = self.context.run('review_current_market', {'asset': 'NAS'}, cancelled)
        self.assertEqual(result['status'], 'stale_market_context')
        self.assertIsNone(self.context.selected)
        self.assertIsNone(self.context._journal_review)

    def test_current_schema_is_contextual_and_ordinary_historical_request_is_not_prefetched(self):
        tools = contextual_tools(market.MARKET_TOOLS)
        current = next(t for t in tools if t['name'] == 'review_current_market')
        self.assertIn('context_action', current['parameters']['properties'])
        self.context.begin_turn('What did NAS do last night?', now=self.now)
        self.assertIsNone(self.context.required_evidence_request())

    def test_offshift_followup_cannot_silently_switch_to_completed_shift(self):
        self.now = ts('14:30')
        self.h.seed(self.now, candles(ts('07:00'), self.now))
        self.current()
        selected = deepcopy(self.context.selected)
        self.context.begin_turn('And was that bullish?', now=self.now)
        count = len(self.calls)
        result = self.context.run('review_market_session',
            {'asset': 'NAS', 'date_ny': None, 'shift': 'day'}, self.runner)
        self.assertEqual(result['status'], 'selected_range_requires_detail')
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.context.selected, selected)

    def test_audio_cannot_fill_a_null_current_cutoff_from_generated_arguments(self):
        self.now = ts('14:30', '2026-10-03')
        self.h.seed(self.now, candles(ts('07:00'), ts('12:00')), capture=ts('12:00'))
        self.current()
        self.context.begin_turn(None, now=self.now)
        count = len(self.calls)
        result = self.context.run('review_market_crt', dict(asset='NAS',
            anchor_start_ny=self.context.selected['anchor_start_ny'], anchor_timeframe='H1',
            through_ny=market.stamp(self.now)), self.runner)
        self.assertEqual(result['status'], 'current_market_evidence_unavailable')
        self.assertEqual(len(self.calls), count)

    def test_custom_assigned_timeframe_is_preserved_in_frozen_detail(self):
        self.now = ts('10:32')
        self.h.seed(self.now, candles(ts('00:00'), self.now))
        self.current('Currently review NAS H6 anchor 2026-10-02T04:00:00-04:00 assigned M30')
        self.assertEqual(self.context.selected['assigned_timeframe'], 'M30')
        self.context.begin_turn('When did its CISD confirm?', now=self.now)
        request = self.context.required_evidence_request()
        self.assertEqual(request['args']['anchor_timeframe'], 'H6')
        self.assertEqual(request['args']['confirmation_timeframe'], 'M30')
        result = self.context.run(request['tool'], request['args'], self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['assigned_timeframe'], 'M30')
        self.context.begin_turn(None, now=self.now)
        args = {key: self.context.selected[key] for key in
                ('asset', 'anchor_start_ny', 'anchor_timeframe', 'through_ny')}
        result = self.context.run('review_market_crt', args, self.runner)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.calls[-1][1]['confirmation_timeframe'], 'M30')
        result = self.context.run('review_market_crt', {**args, 'confirmation_timeframe': 'M5'}, self.runner)
        self.assertEqual(result['status'], 'market_context_mismatch')

    def test_mismatched_current_response_cannot_overwrite_verified_selection(self):
        first = self.current()
        selected = deepcopy(self.context.selected)
        self.context.begin_turn('What do you see on NAS right now?', now=self.now)
        def wrong(name, args):
            result = deepcopy(first)
            result['review']['current_scope']['asset'] = 'XAUUSD'
            return result
        result = self.context.run('review_current_market', {'asset': 'NAS'}, wrong)
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(self.context.selected, selected)

    def test_forming_selected_reference_is_not_executable_crt_evidence(self):
        self.now = ts('07:30')
        self.h.seed(self.now, candles(ts('07:00'), self.now))
        self.current()
        self.context.begin_turn('When did its CISD confirm?', now=self.now)
        request = self.context.required_evidence_request()
        self.assertIsNone(request['args'])
        self.assertIn('still forming', request['error'])


if __name__ == '__main__':
    unittest.main()
