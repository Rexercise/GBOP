"""Synthetic regression for opening-range double purge surviving voice transport."""
from copy import deepcopy
import json
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.market_data import session_review
from gbop_voice_web.voice_payload import voice_tool_payload, SHIFT_SYNOPSIS_TARGET_CHARS
from test_market_conversation import function


def ny(clock):
    return '2026-10-05T' + clock + ':00-04:00'


def fixture():
    bars = [dict(time=t, open=100, high=150, low=50, close=100)
            for t in range(parse_time(ny('19:00')), parse_time(ny('20:00')), 60)]
    bars += [dict(time=t, open=100, high=120, low=80, close=100)
             for t in range(parse_time(ny('20:00')), parse_time(ny('21:00')), 60)]
    end = parse_time('2026-10-06T00:00:00-04:00')
    bars += [dict(time=t, open=90, high=95, low=85, close=90)
             for t in range(parse_time(ny('21:00')), end, 60)]
    def change(clock, **values):
        next(b for b in bars if b['time'] == parse_time(ny(clock))).update(values)
    change('21:01', open=110, high=125, low=105, close=122)
    change('21:40', open=90, high=92, low=75, close=78)
    change('21:41', open=78, high=92, low=76, close=90)
    change('23:23', high=100)
    return bars


def result(bars=None):
    return {'ok': True, 'asset': 'NAS100', 'symbol': 'SYNTHETIC',
            'review': session_review(fixture() if bars is None else bars, '2026-10-05', 'night', 60)}


class ShiftDoublePurgeVoiceTests(unittest.TestCase):
    def assert_double(self, fact):
        self.assertEqual((fact['direction'], fact['outcome']), ('bearish', 'opposing_liquidity_delivered'))
        double = fact['double_purge']
        self.assertTrue(double['observed'])
        self.assertTrue(double['original_completion_preserved'])
        self.assertEqual(double['original_first_purged_side'], 'buy')
        sequence = double['sequence']
        self.assertEqual(sequence['first_purge']['bar_open_ny'], ny('21:01'))
        self.assertEqual(sequence['opposing_purge']['bar_open_ny'], ny('21:40'))
        self.assertEqual(sequence['source_return_inside']['known_at_ny'], ny('21:42'))
        self.assertEqual(sequence['source_return_inside']['timeframe'], 'M1')
        self.assertEqual(sequence['assigned_return_inside']['known_at_ny'], ny('21:45'))
        self.assertEqual(sequence['assigned_return_inside']['timeframe'], 'M5')
        reverse = double['reversal_thesis']
        self.assertEqual((reverse['direction'], reverse['status']), ('bullish', 'midpoint_only'))
        self.assertEqual((reverse['objective_side'], reverse['objective_level']), ('buy', 120))
        self.assertEqual(reverse['midpoint_role'], 'halfway_progress_not_full_objective')
        self.assertEqual(reverse['objectives']['midpoint']['source_interval']['known_at_ny'], ny('23:24'))
        self.assertEqual(reverse['objectives']['original_side']['distance_price_points'], 20)

    def test_default_opening_synopsis_preserves_both_outcomes_and_known_at_times(self):
        raw = result()
        before = deepcopy(raw)
        wire = voice_tool_payload('review_market_session', raw)
        synopsis = wire['review']['shift_synopsis']
        self.assert_double(synopsis['ranges'][0])
        self.assertIn('same-range double-purge bullish reversal delivered 50% only', synopsis['spoken_summary'])
        self.assertIn('sell-side delivered', synopsis['spoken_summary'])
        self.assertEqual(synopsis['shift_end']['active_anchor_ny'], ny('20:00'))
        self.assertLess(len(json.dumps(wire, separators=(',', ':'))), SHIFT_SYNOPSIS_TARGET_CHARS)
        self.assertEqual(raw, before)

    def test_return_inside_alone_does_not_create_double_purge(self):
        bars = fixture()
        for b in bars:
            if b['time'] >= parse_time(ny('21:40')):
                b.update(open=90, high=100, low=85, close=90)
        wire = voice_tool_payload('review_market_session', result(bars))
        synopsis = wire['review']['shift_synopsis']
        self.assertNotIn('double_purge', synopsis['ranges'][0])
        self.assertNotIn('double-purge', synopsis['spoken_summary'])

    def test_missing_sequence_evidence_cannot_be_promoted_by_compaction(self):
        bars = [b for b in fixture() if b['time'] != parse_time(ny('21:20'))]
        raw = result(bars)
        self.assertFalse(raw['review']['shift_story']['ranges'][0]['double_purge']['observed'])
        synopsis = voice_tool_payload('review_market_session', raw)['review']['shift_synopsis']
        self.assertNotIn('double_purge', synopsis['ranges'][0])
        self.assertNotIn('double-purge', synopsis['spoken_summary'])

    def test_backend_function_output_contains_evidence_before_second_model_call(self):
        raw = result()
        args = {'asset': 'NAS100', 'date_ny': '2026-10-05', 'shift': 'night'}
        call = NS(type='function_call', name='review_market_session', arguments=json.dumps(args), call_id='synthetic-call')
        create = Mock(side_effect=[NS(output=[call], output_text=''), NS(output=[], output_text='checked')])
        def dispatch(user, name, values, token):
            if name == 'list_market_shifts':
                return {'ok': True, 'available_shifts': [{**args, 'temporal_status': 'completed',
                    'review_scope': 'full'}]}
            return deepcopy(raw)
        backend = function('run_backend', dict(PENDING_JOURNAL_DELETIONS={}, time=time,
            member_context=lambda _: '', GTOP_GUILD_ID=1, client=NS(responses=NS(create=create)),
            BACKEND_MODEL='offline-test', BACKEND_PROMPT='', TOOLS=[], json=json,
            run_tool=dispatch))
        context = MarketConversation((1, 2, 'synthetic-authenticated-session'))
        text = backend([{'role': 'user', 'text': 'Review the NAS night shift.'}], 2, context, 1)
        self.assertEqual(text, 'checked')
        self.assertEqual(create.call_count, 2)
        outputs = [x for x in create.call_args.kwargs['input']
                   if isinstance(x, dict) and x.get('type') == 'function_call_output']
        self.assertEqual(len(outputs), 1)
        wire = json.loads(outputs[0]['output'])
        self.assert_double(wire['review']['shift_synopsis']['ranges'][0])


if __name__ == '__main__':
    unittest.main()
