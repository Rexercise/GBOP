"""Synthetic all-asset scans keep every fact under the existing voice ceiling."""
from copy import deepcopy
import json
import sqlite3
import unittest

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_data import ASSETS, CREATE_SQL, HISTORY_SQL, market_tool
from gbop_voice_web.voice_payload import voice_tool_payload


DAY = '2026-06-11'


def size(value):
    return len(json.dumps(value, separators=(',', ':')))


def expand(wire):
    """Independent decoder: references resolve against final wire paths first."""
    symbols = 'scan_keys' in wire
    def key_name(key):
        return wire['scan_keys'][int(key)] if symbols and key.isdigit() else key
    def string(index):
        value = wire['scan_strings'][index]
        if symbols and isinstance(value, list):
            template, clock = value
            prefix, suffix = wire['scan_clock_templates'][template]
            return prefix + clock + suffix
        return value
    def resolve(value, seen=()):
        if isinstance(value, dict) and set(value) == {'same_evidence_as'}:
            path = value['same_evidence_as']
            if not path.startswith('#/') or path in seen:
                raise AssertionError('Invalid or cyclic fact reference: ' + path)
            target = wire
            for part in path[2:].split('/'):
                part = part.replace('~1', '/').replace('~0', '~')
                target = target[int(part)] if isinstance(target, list) else target[part]
            return resolve(target, (*seen, path))
        if isinstance(value, dict):
            return {key: resolve(child, seen) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child, seen) for child in value]
        return value

    def decode(value):
        if isinstance(value, dict):
            if set(value) == {'row'}:
                index, *cells = value['row']
                columns = [key_name(key) for key in wire['scan_columns'][index]]
                if len(columns) != len(cells):
                    raise AssertionError('Missing or extra fact-table cells')
                return {**decode(wire['scan_defaults'][index]),
                        **{key: decode(cell) for key, cell in zip(columns, cells)}}
            if set(value) == {'str'}:
                return string(value['str'])
            if set(value) == {'text'}:
                index, *clocks = value['text']
                fragments = wire['scan_texts'][index]
                if len(fragments) != len(clocks) + 1:
                    raise AssertionError('Missing or extra text fragments')
                return fragments[0] + ''.join(clock + fragment for clock, fragment in zip(clocks, fragments[1:]))
            return {key_name(key): decode(child) for key, child in value.items()}
        if isinstance(value, list):
            return [decode(child) for child in value]
        if symbols and isinstance(value, str) and value.startswith('$'):
            return value[1:] if value.startswith('$$') else string(int(value[1:]))
        return value

    out = resolve(wire)
    out['results'] = decode(out['results'])
    for key in ('scan_columns', 'scan_defaults', 'scan_strings', 'scan_texts',
                'scan_keys', 'scan_clock_templates', 'voice_view'):
        out.pop(key, None)
    return out


def source(index, shift, *, gap=False, mirrored=False):
    start = parse_time(f'{DAY}T{7 if shift == "day" else 19:02}:00:00-04:00')
    rows = [dict(time=start+i*60, open=100, high=110 if i < 60 else 108,
                 low=90 if i < 60 else 92, close=100) for i in range(300)]
    for minute, values in (
            (65+index, dict(open=108, high=112, low=107, close=109)),
            (85+index, dict(open=95, high=97, low=88, close=94)),
            (125+index, dict(high=111)), (185+index, dict(low=88)),
            (245+index, dict(high=112))):
        rows[minute].update(values)
    if gap:
        # The initial and reverse deliveries are already proven; the later
        # own-timeframe return must retain its gap qualification.
        del rows[150]
    if mirrored:
        rows = [{**bar, 'open': 200-bar['open'], 'high': 200-bar['low'],
                 'low': 200-bar['high'], 'close': 200-bar['close']} for bar in rows]
    # All nine assets carry distinct prices as well as distinct purge clocks.
    for bar in rows:
        for key in ('open', 'high', 'low', 'close'):
            bar[key] = round(bar[key] * (1+index/10) + index*1000, 3)
    return start, rows


