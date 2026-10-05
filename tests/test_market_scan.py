import copy
import json
import unittest
from unittest.mock import Mock, patch

from tests import test_current_market as fixtures
from tests.test_current_market import candles, ts
from gbop_voice_web.market_data import ASSETS, market_tool
from gbop_voice_web.market_scan import scan_intent, scan_args
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.market_prefetch import prefetch_market_evidence
from gbop_voice_web.voice_payload import voice_tool_payload


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CurrentMarketTests()
        self.fixture.setUp()
        self.db = self.fixture.db
        self.now = ts('08:50')

    def tearDown(self):
        self.fixture.tearDown()

    def scan(self, **args):
        return market_tool(self.db, 'scan_young_lefty', dict(date_ny='2026-10-02', shift='day', **args), now=self.now)

    def test_all_assets_and_missing_evidence_not_absence(self):
        self.fixture.seed(self.now, candles(ts('07:00'), self.now))
        result = self.scan()
        self.assertTrue(result['ok'])
        self.assertEqual(set(result['checked_assets']), ASSETS)
        self.assertEqual(next(r for r in result['results'] if r['asset']=='NAS100')['status'], 'not_observed')
        self.assertEqual(sum(r['status']=='unavailable' for r in result['results']), 8)

    def test_other_pair_excludes_only_selected_asset(self):
        result = self.scan(exclude_asset='NAS')
        self.assertEqual(set(result['checked_assets']), ASSETS - {'NAS100'})

    def test_purge_direction_and_own_objective_are_preserved(self):
        bars = candles(ts('07:00'), self.now, low=96)
        for b in bars[:60]: b['low'] = 95
        bars[70].update(high=112, low=101, open=103, close=103)
        bars[71].update(low=99)
        self.fixture.seed(self.now, bars, asset='XAUUSD')
        row = next(r for r in self.scan()['results'] if r['asset']=='XAUUSD')
        self.assertEqual(row['status'],'observed_setup')
        self.assertEqual(row['evidence']['direction'],'bearish')
        self.assertIsNotNone(row['evidence']['first_purge'])
        self.assertIn('midpoint', row['evidence'])
        self.assertEqual(row['evidence']['detail_request']['args']['asset'],'XAUUSD')

    def test_frozen_cutoff_excludes_later_purge(self):
        bars = candles(ts('07:00'), self.now)
        bars[100]['high'] = 120
        self.fixture.seed(self.now, bars)
        result = self.scan(through_ny='2026-10-02T08:30:00-04:00')
        row=next(r for r in result['results'] if r['asset']=='NAS100')
        self.assertEqual(row['status'],'not_observed')
        self.assertEqual(row['source_cutoff_ny'],'2026-10-02T08:30:00-04:00')

    def test_capture_boundary_excludes_unobserved_future_bars(self):
        bars = candles(ts('07:00'), self.now)
        bars[100]['high']=120
        self.fixture.seed(self.now,bars,capture=ts('08:30'))
        row=next(r for r in self.scan()['results'] if r['asset']=='NAS100')
        self.assertEqual(row['status'],'not_observed')
        self.assertFalse(row['is_live'])

    def test_forming_and_missing_reference_never_verified(self):
        self.now=ts('07:45')
        self.fixture.seed(self.now,candles(ts('07:00'),self.now))
        row=next(r for r in self.scan()['results'] if r['asset']=='NAS100')
        self.assertEqual(row['status'],'forming')
        self.now=ts('08:50')
        self.fixture.seed(self.now,candles(ts('07:30'),self.now))
        row=next(r for r in self.scan()['results'] if r['asset']=='NAS100')
        self.assertEqual(row['status'],'unavailable')

    def test_invalidated_range_not_listed_as_active_setup(self):
        self.now=ts('09:30')
        bars=candles(ts('07:00'),self.now)
        bars[70]['high']=120
        bars[119].update(high=115,close=111)
        self.fixture.seed(self.now,bars)
        row=next(r for r in self.scan()['results'] if r['asset']=='NAS100')
        self.assertEqual(row['status'],'invalidated')
        self.assertIsNotNone(row['evidence']['first_purge'])

    def test_all_nine_populated_results_fit_voice_budget_without_loss(self):
        for asset in ASSETS:
            self.fixture.seed(self.now,candles(ts('07:00'),self.now),asset=asset)
        result=self.scan()
        wire=voice_tool_payload('scan_young_lefty',result)
        self.assertEqual(wire,result)
        self.assertLess(len(json.dumps(wire,separators=(',',':'))),28000)

    def test_one_failed_source_does_not_prevent_other_reads(self):
        with patch('gbop_voice_web.market_data.read_feed',side_effect=RuntimeError('private error')):
            result=self.scan()
        self.assertEqual(len(result['results']),9)
        self.assertNotIn('private error',json.dumps(result))


