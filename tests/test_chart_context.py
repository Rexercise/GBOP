import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from gbop_voice_web import shared_gtop_rules as rules
from gbop_voice_web.chart_context import parse_snapshot, compare_snapshot, compare_chart_context
from gbop_voice_web.candle_evidence import parse_time, stamp
from gbop_voice_web.market_routes import market_router

START = parse_time('2026-10-02T09:00:00-04:00')
NOW = START + 8*3600


def snapshot():
    return dict(schema='gtop.chart.v1', rules_version=rules.RULESET_VERSION, rules_hash=rules.RULESET_SHA256,
        source='TradingView', symbol='OANDA:NAS100USD', chart_tf='5', range_tf='240',
        range_open=START, range_end=START+4*3600, high=110, low=90, cutoff=NOW,
        captured_at=NOW, timezone='America/New_York', range_definition='gtop_ny_1_5_9', coverage='closed_source')


def broker():
    return dict(ok=True, asset='NAS100', source='MT5 broker feed', symbol='USTECm', status='fresh',
        review=dict(anchor_timeframe='H4', anchor=dict(complete=True, start_ny=stamp(START),
            end_ny=stamp(START+4*3600), high=110, low=90), range_observation_coverage=dict(complete=True)))


class ChartContextTests(unittest.TestCase):
    def test_matching_geometry_does_not_establish_matching_feed_or_live_connection(self):
        chart, feed = snapshot(), broker()
        before = deepcopy((chart, feed))
        result = compare_snapshot(parse_snapshot(json.dumps(chart), NOW), feed, NOW)
        self.assertFalse(result['same_context_verified'])
        self.assertFalse(result['live_chart_connected'])
        self.assertEqual(result['status'], 'unverified_feed_equivalence')
        self.assertEqual((chart, feed), before)

    def test_different_bounds_clock_range_or_rules_fail_match(self):
        for key, value in [('high',111), ('rules_version','0.9'), ('rules_hash','old'),
                           ('timezone','Etc/GMT+5'), ('range_open',START+3600), ('range_tf','60')]:
            with self.subTest(field=key):
                chart = snapshot(); chart[key] = value
                result = compare_snapshot(chart, broker(), NOW)
                self.assertEqual(result['status'], 'mismatched')
                self.assertFalse(result['same_context_verified'])

    def test_stale_capture_and_feed_are_explicit_without_erasing_historical_evidence(self):
        chart = snapshot(); chart['captured_at'] -= 1000; chart['cutoff'] -= 1000
        feed = broker(); feed['status'] = 'stale'
        result = compare_snapshot(chart, feed, NOW)
        checks = {c['field']:c['status'] for c in result['checks']}
        self.assertEqual(checks['snapshot_freshness'], 'stale')
        self.assertEqual(checks['broker_freshness'], 'stale')
        self.assertEqual(checks['range_prices'], 'match')

    def test_missing_data_and_native_h4_are_not_verified(self):
        chart = snapshot(); chart.update(range_definition='native_chart',coverage='unverified')
        result = compare_snapshot(chart, dict(ok=False), NOW)
        checks = {c['field']:c['status'] for c in result['checks']}
        self.assertEqual(checks['range'], 'unverified')
        self.assertEqual(checks['H4_definition'], 'unverified')
        self.assertFalse(result['same_context_verified'])

    def test_invalid_future_nonfinite_and_oversized_snapshots_are_rejected(self):
        for field, value in [('captured_at',NOW+100), ('cutoff',START), ('high',float('nan')),
                             ('low',110), ('range_open',True), ('range_tf','3'), ('source','MT5')]:
            with self.subTest(field=field):
                chart = snapshot(); chart[field] = value
                with self.assertRaises((ValueError, TypeError)):
                    parse_snapshot(json.dumps(chart), NOW)
        with self.assertRaises(ValueError):
            parse_snapshot(' '*4097, NOW)

    def test_comparison_queries_exact_snapshot_anchor_and_cutoff_without_ingestion(self):
        with patch('gbop_voice_web.market_data.market_tool', return_value=broker()) as read:
            result = compare_chart_context(None, dict(asset='NAS100',snapshot_json=json.dumps(snapshot())), NOW)
        self.assertTrue(result['ok'])
        self.assertEqual(read.call_args.args[1], 'review_market_crt')
        self.assertEqual(read.call_args.args[2], dict(asset='NAS100',anchor_start_ny=stamp(START),
            through_ny=stamp(NOW),anchor_timeframe='H4'))

    def test_authenticated_route_is_read_only_and_bounded(self):
        async def allow(request): return {'user_id':1}
        app = FastAPI(); app.include_router(market_router(None, allow))
        client = TestClient(app)
        with patch('gbop_voice_web.market_routes.market_tool', return_value=dict(ok=True, same_context_verified=False)) as compare:
            self.assertEqual(client.post('/api/market/chart-context/compare', json=dict(asset='NAS100',snapshot_json='{}')).status_code,200)
            self.assertEqual(compare.call_args.args[1], 'compare_butterfly_context')
            self.assertEqual(client.post('/api/market/chart-context/compare', content=' '*8193).status_code,413)
            self.assertEqual(compare.call_count,1)
        async def deny(request): raise HTTPException(401,'Sign in')
        app = FastAPI(); app.include_router(market_router(None, deny))
        with patch('gbop_voice_web.market_routes.market_tool') as compare:
            self.assertEqual(TestClient(app).post('/api/market/chart-context/compare',json={}).status_code,401)
            compare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
