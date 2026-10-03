"""Actual broker-history overview budget; no live calls or invented prices."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_OVERVIEW_TARGET_CHARS
from gbop_voice_web.voice_runtime import compact_voice_tool_result
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

    def assert_overview(self, source, *, context_size=0):
        if context_size:
            source['market_context'] = {
                'selection': {'asset': source['asset'], 'date_ny': source['review']['date_ny'],
                              'shift': source['review']['shift']},
                'evidence': {'scope_id': 'fixture-scope', 'evidence_id': 'fixture-evidence',
                             'verified_snapshot': 'x' * context_size}}
        saved = deepcopy(source)
        overview = voice_tool_payload('review_market_session', source)
        self.assertEqual(source, saved)
        self.assertTrue(overview['ok'], overview)
        self.assertLessEqual(encoded_size(overview), SHIFT_OVERVIEW_TARGET_CHARS)
        self.assertEqual(overview.get('market_context'), source.get('market_context'))
        review, original = overview['review'], source['review']
        self.assertEqual((review['date_ny'], review['shift']), (original['date_ny'], original['shift']))
        self.assertEqual(review['shift_recap']['spoken_summary'], original['shift_story']['recap']['spoken_summary'])
        self.assertEqual(review['shift_recap'].get('evidence_precedence'), original['shift_story']['recap'].get('evidence_precedence'))
        for key in ('hourly_crt_summary', 'range_summaries'):
            self.assertEqual(expanded(overview, review['shift_recap'][key]), original['shift_story']['recap'][key])
        def assert_pair(actual, raw):
            actual = expanded(overview, actual)
            self.assertEqual(actual.get('recap_eligible'), raw.get('recap_eligible'))
            for event, original_event in zip(actual['events'], raw['events']):
                self.assertEqual(event.get('setup_interval'), original_event.get('setup_interval'))
                self.assertEqual(event.get('recap_eligible'), original_event.get('recap_eligible'))
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
                'asset': source['asset'], 'anchor_start_ny': raw['anchor_start_ny'],
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
                overview = self.assert_overview(source, context_size=2900)
                self.assertLess(encoded_size(overview), old_size * .25)

    def test_actual_night_assets_keep_pair_scope_h1_truth_and_inherited_identity(self):
        replay = self.replay(crypto.CryptoShiftReplayTests)
        for asset in ('BTCUSD', 'ETHUSD'):
            with self.subTest(asset=asset):
                source = replay.tool(asset)
                overview = self.assert_overview(source, context_size=2900)
                for actual, raw in zip(overview['review']['paired_smt']['events'], source['review']['paired_smt']['events']):
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
        later = overview['review']['shift_story']['ranges'][1]
        columns = overview['voice_view']['model1_outcome_columns']
        outcomes = [dict(zip(columns, row)) for row in later['candle_lifecycle']['model1_outcome_rows']]
        for clock, quality, price, touch in [('10:00', 'clean', 30930.59, '10:59'),
                                            ('10:10', 'not_clean', 30963.6, '10:52')]:
            row = next(r for r in outcomes if f'T{clock}:' in r['model1_bar_open_ny'])
            self.assertEqual(row['soup_structural_quality'], quality)
            self.assertTrue(row['csd_candle_open_ny'])
            self.assertEqual(row['local_crt_invalidated_at_ny'], '2026-10-02T10:20:00-04:00')
            self.assertEqual(row['physical_local_function_outcome'], 'opposing_liquidity_delivered')
            self.assertEqual(row['parent_range_function_outcome'], 'opposing_liquidity_delivered')
            objective = dict(zip(overview['voice_view']['own_objective_columns'], row['own_opposing_liquidity']))
            self.assertEqual(objective['level'], price)
            self.assertEqual(objective['touch_bar_open_ny'], f'2026-10-02T{touch}:00-04:00')
            self.assertEqual(objective['relative_to_model1_invalidation'], 'after_model1_invalidation')

    def test_oversized_story_fails_explicitly_with_exact_range_requests_not_raw_truncation(self):
        replay = self.replay(retained.RetainedMarketReplayTests)
        source = replay.tool('NAS100')
        source['review']['shift_story']['recap']['spoken_summary'] += ' additional detail' * 5000
        source['market_context'] = {'selection': {'asset': 'NAS100', 'shift': 'day'}}
        result = voice_tool_payload('review_market_session', source)
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
            ('get_journal_history', {'ok': True, 'entries': []})]:
            self.assertEqual(voice_tool_payload(name, source), compact_voice_tool_result(name, source))


if __name__ == '__main__':
    unittest.main()