class LeftyScanBudgetTests(unittest.TestCase):
    def scan(self, shift='day', *, mixed=False):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute(CREATE_SQL)
        conn.execute(HISTORY_SQL)
        for index, asset in enumerate(sorted(ASSETS)):
            start, rows = source(index, shift, gap=mixed and index % 3 == 0,
                                 mirrored=mixed and index % 2 == 1)
            now = start + 18000
            payload = dict(asset=asset, symbol=asset+'m', bid=101, ask=102,
                           tick_time=now, bars=[], bars_m1=rows)
            conn.execute('INSERT INTO gbop_market_feed VALUES (?,?,?,?)',
                         (asset, now, now, json.dumps(payload)))
        try:
            return market_tool(lambda: conn, 'scan_young_lefty',
                               {'date_ny': DAY, 'shift': shift}, now=now)
        finally:
            conn.close()

    def assert_lossless(self, raw):
        before = deepcopy(raw)
        wire = voice_tool_payload('scan_young_lefty', raw)
        self.assertTrue(wire.get('ok'), wire)
        self.assertLessEqual(size(wire), 28000)
        self.assertEqual(wire['voice_view']['character_budget'], 28000)
        self.assertEqual(expand(wire), raw)
        self.assertEqual(raw, before)
        self.assertEqual(wire, voice_tool_payload('scan_young_lefty', raw))
        self.assertEqual({row['asset'] for row in wire['results']}, ASSETS)
        return wire

    def test_nine_distinct_populated_assets_round_trip_day_and_night(self):
        for shift in ('day', 'night'):
            with self.subTest(shift=shift):
                raw = self.scan(shift)
                self.assertGreater(size(raw), 28000)
                self.assertTrue(all(row['status'] == 'delivered' for row in raw['results']))
                wire = self.assert_lossless(raw)
                self.assertTrue(wire['scan_texts'])
                for row in raw['results']:
                    context = row['evidence']['young_lefty_context']
                    self.assertIsNone(context['selected_direction'])
                    recap = context['delivery_recap']
                    self.assertEqual(recap['double_purge']['reversal_thesis']['status'], 'original_side_delivered')
                    self.assertEqual(len(recap['continuation']['legs']), 3)

    def test_mirrored_delivery_and_unknown_later_gaps_are_not_merged(self):
        raw = self.scan(mixed=True)
        self.assert_lossless(raw)
        for index, row in enumerate(raw['results']):
            recap = row['evidence']['young_lefty_context']['delivery_recap']
            self.assertEqual(row['evidence']['direction'], 'bullish' if index % 2 else 'bearish')
            if index % 3 == 0:
                self.assertFalse(row['evidence']['coverage_complete'])
                self.assertEqual(recap['continuation']['legs'], [])
                self.assertIn('unverified', recap['continuation']['next_boundary']['confirmation_status'])

    def test_small_scan_is_unchanged_and_unbounded_unknown_data_fails_closed(self):
        small = {'ok': True, 'results': [{'asset': 'XAUUSD', 'status': 'unavailable'}]}
        self.assertEqual(voice_tool_payload('scan_young_lefty', small), small)
        huge = deepcopy(small)
        huge['unrecognized_metadata'] = 'z' * 28001
        wire = voice_tool_payload('scan_young_lefty', huge)
        self.assertFalse(wire['ok'])
        self.assertEqual(wire['status'], 'scan_payload_budget_exceeded')
        self.assertLess(size(wire), 28000)

    def test_codec_marker_collision_is_not_reinterpreted(self):
        raw = self.scan()
        raw['results'][0]['unrecognized_metadata'] = {'row': [0, 'user data']}
        wire = voice_tool_payload('scan_young_lefty', raw)
        self.assertFalse(wire['ok'])
        self.assertEqual(wire['status'], 'scan_payload_budget_exceeded')
        self.assertEqual(raw['results'][0]['unrecognized_metadata'], {'row': [0, 'user data']})

    def test_existing_codec_headers_survive_small_scan_and_fail_closed_when_large(self):
        large = self.scan()
        for suffix in ('columns', 'defaults', 'strings', 'texts', 'keys', 'clock_templates'):
            with self.subTest(suffix=suffix):
                name = 'scan_' + suffix
                unknown = {'unknown': 'must survive'}
                small = {'ok': True, 'results': [], name: unknown}
                self.assertEqual(voice_tool_payload('scan_young_lefty', small), small)
                raw = deepcopy(large)
                raw[name] = deepcopy(unknown)
                before = deepcopy(raw)
                wire = voice_tool_payload('scan_young_lefty', raw)
                self.assertFalse(wire['ok'])
                self.assertEqual(wire['status'], 'scan_payload_budget_exceeded')
                self.assertLess(size(wire), 28000)
                self.assertEqual(raw, before)


if __name__ == '__main__':
    unittest.main()
