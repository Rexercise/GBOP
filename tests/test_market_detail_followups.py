"""Offline text-intent routing contracts; audio has no fabricated transcript."""
from copy import deepcopy
import unittest

from gbop_voice_web.market_conversation import MarketConversation, _detail_index
from gbop_voice_web.market_data import MARKET_TOOLS, market_tool
from gbop_voice_web.voice_payload import voice_tool_payload
from test_market_conversation import NOW, Provider
import test_retained_market_replays as retained

DAY = {'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day'}


class FocusedRoutingTests(unittest.TestCase):
    def setUp(self):
        self.context, self.provider = MarketConversation(), Provider()
        self.context.begin_turn()
        self.context.run('review_market_session', DAY, self.provider)

    def request(self, text):
        self.context.begin_turn(text, now=NOW)
        return self.context.required_evidence_request()

    def execute(self, request):
        return self.context.run(request['tool'], request['args'], self.provider)

    def test_named_topics_require_actual_retrieval_despite_wrong_overview(self):
        for text, purpose in [('Was there a Model 1?', 'model1'),
                              ('Did SuperSoup perform?', 'super_soup'),
                              ('When did its CSD confirm?', 'csd'),
                              ('Was it a wick or body purge?', 'purge_identity'),
                              ('How close did it get to the midpoint?', 'objective_distance'),
                              ('How close did it get to the opposing liquidity?', 'objective_distance')]:
            with self.subTest(text=text):
                request = self.request(text)
                self.assertEqual(request['query_purpose'], purpose)
                self.assertEqual(request['args']['anchor_start_ny'], retained.ny('08:00'))
                count = len(self.provider.calls)
                result = self.context.run('review_market_session', DAY, self.provider)
                self.assertEqual(result['status'], 'market_detail_required')
                self.assertEqual(result['next_arguments'], request['args'])
                self.assertEqual(len(self.provider.calls), count)
                self.assertTrue(self.execute(request)['ok'])

    def test_exact_candle_cannot_be_silently_changed_and_ellipsis_retains_it(self):
        request = self.request('Was the 11:10 M5 candle a Model 1?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('11:10'))
        self.assertEqual(request['args']['anchor_start_ny'], retained.ny('08:00'))
        wrong = {**request['args'], 'asset': 'XAUUSD', 'anchor_start_ny': retained.ny('10:00'),
                 'detail_candle_start_ny': retained.ny('11:15'), 'detail_from_ny': retained.ny('11:20'),
                 'through_ny': retained.ny('13:00'), 'confirmation_timeframe': 'M15'}
        result = self.context.run('review_market_crt', wrong, self.provider)
        self.assertTrue(result['ok'], result)
        actual = self.provider.calls[-1][1]
        self.assertEqual(actual['asset'], 'NAS100')
        self.assertEqual(actual['detail_candle_start_ny'], retained.ny('11:10'))
        self.assertEqual(actual['confirmation_timeframe'], 'M5')
        self.assertIsNone(actual['detail_from_ny'])
        followup = self.request('Did it perform?')
        self.assertEqual(followup['args'], request['args'])

    def test_fulfilled_detail_allows_scoped_supplementary_evidence_only(self):
        request = self.request('When did its CSD confirm, and was there SMT?')
        extra = {'asset': 'NAS100', 'comparison_asset': 'SPX',
                 'anchor_start_ny': retained.ny('08:00'), 'anchor_timeframe': 'H1',
                 'through_ny': retained.ny('12:00')}
        self.assertEqual(self.context.run('review_market_smt', extra, self.provider)['status'],
                         'market_detail_required')
        self.execute(request)
        self.assertTrue(self.context.run('review_market_smt', extra, self.provider)['ok'])
        raw = {'asset': 'NAS100', 'start_ny': retained.ny('11:10'),
               'end_ny': retained.ny('11:20'), 'timeframe': 'M1'}
        self.assertTrue(self.context.run('inspect_market_candles', raw, self.provider)['ok'])
        self.assertEqual(self.context.run('inspect_market_candles',
            {**raw, 'end_ny': retained.ny('13:00')}, self.provider)['status'], 'market_context_mismatch')
        self.assertEqual(self.context.run('review_market_session', DAY, self.provider)['status'],
                         'market_detail_required')
        self.context.begin_turn('When did its CSD confirm?', now=NOW)
        self.assertEqual(self.context.run('review_market_smt', extra, self.provider)['status'],
                         'market_detail_required')

    def test_other_candle_never_defaults_to_previous_or_first_page(self):
        self.execute(self.request('Did the 10:00 Model 1 have Super Soup?'))
        self.context._detail_index = [
            {'anchor_start_ny': retained.ny('08:00'), 'bar_open_ny': retained.ny('10:00'),
             'direction': 'bearish', 'purge_type': 'body_soup'},
            {'anchor_start_ny': retained.ny('08:00'), 'bar_open_ny': retained.ny('11:15'),
             'direction': 'bullish', 'purge_type': 'body_soup'}]
        for adjective in ('other', 'another', 'different'):
            request = self.request('Did the ' + adjective + ' Model 1 have Super Soup?')
            self.assertIsNone(request['args'], request)
        request = self.request('Did the other bullish Model 1 have Super Soup?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('11:15'))
        request = self.request('Did the other 11:15 Model 1 have Super Soup?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('11:15'))

    def test_other_same_direction_cannot_repeat_previous_candle(self):
        self.execute(self.request('Did the 10:00 Model 1 have Super Soup?'))
        self.context._detail_index = [
            {'anchor_start_ny': retained.ny('08:00'), 'bar_open_ny': retained.ny('10:00'),
             'direction': 'bearish', 'purge_type': 'body_soup'}]
        self.assertIsNone(self.request('Did the other Model 1 have Super Soup?')['args'])
        self.assertIsNone(self.request('Did the other bearish Model 1 have Super Soup?')['args'])

    def test_whole_hour_assigned_candle_is_not_a_parent_range(self):
        request = self.request('Was the 11 AM candle a Model 1?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('11:00'))
        self.assertEqual(request['args']['anchor_start_ny'], retained.ny('08:00'))
        request = self.request('Was there a Model 1 in the 10 AM range?')
        self.assertEqual(request['args']['anchor_start_ny'], retained.ny('10:00'))
        self.assertIsNone(request['args']['detail_candle_start_ny'])

    def test_night_candle_clock_uses_retained_date_and_shift(self):
        self.context.begin_turn('Review the night shift', now=NOW)
        self.context.run('review_market_session', DAY, self.provider)
        request = self.request('Was the 11:15 candle a Model 1?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('23:15'))
        self.assertEqual(request['args']['through_ny'], '2026-10-03T00:00:00-04:00')

    def test_scope_switch_clears_exact_identity(self):
        self.execute(self.request('Was the 11:10 M5 candle a Model 1?'))
        request = self.request('What about Model 1 on gold in the night shift?')
        self.assertEqual(request['args']['asset'], 'XAUUSD')
        self.assertEqual(request['args']['anchor_start_ny'], retained.ny('20:00'))
        self.assertIsNone(request['args']['detail_candle_start_ny'])
        self.assertIsNone(request['args']['confirmation_timeframe'])

    def test_no_scope_or_out_of_range_does_not_invent_evidence(self):
        blank = MarketConversation()
        blank.begin_turn('Did Super Soup perform?', now=NOW)
        self.assertIsNone(blank.required_evidence_request()['args'])
        for text in ('Was the 21:15 candle a Model 1?',
                     'Was 2026-10-01T11:10:00-04:00 a Model 1?'):
            request = self.request(text)
            self.assertIsNone(request['args'])

    def test_definition_and_whole_shift_leave_no_focused_retrieval(self):
        for text in ('What is Model 1?', 'What is a Super Soup?', 'Define CSD',
                     'Explain Model 1', 'What does CSD mean?', 'What is CSD in general?',
                     'Review the whole shift'):
            self.assertIsNone(self.request(text), text)

    def test_audio_remains_model_classified_and_cancellation_clears_text_requirement(self):
        self.request('When did its CSD confirm?')
        self.context.invalidate()
        self.assertIsNone(self.context.required_evidence_request())
        self.context.begin_turn(None)
        self.assertIsNone(self.context.required_evidence_request())
        # No claim of deterministic audio routing. Wrong overview is still the
        # model's choice when there is neither transcript nor declared detail.
        self.assertTrue(self.context.run('review_market_session', DAY, self.provider)['ok'])

    def test_private_routing_index_is_bounded_and_retains_dual_direction(self):
        facts = [{'bar_open_ny': retained.ny('10:10'), 'purge_type': 'body_soup', 'direction': direction}
                 for direction in ('bullish', 'bearish')]
        rows = [{'anchor_start_ny': retained.ny('08:00'), 'candle_lifecycle': {'purge_candles': facts}}]
        index = _detail_index({'review': {'shift_story': {'ranges': rows * 99}}})
        self.assertEqual(len(index), 8)
        self.assertEqual({row['direction'] for row in index}, {'bullish', 'bearish'})
        self.context._detail_index = index[:2]
        request = self.request('Was the bullish Model 1 valid?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('10:10'))
        self.assertNotIn('_detail_index', self.context.prompt())

    def test_multiple_matching_parents_or_directional_candles_require_clarification(self):
        self.context._detail_index = [
            {'anchor_start_ny': retained.ny('08:00'), 'bar_open_ny': retained.ny('11:10'), 'direction': 'bullish', 'purge_type': 'body_soup'},
            {'anchor_start_ny': retained.ny('10:00'), 'bar_open_ny': retained.ny('11:10'), 'direction': 'bullish', 'purge_type': 'body_soup'}]
        self.assertIsNone(self.request('Was the 11:10 candle Model 1?')['args'])
        self.context._detail_index[1]['anchor_start_ny'] = retained.ny('08:00')
        self.context._detail_index[1]['bar_open_ny'] = retained.ny('11:15')
        self.assertIsNone(self.request('Was the bullish Model 1 valid?')['args'])

    def test_multiple_named_candles_do_not_silently_select_first(self):
        for text in ('Compare the 10:10 and 11:15 Model 1 candles',
                     'You said 10:10, but what about the 11:15 Model 1?'):
            self.assertIsNone(self.request(text)['args'])

    def test_parent_clock_and_exact_candle_clock_are_distinct(self):
        request = self.request('For the 9:00 AM range, show the 11:15 Model 1')
        self.assertEqual(request['args']['anchor_start_ny'], retained.ny('09:00'))
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('11:15'))

    def test_standalone_crt_exact_iso_does_not_rewrite_parent_scope(self):
        standalone = MarketConversation()
        args = {'asset': 'NAS100', 'anchor_start_ny': retained.ny('09:00'),
                'anchor_timeframe': 'H1', 'through_ny': retained.ny('12:00')}
        standalone.begin_turn()
        self.assertTrue(standalone.run('review_market_crt', args, self.provider)['ok'])
        standalone.begin_turn('Show the 2026-10-02T10:10:00-04:00 candle identity', now=NOW)
        request = standalone.required_evidence_request()
        self.assertEqual(request['args']['anchor_start_ny'], args['anchor_start_ny'])
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('10:10'))
        self.assertNotIn('date_ny', standalone.requested)

    def test_new_exact_iso_time_does_not_reuse_failed_old_clock(self):
        self.request('Was the 21:15 candle Model 1?')
        request = self.request('Was 2026-10-02T11:15:00-04:00 Model 1?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('11:15'))


class RetainedFocusedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.replay = retained.RetainedMarketReplayTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)
        self.calls = []
        self.context = MarketConversation()
        self.context.begin_turn()
        self.context.run('review_market_session', DAY, self.runner)

    def runner(self, name, args):
        self.calls.append((name, deepcopy(args)))
        return market_tool(self.replay.db, name, args)

    def focused(self, text):
        self.context.begin_turn(text, now=NOW)
        request = self.context.required_evidence_request()
        self.assertIsNotNone(request['args'], request)
        result = self.context.run(request['tool'], request['args'], self.runner)
        return request, result, voice_tool_payload(request['tool'], result)

    def test_exact_wick_and_later_body_use_own_verified_parent_and_identity(self):
        for clock, expected in [('11:10', 'wick_soup'), ('11:15', 'body_soup')]:
            request, result, page = self.focused('Was the ' + clock + ' M5 candle a Model 1?')
            self.assertEqual(request['args']['anchor_start_ny'], retained.ny('09:00'))
            self.assertTrue(result['ok'], result)
            self.assertTrue(page['ok'], page)
            facts = page['review']['candle_lifecycle']['purge_candles']
            self.assertEqual(len(facts), 1)
            self.assertEqual(facts[0]['bar_open_ny'], retained.ny(clock))
            self.assertEqual(facts[0]['purge_type'], expected)

    def test_missing_named_candle_is_unverified_without_identity_substitution(self):
        request, result, page = self.focused('Was the 11:36 candle a Model 1?')
        self.assertTrue(result['ok'], result)
        self.assertFalse(page['ok'], page)
        self.assertEqual(page['status'], 'detail_identity_not_in_available_page')
        self.assertEqual(page['detail_request']['args']['detail_candle_start_ny'], retained.ny('11:36'))

    def test_missing_source_minute_never_recreates_body_identity(self):
        bars = [b for b in self.replay.bars['NAS100'] if b['time'] != retained.parse_time(retained.ny('11:17'))]
        self.replay.store('NAS100', bars)
        request, result, page = self.focused('Was the 11:15 M5 candle a Model 1?')
        self.assertEqual(request['args']['detail_candle_start_ny'], retained.ny('11:15'))
        self.assertFalse(page['ok'], page)
        self.assertNotIn('purge_candles', page.get('review', {}).get('candle_lifecycle', {}))

    def test_distance_metric_is_available_on_exact_retrieved_parent(self):
        request, result, page = self.focused('How close did it get to the midpoint?')
        metric = result['review']['objective_approach']
        self.assertEqual(metric['status'], 'evaluated')
        self.assertEqual(metric['range_start_ny'], retained.ny('08:00'))
        self.assertIn('midpoint', metric['objectives'])


if __name__ == '__main__':
    unittest.main()
