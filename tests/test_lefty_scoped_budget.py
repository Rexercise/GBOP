"""Synthetic same-parent recaps through realistic scoped market responses."""
from copy import deepcopy
import unittest

from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.market_data import market_tool
from gbop_voice_web.voice_payload import (_compact_fact_symbols, _compact_record_tables,
    _encoded_size, voice_tool_payload)
from test_chronological_transport import expand
from test_current_market import CurrentMarketTests
from test_young_lefty_delivery import START, sequence


class ScopedLeftyBudgetTests(unittest.TestCase):
    def test_two_asset_day_night_scoped_responses_preserve_every_fact(self):
        for asset in ('NAS100', 'XAUUSD'):
            for hours, shift in ((0, 'day'), (12, 'night')):
                with self.subTest(asset=asset, shift=shift):
                    fixture = CurrentMarketTests()
                    fixture.setUp()
                    try:
                        start = START + hours*3600
                        now = start + 18000
                        rows = sequence(start, scale=.1 if asset == 'XAUUSD' else 1,
                                        shift=4000 if asset == 'XAUUSD' else 0)
                        fixture.seed(now, rows, asset=asset)
                        context = MarketConversation()
                        context.begin_turn()
                        raw = context.run('review_market_session',
                            {'asset': asset, 'date_ny': '2026-06-11', 'shift': shift},
                            lambda name, args: market_tool(fixture.db, name, args, now=now))
                        self.assertTrue(raw['ok'])
                        before = deepcopy(raw)
                        wire = voice_tool_payload('review_market_session', raw)
                        self.assertTrue(wire['ok'], wire)
                        self.assertLessEqual(_encoded_size(wire), 12000)
                        self.assertEqual(wire['voice_view']['character_budget'], 12000)
                        self.assertEqual(raw, before)
                        self.assertEqual(wire, voice_tool_payload('review_market_session', raw))
                        actual = expand(wire)['review']['shift_synopsis']
                        expected = raw['review']['shift_synopsis']
                        for key in ('ranges', 'chronological_context', 'active_range_context',
                                    'shift_end', 'spoken_summary'):
                            self.assertEqual(actual[key], expected[key], key)
                        self.assertIsInstance(wire['review']['shift_synopsis']['spoken_summary'], str)
                        self.assertIn('Triple purge', wire['review']['shift_synopsis']['spoken_summary'])
                        self.assertIsInstance(wire['review']['shift_synopsis']['range_index'], list)
                        for key in ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'):
                            self.assertEqual(wire['market_context'][key], raw['market_context'][key])
                        young = next(row for row in actual['ranges'] if row.get('play') == 'Young Lefty')
                        self.assertIsNone(young['young_lefty_context']['selected_direction'])
                        self.assertEqual(young['young_lefty_context']['delivery_recap']['continuation']['legs'][0]['leg_index'], 3)
                    finally:
                        fixture.tearDown()

    def test_literal_dollar_text_and_exact_clock_suffix_round_trip(self):
        synopsis = {'ranges': [dict(
            unknown_literal='$0' if index % 2 else '$$literal',
            repeated_qualification='unverified_missing_evidence',
            exact_time=f'2026-11-01T01:{index:02}:37.250' + ('-04:00' if index % 2 else '-05:00'),
            repeated_exact_time=f'2026-11-01T01:{index:02}:37.250' + ('-04:00' if index % 2 else '-05:00'),
            prior_time='2026-06-11T07:00:00-04:00',
            low=90+index, high=110+index) for index in range(12)]}
        before = deepcopy(synopsis)
        self.assertTrue(_compact_record_tables(synopsis, 'ranges'))
        self.assertTrue(_compact_fact_symbols(synopsis))
        self.assertEqual({parts[1] for parts in synopsis['fact_clock_templates']},
                         {':37.250-04:00', ':37.250-05:00'})
        actual = expand({'review': {'shift_synopsis': synopsis}})['review']['shift_synopsis']
        self.assertEqual(actual, before)

    def test_numeric_source_keys_never_become_symbol_aliases(self):
        synopsis = {'ranges': [{'0': index, 'long_repeated_field': 'same-value'} for index in range(12)]}
        before = deepcopy(synopsis)
        self.assertTrue(_compact_record_tables(synopsis, 'ranges'))
        encoded = deepcopy(synopsis)
        self.assertFalse(_compact_fact_symbols(synopsis))
        self.assertEqual(synopsis, encoded)
        self.assertEqual(expand({'review': {'shift_synopsis': synopsis}})['review']['shift_synopsis'], before)

    def test_every_existing_codec_header_blocks_table_creation_without_overwrite(self):
        for suffix in ('columns', 'defaults', 'strings', 'texts', 'keys', 'clock_templates'):
            for nested in (False, True):
                with self.subTest(suffix=suffix, nested=nested):
                    synopsis = {'ranges': [{'long_repeated_field': 'same', 'index': index}
                                           for index in range(30)]}
                    target = synopsis['ranges'][0] if nested else synopsis
                    target['fact_' + suffix] = {'unknown': 'must survive'}
                    before = deepcopy(synopsis)
                    self.assertFalse(_compact_record_tables(synopsis, 'ranges'))
                    self.assertEqual(synopsis, before)

    def test_existing_symbol_headers_block_symbols_without_overwrite(self):
        for suffix in ('keys', 'clock_templates'):
            with self.subTest(suffix=suffix):
                synopsis = {'ranges': [{'long_repeated_field': 'same', 'index': index}
                                       for index in range(30)]}
                self.assertTrue(_compact_record_tables(synopsis, 'ranges'))
                synopsis['fact_' + suffix] = {'unknown': 'must survive'}
                before = deepcopy(synopsis)
                self.assertFalse(_compact_fact_symbols(synopsis))
                self.assertEqual(synopsis, before)


if __name__ == '__main__':
    unittest.main()