class ScanRoutingTests(unittest.TestCase):
    def test_screenshot_and_common_requests(self):
        for text in ('See any Young leftys anywhere?', 'What about any other pair with a young lefty',
                     'Scan all markets for Young Lefty', 'Any Young Lefties right now?'):
            self.assertTrue(scan_intent(text),text)
        for text in ('Define Young Lefty', 'Any Young Lefty on gold?', 'Save any young lefty to my journal',
                     'Watch for any Young Lefty', 'What about Young Lefty?', 'Whatever applicable'):
            self.assertFalse(scan_intent(text),text)
        self.assertTrue(scan_intent('Whatevers applicable',True))

    def test_prefetch_all_markets_cached_and_does_not_bind_a_trade(self):
        context=MarketConversation((1,2,'dm'))
        context.selected=dict(asset='NAS100',date_ny='2026-10-02',shift='day',through_ny='2026-10-02T08:50:00-04:00')
        selected=copy.deepcopy(context.selected)
        context._journal_review={'old':'trade'}
        generation=context.begin_turn('What about any other pair with a young lefty',now=ts('08:55'))
        runner=Mock(return_value={'ok':True,'through_ny':'2026-10-02T08:50:00-04:00','results':[]})
        prefetched=prefetch_market_evidence(context,runner,generation)
        self.assertIn('READ-ONLY MARKET EVIDENCE',prefetched)
        self.assertEqual(runner.call_args.args[0],'scan_young_lefty')
        self.assertEqual(runner.call_args.args[1]['exclude_asset'],'NAS100')
        context.run('scan_young_lefty',{},runner,generation=generation)
        self.assertEqual(runner.call_count,1)
        self.assertEqual(context.selected,selected)
        self.assertIsNone(context._journal_review)
        context.begin_turn('Whatevers applicable',now=ts('08:56'))
        self.assertEqual(context.required_evidence_request()['args']['through_ny'],selected['through_ny'])
        context.begin_turn('hello',now=ts('08:56'))
        context.begin_turn('Whatever applicable',now=ts('08:56'))
        self.assertIsNone(context.required_evidence_request())

    def test_new_current_scan_uses_actual_ny_date_and_night(self):
        context=MarketConversation()
        context.begin_turn('See any Young leftys anywhere?',now=ts('20:55'))
        self.assertEqual(context.required_evidence_request()['args']['shift'],'night')
        self.assertEqual(context.required_evidence_request()['args']['date_ny'],'2026-10-02')

    def test_stale_result_cannot_restore_scan_context(self):
        context=MarketConversation()
        generation=context.begin_turn('Any Young Leftys anywhere?',now=ts('08:50'))
        def runner(name,args):
            context.invalidate()
            return {'ok':True,'results':[]}
        result=context.run('scan_young_lefty',{},runner,generation=generation)
        self.assertFalse(result['ok'])
        self.assertIsNone(context._scan_result)
        self.assertIsNone(context._previous_scan)

    def test_single_market_tool_cannot_substitute_for_scan(self):
        context=MarketConversation()
        generation=context.begin_turn('Any Young Leftys anywhere?',now=ts('08:50'))
        runner=Mock()
        result=context.run('review_current_market',{'asset':'NAS100'},runner,generation=generation)
        self.assertEqual(result['status'],'cross_asset_scan_required')
        runner.assert_not_called()

    def test_explicit_today_refreshes_old_historical_cutoff(self):
        args=scan_args('any Young Lefty tonight?',{'date_ny':'2026-10-04','shift':'night'},
            {'asset':'NAS100','date_ny':'2026-10-02','shift':'day','through_ny':'2026-10-02T12:00:00-04:00'},ts('20:55'))
        self.assertEqual(args['date_ny'],'2026-10-04')
        self.assertIsNone(args['through_ny'])

    def test_named_scan_followup_keeps_its_seven_range_and_cutoff(self):
        context=MarketConversation()
        generation=context.begin_turn('Any Young Leftys anywhere?',now=ts('08:50'))
        detail=dict(asset='XAUUSD',anchor_start_ny='2026-10-02T07:00:00-04:00',
                    anchor_timeframe='H1',through_ny='2026-10-02T08:50:00-04:00')
        context.run('scan_young_lefty',{},lambda *a:{'ok':True,'date_ny':'2026-10-02',
            'shift':'day','through_ny':detail['through_ny'],'results':[{'asset':'XAUUSD',
            'evidence':{'detail_request':{'args':detail}}}]},generation=generation)
        context.begin_turn('What about gold?',now=ts('08:55'))
        for key,value in detail.items():
            self.assertEqual(context.requested[key],value)

    def test_audio_scan_uses_prior_window_and_respects_cancellation(self):
        context=MarketConversation()
        context.begin_turn(None,now=ts('08:50'))
        runner=Mock(return_value={'ok':True,'through_ny':'2026-10-02T08:50:00-04:00','results':[]})
        context.run('scan_young_lefty',{},runner)
        context.begin_turn(None,now=ts('08:55'))
        context.run('scan_young_lefty',{},runner)
        self.assertEqual(runner.call_args.args[1]['through_ny'],'2026-10-02T08:50:00-04:00')
        context.close()
        self.assertIsNone(context._previous_scan)


if __name__ == '__main__':
    unittest.main()
