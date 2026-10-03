"""Actual broker-history overview budget; no live calls or invented prices."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.voice_payload import voice_tool_payload, shift_voice_overview, SHIFT_OVERVIEW_TARGET_CHARS
from gbop_voice_web.voice_runtime import compact_voice_tool_result
from gbop_voice_web.market_conversation import MarketConversation
import test_retained_market_replays as retained
import test_crypto_shift_replays as crypto


def encoded_size(value):
    return len(json.dumps(value, separators=(',', ':')))


def identities(overview, row):
    model = row['model1']
    if 'candles' in model:
        return model['candles']
    return [dict(zip(overview['voice_view']['model1_identity_columns'], values))
            for values in model['identity_rows']]


def expanded(overview, value):
    if isinstance(value, dict) and set(value) == {'same_evidence_as'}:
        path = value['same_evidence_as']
        if not path.startswith('#/'):
            path = '#/' + path.replace('.', '/')
        target = overview
        for part in path[2:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            target = target[int(part)] if isinstance(target, list) else target[part]
        return expanded(overview, target)
    if isinstance(value, dict) and 'join_text_from' in value:
        target = expanded(overview, {'same_evidence_as': value['join_text_from']})
        return value['separator'].join(row['text'] for row in target if row['role'] == value['where_role'])
    if isinstance(value, dict):
        return {key: expanded(overview, child) for key, child in value.items()}
    if isinstance(value, list):
        return [expanded(overview, child) for child in value]
    return value


class VoicePayloadBudgetTests(unittest.TestCase):
    def replay(self, cls):
        replay = cls()
        replay.setUp()
        self.addCleanup(replay.doCleanups)
        return replay

    def assert_overview(self, source, *, with_context=False):
        if with_context:
            context = MarketConversation()
            context.begin_turn()
            source = context.run('review_market_session', {
                'asset': source['asset'], 'date_ny': source['review']['date_ny'],
                'shift': source['review']['shift']}, lambda name, args: source)
        saved = deepcopy(source)
        overview = shift_voice_overview(source)
        self.assertEqual(source, saved)
        self.assertTrue(overview['ok'], overview)
        self.assertLessEqual(encoded_size(overview), SHIFT_OVERVIEW_TARGET_CHARS)
        if with_context:
            for key in ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'):
                self.assertEqual(overview['market_context'][key], source['market_context'][key])
            self.assertEqual(overview['market_context']['evidence_ref'], '#/review')
        else:
            self.assertEqual(overview.get('market_context'), source.get('market_context'))
        review, original = expanded(overview, overview['review']), source['review']
        self.assertEqual((review['date_ny'], review['shift']), (original['date_ny'], original['shift']))
        self.assertEqual(review['shift_recap']['spoken_summary'], original['shift_story']['recap']['spoken_summary'])
        self.assertEqual(review['shift_recap'].get('evidence_precedence'), original['shift_story']['recap'].get('evidence_precedence'))
        for key in ('hourly_crt_summary', 'range_summaries'):
            if key in review['shift_recap']:
                self.assertEqual(review['shift_recap'][key], original['shift_story']['recap'][key])
            else:
                self.assertIn('range_recap_prose_omitted', overview['voice_view'])
        def assert_pair(actual, raw):
            actual = expanded(overview, actual)
            self.assertEqual(actual.get('recap_eligible'), raw.get('recap_eligible'))
            for event, original_event in zip(actual['events'], raw['events']):
                self.assertEqual(event.get('recap_eligible'), original_event.get('recap_eligible'))
                if event.get('detail_omitted'):
                    self.assertEqual(original_event['boneless_status'], 'disqualified')
                    self.assertIs(original_event['recap_eligible'], False)
                    for part in ('setup_interval', 'scope'):
                        for key, value in event[part].items():
                            self.assertEqual(value, original_event[part][key])
                    self.assertNotIn('objective_status', event)
                    self.assertNotIn('paired_model1', event)
                else:
                    self.assertEqual(event.get('setup_interval'), original_event.get('setup_interval'))
                    self.assertEqual(event['scope'], {k:v for k,v in original_event.get('scope', {}).items() if k != 'response_contract'})
                    self.assertEqual(event['paired_model1'].get('smt_qualified_at_ny'), original_event.get('paired_model1', {}).get('smt_qualified_at_ny'))
        assert_pair(review['paired_smt'], original['paired_smt'])
        for actual, raw in zip(review['paired_context']['ranges'], original['paired_context']['ranges']):
            assert_pair(actual['paired_review'], raw['paired_review'])
        story = review['shift_story']
        for key in ('hourly_progression', 'range_transitions', 'active_anchor_ny', 'progression_complete'):
            self.assertEqual(story[key], original['shift_story'][key])
        self.assertEqual(len(story['ranges']), len(original['shift_story']['ranges']))
        for row, raw in zip(story['ranges'], original['shift_story']['ranges']):
            for key in ('anchor_start_ny', 'selected_at_ny', 'role', 'status', 'invalidated_at_ny', 'variant_evidence'):
                self.assertEqual(row[key], raw[key])
            self.assertEqual(row['observation_coverage']['complete'], raw['observation_coverage']['complete'])
            self.assertEqual(row.get('scoped_coverage'), raw.get('scoped_coverage'))
            for actual, target in zip(row['objectives'], raw['objectives']):
                for key in ('objective', 'level', 'status', 'spoken_label', 'scope'):
                    if key in target:
                        self.assertEqual(actual[key], target[key])
                if target.get('evidence'):
                    for key in ('bar_open_ny', 'bar_close_ny', 'precision_seconds', 'exact_tick_time_known'):
                        self.assertEqual(actual['evidence'].get(key), target['evidence'].get(key))
            actual_ids = identities(overview, row)
            self.assertEqual(len(actual_ids), len(raw['model1']['candles']))
            for actual, candle in zip(actual_ids, raw['model1']['candles']):
                for key in ('identity', 'timeframe', 'bar_open_ny', 'bar_close_ny', 'identified_at_ny',
                            'purged_side', 'purged_level', 'purge_source_interval'):
                    self.assertEqual(actual.get(key), candle.get(key))
            self.assertEqual(row['detail_request'], {'tool': 'review_market_crt', 'args': {
                'asset': source['asset'], 'context_action': 'continue', 'anchor_start_ny': raw['anchor_start_ny'],
                'anchor_timeframe': 'H1', 'through_ny': story['end_ny']}})
        self.assertTrue(overview['voice_view']['detail_omitted'])
        self.assertIn('Do not infer absence', overview['voice_view']['note'])
        return overview

    def test_actual_day_assets_fit_strict_budget_with_pinned_context(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        for asset in ('NAS100', 'SPX', 'XAUUSD', 'XAGUSD'):
            with self.subTest(asset=asset):
                source = replay.tool(asset)
                old_size = encoded_size(compact_voice_tool_result('review_market_session', source))
                overview = self.assert_overview(source, with_context=True)
                self.assertLess(encoded_size(overview), old_size * .25)

    def test_actual_night_assets_keep_pair_scope_h1_truth_and_inherited_identity(self):
        replay = self.replay(crypto.CryptoShiftReplayTests)
        for asset in ('BTCUSD', 'ETHUSD'):
            with self.subTest(asset=asset):
                source = replay.tool(asset)
                overview = self.assert_overview(source, with_context=True)
                pair = expanded(overview, overview['review']['paired_smt'])
                for actual, raw in zip(pair['events'], source['review']['paired_smt']['events']):
                    if actual.get('detail_omitted'):
                        self.assertEqual(raw['boneless_status'], 'disqualified')
                        self.assertFalse(raw['recap_eligible'])
                        continue
                    self.assertEqual(actual['scope'], {k:v for k,v in raw['scope'].items() if k != 'response_contract'})
                    self.assertEqual(actual['paired_model1']['status'], raw['paired_model1']['status'])
                    if 'boneless_reference' in raw['paired_model1']:
                        self.assertEqual(actual['paired_model1']['boneless_reference']['bar_open_ny'],
                                         raw['paired_model1']['boneless_reference']['bar_open_ny'])
                btc = next(row for row in overview['review']['shift_story']['ranges']
                           if 'T20:00:' in row['anchor_start_ny'])
                if asset == 'BTCUSD':
                    self.assertEqual(btc['objectives'][0]['status'], 'observed_after_purge')
                    self.assertEqual(btc['objectives'][1]['status'], 'not_observed_before_invalidation')
                    self.assertEqual(btc['invalidated_at_ny'], '2026-10-03T00:00:00-04:00')

    def test_selected_model1_local_function_and_parent_outcomes_remain_separate(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        source = replay.tool('NAS100')
        overview = self.assert_overview(source)
        later = expanded(overview, overview['review']['shift_story']['ranges'][1])
        outcomes = later['candle_lifecycle']['model1_outcomes']
        self.assertEqual(len(outcomes), 1)
        card = outcomes[0]
        self.assertEqual(card['bar_open_ny'], '2026-10-02T10:00:00-04:00')
        soup = card['super_soup']
        self.assertEqual(soup['structural_quality'], 'clean')
        self.assertEqual(soup['pre_csd_status'], 'observed_before_csd')
        self.assertEqual(soup['candle']['bar_open_ny'], '2026-10-02T10:05:00-04:00')
        self.assertEqual(soup['structure_known_at_ny'], '2026-10-02T10:10:00-04:00')
        self.assertEqual(card['csd']['candle']['bar_open_ny'], '2026-10-02T11:00:00-04:00')
        self.assertEqual(soup['local_crt_invalidated_at_ny'], '2026-10-02T10:20:00-04:00')
        self.assertEqual(soup['local_function_outcome'], 'opposing_liquidity_delivered')
        self.assertEqual(soup['parent_function_outcome'], 'opposing_liquidity_delivered')
        objective = soup['local_function_objectives']['opposing_liquidity']
        self.assertEqual(objective['level'], 30930.59)
        self.assertEqual(objective['source_interval']['bar_open_ny'], '2026-10-02T10:59:00-04:00')
        self.assertEqual(objective['relative_to_model1_invalidation'], 'after_model1_invalidation')
        self.assertGreater(later['candle_lifecycle']['detail_omissions']['omitted_lifecycle_card_count'], 0)
        self.assertIn('2026-10-02T10:10:00-04:00', [x['bar_open_ny'] for x in later['model1']['candles']])

    def test_oversized_story_fails_explicitly_with_exact_range_requests_not_raw_truncation(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        source = replay.tool('NAS100')
        source['review']['shift_story']['recap']['spoken_summary'] += ' additional detail' * 5000
        source['market_context'] = {'selection': {'asset': 'NAS100', 'shift': 'day'}}
        result = shift_voice_overview(source)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'voice_overview_budget_exceeded')
        self.assertEqual(result['market_context'], source['market_context'])
        self.assertLessEqual(encoded_size(result), SHIFT_OVERVIEW_TARGET_CHARS)
        self.assertEqual(len(result['range_index']), 4)
        self.assertEqual(result['range_index'][1]['detail_request']['args']['anchor_start_ny'], '2026-10-02T09:00:00-04:00')
        self.assertIn('full shift was not supplied', result['message'])

    def test_raw_pages_failures_and_nonmarket_results_keep_existing_contract(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        for name, source in [
            ('inspect_market_candles', {'review': {'candles': [{'start_ny': str(i)} for i in range(8)]}}),
            ('review_market_session', {'ok': False, 'status': 'shift_unavailable'}),
            ('get_trade_state', {'ok': True, 'entries': []})]:
            self.assertEqual(voice_tool_payload(name, source), compact_voice_tool_result(name, source))


if __name__ == '__main__':
    unittest.main()
