"""Synthetic selectors and broker reconstruction; no native chart/live claims."""
import unittest
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.range_reference import resolve, requested, matched_names
import test_range_reference as fixture

NOW = parse_time('2026-11-12T12:00:00-05:00')

class H4Selectors(unittest.TestCase):
    def test_all_six_windows_and_spoken_names(self):
        cases = [
            ('RCT', 1, 5, ('RCT', 'Rice Crown Transition', 'Asia Expansion')),
            ('London Lunch', 5, 9, ('London Lunch',)),
            ('GCT', 9, 13, ('GCT', 'Golden Candle Time', 'New York AM')),
            ('New York PM', 13, 17, ('New York PM',)),
            ('CCAO', 17, 21, ('CCAO', 'Cacao candle', 'CBDR Closure, Asia Open', 'CAB', 'CBDR + Early Asia')),
            ('TCT', 21, 1, ('TCT', 'True Chow Time', 'Asia Open')),
        ]
        for canonical, start, end, aliases in cases:
            for alias in aliases:
                with self.subTest(alias=alias):
                    text=f'2026-11-11 NAS H4 {alias}'
                    self.assertTrue(requested(text))
                    result=resolve(text,None,NOW)
                    self.assertTrue(result['ok'],result)
                    self.assertEqual(result['selectors']['range_name'],canonical)
                    self.assertIsNone(result['selectors']['shift'])
                    self.assertEqual(result['anchor_timeframe'],'H4')
                    self.assertEqual(result['anchor_start_ny'],f'2026-11-11T{start:02d}:00:00-05:00')
                    end_day='2026-11-12' if start==21 else '2026-11-11'
                    self.assertEqual(result['anchor_end_ny'],f'{end_day}T{end:02d}:00:00-05:00')
                    self.assertEqual(result['through_ny'],result['anchor_end_ny'])
                    self.assertTrue(result['closed'])

    def test_contained_alias_does_not_create_second_range(self):
        self.assertEqual(matched_names('CBDR Closure, Asia Open'), ['CCAO'])
        self.assertEqual(matched_names('CCAO and TCT'), ['CCAO','TCT'])
        self.assertFalse(resolve('yesterday NAS CCAO and TCT',None,NOW)['ok'])
        self.assertFalse(matched_names('CCAOX TCTopic RCTotal'))
        self.assertFalse(requested('What is RCT?'))
        self.assertTrue(requested('RCT', {'date_ny':'2026-11-11'}))

    def test_tct_date_is_opening_date_not_following_midnight(self):
        now=parse_time('2026-11-12T00:30:00-05:00')+40
        result=resolve('last night NAS TCT',None,now)
        self.assertEqual(result['anchor_start_ny'],'2026-11-11T21:00:00-05:00')
        self.assertEqual(result['anchor_end_ny'],'2026-11-12T01:00:00-05:00')
        self.assertEqual(result['through_ny'],'2026-11-12T00:30:00-05:00')
        self.assertFalse(result['closed'])
        today=resolve('today NAS TCT',None,now)
        self.assertEqual(today['anchor_start_ny'],'2026-11-12T21:00:00-05:00')
        self.assertFalse(today['closed'])

    def test_tct_summer_and_dst_eve_stays_local_calendar(self):
        for day,now,start_offset,end_offset in [
            ('2026-07-10','2026-07-12T12:00:00-04:00','-04:00','-04:00'),
            ('2026-10-31','2026-11-02T12:00:00-05:00','-04:00','-04:00')]:
            result=resolve(f'{day} gold TCT',None,parse_time(now))
            self.assertTrue(result['ok'],result)
            self.assertTrue(result['anchor_start_ny'].endswith(start_offset))
            self.assertTrue(result['anchor_end_ny'].endswith(end_offset))
            self.assertEqual(parse_time(result['anchor_end_ny'])-parse_time(result['anchor_start_ny']),4*3600)

    def test_native_wrong_timeframe_conflict_feed_and_invalid_date_fail_closed(self):
        for text in ('2026-02-30 NAS RCT', 'today last night NAS TCT', 'yesterday NAS H1 RCT',
                     'yesterday NAS RCT evening','yesterday NAS CCAO morning',
                     'yesterday NAS TCT morning','yesterday OANDA:NAS100 TCT'):
            self.assertFalse(resolve(text,None,NOW)['ok'],text)
        for name in ('RCT','London Lunch','GCT','New York PM','CCAO','TCT'):
            result=resolve(f'yesterday NAS native H4 {name}',None,NOW)
            self.assertEqual(result['status'],'native_chart_h4_unavailable')

    def test_name_only_correction_preserves_asset_date_and_resets_shift(self):
        previous=resolve('2026-11-11 gold CCAO',None,NOW)['selectors']
        result=resolve('actually TCT',previous,NOW)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['selectors']['asset'],'XAUUSD')
        self.assertEqual(result['anchor_start_ny'],'2026-11-11T21:00:00-05:00')

    def test_dst_rct_bounds_are_not_silently_four_elapsed_hours(self):
        for day,hours in (('2026-03-08',3),('2026-11-01',5)):
            result=resolve(f'{day} NAS RCT',None,NOW)
            self.assertTrue(result['ok'],result)
            self.assertEqual(parse_time(result['anchor_end_ny'])-parse_time(result['anchor_start_ny']),hours*3600)

class H4BrokerTests(unittest.TestCase):
    setUp=fixture.ConversationTests.setUp
    db=fixture.ConversationTests.db
    store=fixture.ConversationTests.store
    runner=fixture.ConversationTests.runner
    ask=fixture.ConversationTests.ask

    def test_six_closed_windows_read_exact_aggregated_range(self):
        for name in ('RCT','London Lunch','GCT','New York PM','CCAO','TCT'):
            with self.subTest(name=name):
                result=self.ask(f'2026-10-02 NAS H4 {name}')
                self.assertTrue(result['ok'],result)
                reference=result['range_reference']
                self.assertEqual(reference['range_kind'],'named_gtop_window')
                self.assertEqual(reference['ohlc_basis'],'aggregated_bridge_bars')
                self.assertEqual(result['review']['anchor']['bar_count'],48)
                self.assertEqual(reference['source'],'MT5 broker feed')
                self.assertFalse(result['is_live'])

    def test_forming_tct_does_not_read_future_fixture_bars(self):
        self.now=parse_time('2026-10-03T00:30:00-04:00')
        result=self.ask('2026-10-02 NAS TCT')
        self.assertEqual(result['status'],'named_range_not_closed')
        self.assertEqual(self.calls,[])

    def test_clock_transition_never_calls_fixed_h4_reader(self):
        self.now=NOW
        result=self.ask('2026-11-01 NAS RCT')
        self.assertEqual(result['status'],'named_range_clock_transition')
        self.assertEqual(self.calls,[])
        self.assertIsNone(self.context.selected)

    def test_missing_h4_coverage_stays_unverified(self):
        self.store('NAS100','USTEC.test',parse_time('2026-10-02T17:15:00-04:00'))
        result=self.ask('2026-10-02 NAS CCAO')
        self.assertEqual(result['status'],'named_range_incomplete')
        self.assertIsNone(self.context.selected)

if __name__=='__main__':
    unittest.main()
