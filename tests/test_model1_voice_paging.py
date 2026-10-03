"""Identified Model 1 facts and raw OHLC tables have different schemas."""
from copy import deepcopy
import unittest

from gbop_voice_web.voice_runtime import compact_voice_tool_result


class Model1VoicePagingTests(unittest.TestCase):
    def test_identified_candles_are_not_raw_pages(self):
        rows = [{'candle_open_ny': f'2026-10-02T10:{i * 5:02d}:00-04:00',
                 'direction': 'bearish', 'entry_confirmed': False} for i in range(6)]
        payload = {'model1': {'candles': rows, 'status': 'observed'}}
        saved = deepcopy(payload)
        result = compact_voice_tool_result('review_market_crt', payload)
        self.assertEqual(result, saved)
        self.assertEqual(payload, saved)
        self.assertEqual(len(result['model1']['candles']), 6)
        self.assertNotIn('next_start_ny', result['model1'])

    def test_raw_table_still_pages_without_losing_model1_facts(self):
        raw = [{'start_ny': f'2026-10-02T10:{i * 5:02d}:00-04:00'} for i in range(6)]
        model1 = [{'candle_open_ny': row['start_ny']} for row in raw]
        payload = {'assigned_candles': {'candles': raw}, 'model1': {'candles': model1}}
        saved = deepcopy(payload)
        result = compact_voice_tool_result('review_market_crt', payload)
        self.assertEqual(len(result['assigned_candles']['candles']), 4)
        self.assertEqual(result['assigned_candles']['next_start_ny'], raw[4]['start_ny'])
        self.assertEqual(result['model1']['candles'], model1)
        self.assertEqual(payload, saved)

    def test_unknown_or_mixed_schema_is_retained_not_guessed(self):
        rows = [{'start_ny': 'known'}, {'candle_open_ny': 'different'}, {}, None, 'unknown']
        payload = {'candles': rows}
        result = compact_voice_tool_result('inspect_market_candles', payload)
        self.assertEqual(result, payload)
        self.assertNotIn('next_start_ny', result)


if __name__ == '__main__':
    unittest.main()
