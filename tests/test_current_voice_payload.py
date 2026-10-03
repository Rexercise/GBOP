"""Bounded on-demand presentation on retained bars; no model or live requests."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest

from gbop_voice_web import market_data as market
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.current_market import review_current_market
from gbop_voice_web.voice_payload import voice_tool_payload, CURRENT_OVERVIEW_TARGET_CHARS
import test_retained_market_replays as retained
ny = retained.ny
from test_voice_payload_budget import expanded


class CurrentVoicePayloadTests(unittest.TestCase):
    def setUp(self):
        self.replay = retained.RetainedMarketReplayTests()
        self.replay.setUp()
        self.addCleanup(self.replay.doCleanups)

    def current(self, asset, clock):
        now = parse_time(ny(clock)) + 31
        def iso(t):
            return datetime.fromtimestamp(t, timezone.utc).isoformat()
        def feed(db, asset, now=now):
            return {**self.replay.feed(db, asset), 'status': 'fresh', 'is_live': True,
                    'tick_age_seconds': 5, 'capture_age_seconds': 10, 'received_age_seconds': 7,
                    'captured_at_utc': iso(now - 10), 'received_at_utc': iso(now - 7),
                    'tick_time_utc': iso(now - 5)}
        market.read_feed.side_effect = feed
        result = review_current_market(self.replay.db, feed(None, asset), {'asset': asset}, now)
        result['market_context'] = {
            'source_tool': 'review_current_market', 'selection': deepcopy(result['review']['current_scope']),
            'scope_id': 'scope_current_fixture', 'evidence_id': 'evidence_current_fixture',
            'limits': 'Current retained source bars only.', 'recap': {'unused': 'x' * 10000},
            'range_outcomes': ['duplicate']}
        return result

    def test_all_current_stages_keep_scope_and_freshness_under_hard_ceiling(self):
        for asset in ('NAS100', 'XAUUSD'):
            for clock in ('07:30', '08:50', '09:20', '10:40', '11:50', '12:10'):
                with self.subTest(asset=asset, clock=clock):
                    source = self.current(asset, clock)
                    before = deepcopy(source)
                    wire = voice_tool_payload('review_current_market', source)
                    self.assertEqual(source, before)
                    self.assertTrue(wire['ok'], wire)
                    self.assertEqual(wire['voice_view']['kind'], 'current_market')
                    self.assertLessEqual(len(json.dumps(wire, separators=(',', ':'))), CURRENT_OVERVIEW_TARGET_CHARS)
                    view = expanded(wire, wire)
                    for key in ('as_of_ny', 'analysis_cutoff_ny', 'observed_through_ny', 'current_scope',
                                'session_clock', 'freshness', 'selection_status', 'ranges', 'paired_context'):
                        self.assertEqual(view['review'][key], source['review'][key])
                    for key in ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'):
                        self.assertEqual(view['market_context'][key], source['market_context'][key])
                    self.assertNotIn('recap', view['market_context'])
                    self.assertEqual(view['market_context']['evidence_ref'], '#/review')
                    for key in ('anchor', 'model1', 'candle_lifecycle', 'events', 'shift_story', 'shift_synopsis'):
                        self.assertNotIn(key, view['review'])
                    self.assertFalse(view['review']['freshness']['instantaneous_tick_observation'])

    def test_eight_fifty_keeps_forming_eight_and_independent_seven(self):
        source = self.current('XAUUSD', '08:50')
        wire = voice_tool_payload('review_current_market', source)
        review = expanded(wire, wire['review'])
        self.assertEqual(review['session_clock']['phase'], 'pre_shift')
        self.assertEqual(review['analysis_cutoff_ny'], ny('08:50'))
        seven, eight = review['ranges']
        self.assertEqual((seven['play'], seven['anchor']['complete']), ('Young Lefty', True))
        self.assertEqual((eight['play'], eight['anchor']['forming']), ('9ate8', True))
        self.assertEqual(eight['setup_status'], 'pending_reference_close')
        self.assertFalse(eight['anchor']['complete'])
        self.assertFalse(eight['model1_count'])
        self.assertEqual(review['current_candle']['status'], 'forming')
        self.assertEqual(seven['detail_request']['args']['through_ny'], ny('08:50'))
        self.assertEqual(review['freshness']['collector_interval_seconds_approx'], 31)
        for key, value in (('capture_age_seconds', 10), ('received_age_seconds', 7), ('tick_age_seconds', 5)):
            self.assertEqual(review['freshness'][key], value)

    def test_raw_lifecycle_is_retained_for_binding_but_never_dumped(self):
        source = self.current('NAS100', '10:40')
        source['review']['candle_lifecycle']['raw_padding'] = 'x' * 100000
        wire = voice_tool_payload('review_current_market', source)
        self.assertTrue(wire['ok'])
        self.assertNotIn('raw_padding', json.dumps(wire))
        self.assertEqual(len(source['review']['candle_lifecycle']['raw_padding']), 100000)

    def test_oversized_metadata_fails_bounded_without_shift_fallback(self):
        source = self.current('NAS100', '10:40')
        source['transport_metadata'] = 'x' * 50000
        failure = voice_tool_payload('review_current_market', source)
        self.assertFalse(failure['ok'])
        self.assertEqual(failure['status'], 'voice_current_budget_exceeded')
        self.assertLessEqual(len(json.dumps(failure, separators=(',', ':'))), CURRENT_OVERVIEW_TARGET_CHARS)
        self.assertNotIn('review', failure)
        self.assertNotIn('evidence_ref', failure['market_context'])
        self.assertEqual(failure['detail_request']['args']['through_ny'], ny('10:40'))


if __name__ == '__main__':
    unittest.main()
