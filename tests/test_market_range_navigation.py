"""Same-shift range navigation and retained-price conversation regressions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading
import unittest

from gbop_voice_web import market_data as market
from gbop_voice_web.market_conversation import MarketConversation, contextual_tools
from gbop_voice_web.voice_payload import shift_voice_overview
from test_market_conversation import NOW, Provider
import test_retained_market_replays as retained


DAY = {'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day'}


def detail(clock='09:00', **overrides):
    return {'asset': 'NAS100', 'anchor_start_ny': retained.ny(clock),
            'anchor_timeframe': 'H1', 'through_ny': retained.ny('12:00'),
            'context_action': 'continue', **overrides}


class SameShiftRangeNavigationTests(unittest.TestCase):
    def setUp(self):
        self.context, self.provider = MarketConversation(), Provider()
        self.context.begin_turn()
        self.initial = self.context.run('review_market_session', DAY, self.provider)
        self.assertTrue(self.initial['ok'], self.initial)

    def test_voice_continue_navigates_to_named_h1_without_resetting_to_eight(self):
        self.context.begin_turn()
        result = self.context.run('review_market_crt', detail(), self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.provider.calls[-1][1]['anchor_start_ny'], retained.ny('09:00'))
        self.assertEqual(result['market_context']['selection'], {**self.initial['market_context']['selection'],
                                                               'anchor_start_ny': retained.ny('09:00')})
        self.assertNotEqual(result['market_context']['scope_id'], self.initial['market_context']['scope_id'])

    def test_same_turn_detail_request_and_omitted_action_allow_range_navigation(self):
        # A recap followed by its advertised detail request can share a tool chain.
        args = detail()
        args.pop('context_action')
        result = self.context.run('review_market_crt', args, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('09:00'))
        self.context.begin_turn()
        result = self.context.run('review_market_crt', detail('10:00'), self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('10:00'))

    def test_same_scope_alias_and_equivalent_timestamp_preserve_identity(self):
        self.context.begin_turn()
        first = self.context.run('review_market_crt', detail(asset='NAS'), self.provider)
        self.context.begin_turn()
        result = self.context.run('review_market_crt', detail(asset='NASDAQ',
            anchor_start_ny='2026-10-02T13:00:00+00:00', through_ny='2026-10-02T16:00:00+00:00',
            anchor_timeframe='1H'), self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['market_context']['scope_id'], first['market_context']['scope_id'])

    def test_continue_rejects_scope_changes_and_ambiguous_anchors_before_dispatch(self):
        variations = [dict(asset='XAUUSD'), dict(date_ny='2026-10-01'), dict(shift='night'),
            dict(anchor_start_ny='2026-10-01T09:00:00-04:00'),
            dict(anchor_start_ny=retained.ny('21:00')),
            dict(anchor_start_ny=retained.ny('06:00')),
            dict(anchor_start_ny=retained.ny('12:00')),
            dict(anchor_start_ny=retained.ny('09:30')),
            dict(anchor_timeframe='H4'), dict(through_ny=retained.ny('13:00')),
            dict(through_ny=retained.ny('11:00'))]
        for fields in variations:
            with self.subTest(fields=fields):
                self.context.begin_turn()
                previous = deepcopy(self.context.selected)
                count = len(self.provider.calls)
                result = self.context.run('review_market_crt', detail(**fields), self.provider)
                self.assertEqual(result['status'], 'market_context_mismatch', result)
                self.assertEqual(len(self.provider.calls), count)
                self.assertEqual(self.context.selected, previous)
                self.assertIsNone(self.context.pending)

    def test_explicit_switch_still_changes_asset_day_and_shift(self):
        self.context.begin_turn()
        result = self.context.run('review_market_crt', detail('21:00', asset='gold',
            date_ny='2026-10-01', shift='night', anchor_start_ny='2026-10-01T21:00:00-04:00',
            through_ny='2026-10-02T00:00:00-04:00', context_action='switch'), self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected, {'asset': 'XAUUSD', 'date_ny': '2026-10-01',
            'shift': 'night', 'anchor_start_ny': '2026-10-01T21:00:00-04:00',
            'anchor_timeframe': 'H1', 'through_ny': '2026-10-02T00:00:00-04:00'})

    def test_day_and_night_context_ranges_have_closed_hourly_boundaries(self):
        for shift, clocks in [('day', ('07:00', '08:00', '09:00', '10:00', '11:00')),
                              ('night', ('19:00', '20:00', '21:00', '22:00', '23:00'))]:
            context = MarketConversation()
            context.begin_turn()
            context.run('review_market_session', {**DAY, 'shift': shift}, self.provider)
            cutoff = context.selected['through_ny']
            for clock in clocks:
                with self.subTest(shift=shift, clock=clock):
                    context.begin_turn()
                    result = context.run('review_market_crt', detail(clock, through_ny=cutoff), self.provider)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(context.selected['anchor_start_ny'], retained.ny(clock))
                    self.assertEqual(context.selected['shift'], shift)

    def test_text_named_range_wins_and_ellipsis_does_not_select_generated_default(self):
        self.context.begin_turn('Show the 9AM range Model 1', now=NOW)
        result = self.context.run('review_market_crt', detail('08:00'), self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('09:00'))
        self.context.begin_turn('When did its CSD confirm?', now=NOW)
        result = self.context.run('review_market_crt', detail('08:00'), self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('09:00'))

    def test_young_lefty_followup_routes_day_and_night_to_same_asset_date(self):
        for shift, clock in [('day', '07:00'), ('night', '19:00')]:
            context = MarketConversation()
            context.begin_turn()
            context.run('review_market_session', {**DAY, 'asset': 'XAUUSD', 'shift': shift}, self.provider)
            context.begin_turn('What about Young Lefty?', now=NOW)
            # If the model asks for another overview, the server supplies the
            # exact detail route, not an unrelated definition or current chart.
            result = context.run('review_market_session', DAY, self.provider)
            self.assertEqual(result['status'], 'selected_range_requires_detail')
            self.assertEqual(result['next_arguments']['anchor_start_ny'], retained.ny(clock))
            result = context.run(result['next_tool'], result['next_arguments'], self.provider)
            self.assertTrue(result['ok'], result)
            self.assertEqual(context.selected['asset'], 'XAUUSD')
            self.assertEqual(context.selected['date_ny'], DAY['date_ny'])
            self.assertEqual(context.selected['shift'], shift)
            self.assertEqual(context.selected['anchor_start_ny'], retained.ny(clock))

    def test_definition_only_and_conflicting_named_ranges_do_not_replace_evidence(self):
        for text in ('What is Young Lefty?', 'What does Young Lefty mean?',
                     'Define Young Lefty', 'Explain the concept of Young Lefty'):
            self.context.begin_turn(text, now=NOW)
            self.assertEqual(self.context.intent['action'], 'continue')
            self.assertNotIn('anchor_start_ny', self.context.intent['fields'])
        self.context.begin_turn('What about Young Lefty in the 9AM range?', now=NOW)
        count = len(self.provider.calls)
        result = self.context.run('review_market_crt', detail(), self.provider)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.provider.calls), count)
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('08:00'))

    def test_contextual_young_lefty_evidence_question_is_not_a_definition(self):
        self.context.begin_turn('What does Young Lefty show here?', now=NOW)
        result = self.context.run('review_market_crt', detail('08:00'), self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('07:00'))

    def test_wrong_returned_range_is_rejected_without_replacing_verified_evidence(self):
        self.context.begin_turn()
        saved = deepcopy(self.context.evidence)
        result = self.context.run('review_market_crt', detail(),
            lambda name, args: self.provider(name, {**args, 'anchor_start_ny': retained.ny('08:00')}))
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(self.context.evidence, saved)
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('08:00'))

    def test_cancelled_range_result_cannot_overwrite_a_newer_range(self):
        entered, finish = threading.Event(), threading.Event()
        def blocked(name, args):
            entered.set()
            self.assertTrue(finish.wait(3))
            return self.provider(name, args)
        self.context.begin_turn()
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.context.run, 'review_market_crt', detail(), blocked)
            self.assertTrue(entered.wait(3))
            self.context.begin_turn()
            newer = self.context.run('review_market_crt', detail('10:00'), self.provider)
            finish.set()
            stale = future.result(3)
        self.assertTrue(newer['ok'], newer)
        self.assertEqual(stale['status'], 'stale_market_context')
        self.assertEqual(self.context.selected['anchor_start_ny'], retained.ny('10:00'))

    def test_pagination_keeps_anchor_cutoff_and_identity_selectors(self):
        self.context.begin_turn()
        self.context.run('review_market_crt', detail(), self.provider)
        self.context.begin_turn()
        args = detail(detail_from_ny=retained.ny('10:10'), blessed_thief_from_ny=retained.ny('11:00'))
        result = self.context.run('review_market_crt', args, self.provider)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.provider.calls[-1][1], {k: v for k, v in args.items() if k != 'context_action'})

    def test_standalone_crt_cannot_be_silently_navigated_without_a_shift(self):
        context = MarketConversation()
        context.begin_turn()
        self.assertTrue(context.run('review_market_crt', detail(), self.provider)['ok'])
        context.begin_turn()
        result = context.run('review_market_crt', detail('10:00'), self.provider)
        self.assertEqual(result['status'], 'market_context_mismatch')
        self.assertEqual(context.selected['anchor_start_ny'], retained.ny('09:00'))

    def test_context_prompt_and_tool_contract_explain_scoped_navigation(self):
        prompt = self.context.prompt()
        self.assertIn('context_action=continue', prompt)
        self.assertIn('7AM (day) or 7PM (night)', prompt)
        for tool in contextual_tools(market.MARKET_TOOLS):
            if tool['name'] == 'review_market_crt':
                self.assertIn('returned detail_request', tool['description'])
                self.assertIn('same asset/date', tool['description'])


class RetainedConversationRangeTests(unittest.TestCase):
    def setUp(self):
        self.replay = retained.RetainedMarketReplayTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)
        self.context = MarketConversation()

    def run_tool(self, name, args):
        return market.market_tool(self.replay.db, name, args)

    def overview(self, asset='NAS100'):
        self.context.begin_turn()
        result = self.context.run('review_market_session', {**DAY, 'asset': asset}, self.run_tool)
        self.assertTrue(result['ok'], result)
        return result

    def assert_nas_nine_model(self, result):
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['market_context']['selection']['anchor_start_ny'], retained.ny('09:00'))
        first = result['review']['model1']['candles'][0]
        self.assertEqual(first['bar_open_ny'], retained.ny('10:00'))
        self.assertEqual(first['purge_source_interval']['bar_open_ny'], retained.ny('10:02'))
        self.assertEqual(first['purge_source_interval']['bar_close_ny'], retained.ny('10:03'))
        candle = result['review']['candle_lifecycle']['purge_candles'][0]
        self.assertEqual(candle['csd']['evidence']['bar_open_ny'], retained.ny('11:00'))
        self.assertEqual(candle['csd']['evidence']['confirmed_at_ny'], retained.ny('11:05'))

    def test_retained_nas_continue_selects_nine_range_ten_model_one(self):
        self.overview()
        self.context.begin_turn()
        self.assert_nas_nine_model(self.context.run('review_market_crt', detail(), self.run_tool))

    def test_returned_detail_request_is_executable_during_natural_continuity(self):
        overview = shift_voice_overview(self.overview())
        request = next(row['detail_request'] for row in overview['review']['shift_story']['ranges']
                       if row['anchor_start_ny'] == retained.ny('09:00'))
        self.context.begin_turn()
        self.assert_nas_nine_model(self.context.run(request['tool'], request['args'], self.run_tool))

    def test_contextual_gold_young_lefty_fetches_same_day_bullish_delivery(self):
        self.overview('XAUUSD')
        self.context.begin_turn('What about Young Lefty?', now=NOW)
        result = self.context.run('review_market_crt', detail('08:00', asset='XAUUSD'), self.run_tool)
        self.assertTrue(result['ok'], result)
        scope = result['market_context']['selection']
        self.assertEqual(scope['asset'], 'XAUUSD')
        self.assertEqual(scope['date_ny'], '2026-10-02')
        self.assertEqual(scope['anchor_start_ny'], retained.ny('07:00'))
        review = result['review']
        self.assertEqual(review['observed_direction'], 'bullish')
        events = {event['kind']: event for event in review['events']}
        for kind, clock in [('sell_side_purge', '08:11'), ('midpoint_observed', '08:19'),
                            ('opposing_liquidity_observed', '08:30')]:
            self.assertEqual(events[kind]['bar_open_ny'], retained.ny(clock))
        self.assertEqual(review['invalidated_at_ny'], retained.ny('09:00'))


if __name__ == '__main__':
    unittest.main()
