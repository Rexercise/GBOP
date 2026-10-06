"""Synthetic-only voice transport regressions; no retained broker/account data."""
from copy import deepcopy
import json
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.shift_synopsis import build_shift_synopsis
from gbop_voice_web import voice_payload as transport
from test_market_conversation import function
from test_shift_double_purge_voice import fixture, ny, result


def size(value):
    return len(json.dumps(value, separators=(',', ':')))


def session(*, incomplete=False, transitions=False):
    bars = fixture()
    if transitions:
        for clock, values in (
                ('21:59', dict(open=85, high=90, low=70, close=75)),
                ('22:59', dict(open=80, high=90, low=65, close=65)),
                ('23:59', dict(open=70, high=90, low=60, close=60))):
            next(b for b in bars if b['time'] == parse_time(ny(clock))).update(values)
    if incomplete:
        bars.pop(0)
    return result(bars)


def captured(raw):
    context = MarketConversation()
    context.begin_turn()
    source = context.run('review_market_session', dict(asset='NAS100',
        date_ny='2026-10-05', shift='night'), lambda name, args: deepcopy(raw))
    return context, source


def expanded_index(synopsis):
    index = deepcopy(synopsis['range_index'])
    common = synopsis.get('range_detail_request')
    if common:
        for row in index:
            row['detail_request'] = deepcopy(common)
            row['detail_request']['args']['anchor_start_ny'] = row['anchor_start_ny']
    return index


def oversized(raw):
    """Force a realistic envelope budget boundary with explicitly synthetic data.

    Unknown metadata must be retained, not silently stripped to make this pass.
    The underlying synthetic evidence/chronology is never padded or modified.
    """
    _, source = captured(raw)
    source['synthetic_transport_metadata'] = ''
    with patch.object(transport, 'SHIFT_SYNOPSIS_TARGET_CHARS', 99999):
        full = transport.voice_tool_payload('review_market_session', source)
    source['synthetic_transport_metadata'] = 'x' * (12500 - size(full))
    return source


class ShiftSynopsisCompactionTests(unittest.TestCase):
    def assert_complete_view(self, source, wire):
        self.assertTrue(wire['ok'])
        self.assertLessEqual(size(wire), 12000)
        self.assertEqual(wire['voice_view']['character_budget'], 12000)
        expected = build_shift_synopsis(source['review'], source['asset'])
        actual = wire['review']['shift_synopsis']
        self.assertEqual(expanded_index(actual), expected['range_index'])
        # Exact structural equality covers all selected/relevant ranges, ordered
        # double purges, original/reversal objectives, variants, known-at times,
        # coverage/negative guards, later transitions and the spoken synopsis.
        for key, value in expected.items():
            if key not in ('range_index', 'response_contract'):
                self.assertEqual(actual[key], value, key)
        for key, value in source.items():
            if key not in ('review', 'market_context'):
                self.assertEqual(wire[key], value, key)
        for key in ('selection', 'scope_id', 'evidence_id', 'source_tool', 'limits'):
            self.assertEqual(wire['market_context'][key], source['market_context'][key])

    def test_overflow_compaction_is_deterministic_fact_complete_and_non_mutating(self):
        source = oversized(session())
        before = deepcopy(source)
        wire = transport.voice_tool_payload('review_market_session', source)
        self.assert_complete_view(source, wire)
        self.assertIn('range_detail_request', wire['review']['shift_synopsis'])
        self.assertEqual(wire, transport.voice_tool_payload('review_market_session', source))
        self.assertEqual(source, before)
        self.assertIn('same-range double-purge bullish reversal delivered 50% only',
                      wire['review']['shift_synopsis']['spoken_summary'])

    def test_missing_seven_stays_unknown_and_scoped_negative_guard_survives(self):
        source = oversized(session(incomplete=True))
        wire = transport.voice_tool_payload('review_market_session', source)
        self.assert_complete_view(source, wire)
        synopsis = wire['review']['shift_synopsis']
        self.assertEqual(synopsis['young_lefty_status'], 'unverified')
        self.assertFalse(synopsis['young_lefty_coverage']['complete'])
        self.assertIn('Young Lefty', synopsis['negative_claim_guard']['unverified_contexts'])
        self.assertIn('Young Lefty: unverified', synopsis['spoken_summary'])
        self.assertNotIn('Young Lefty: absent', synopsis['spoken_summary'])

    def test_completed_opening_preserves_every_later_selected_range_and_cutoff(self):
        source = oversized(session(transitions=True))
        wire = transport.voice_tool_payload('review_market_session', source)
        self.assert_complete_view(source, wire)
        synopsis = wire['review']['shift_synopsis']
        self.assertEqual([r['anchor_start_ny'] for r in synopsis['ranges']],
                         [ny(clock) for clock in ('20:00', '21:00', '22:00', '23:00')])
        self.assertEqual(synopsis['ranges'][0]['verdict'], 'delivered')
        self.assertEqual(synopsis['ranges'][-1]['observation_status'],
                         'no_post_close_evidence_at_cutoff')
        self.assertEqual(synopsis['through_ny'], '2026-10-06T00:00:00-04:00')

    def test_under_budget_payload_keeps_original_navigation_and_contract(self):
        context, source = captured(session())
        retained = deepcopy(context.evidence)
        wire = transport.voice_tool_payload('review_market_session', source)
        self.assertEqual(wire['review']['shift_synopsis'],
                         build_shift_synopsis(source['review'], source['asset']))
        self.assertNotIn('range_detail_request', wire['review']['shift_synopsis'])
        self.assertEqual(context.evidence, retained)

    def test_request_scope_mismatch_is_not_factored_or_guessed(self):
        _, source = captured(session())
        base = transport.voice_tool_payload('review_market_session', source)
        for mutation in ('asset', 'through_ny', 'anchor_timeframe', 'anchor_start_ny', 'future_arg'):
            with self.subTest(mutation=mutation):
                wire = deepcopy(base)
                wire['review']['shift_synopsis']['range_index'][-1]['detail_request']['args'][mutation] = 'different'
                before = deepcopy(wire)
                transport._compact_synopsis_navigation(wire)
                self.assertEqual(wire, before)

    def test_nonidentical_json_argument_types_and_missing_anchors_are_not_factored(self):
        _, source = captured(session())
        base = transport.voice_tool_payload('review_market_session', source)
        for missing in (False, True):
            wire = deepcopy(base)
            rows = wire['review']['shift_synopsis']['range_index']
            for row in rows:
                if missing:
                    row.pop('anchor_start_ny')
                    row['detail_request']['args'].pop('anchor_start_ny')
                else:
                    row['detail_request']['args']['future_arg'] = True
            if not missing:
                rows[-1]['detail_request']['args']['future_arg'] = 1
            before = deepcopy(wire)
            transport._compact_synopsis_navigation(wire)
            self.assertEqual(wire, before)

    def test_unverified_budget_failure_retains_negative_claim_guard(self):
        _, source = captured(session(incomplete=True))
        source['unknown_metadata'] = 'x' * 50000
        wire = transport.voice_tool_payload('review_market_session', source)
        self.assertFalse(wire['ok'])
        self.assertEqual(wire['negative_claim_guard'],
                         build_shift_synopsis(source['review'], source['asset'])['negative_claim_guard'])
        self.assertLessEqual(size(wire), 12000)

    def test_oversized_unknown_metadata_fails_with_original_exact_navigation(self):
        _, source = captured(session())
        source['unknown_metadata'] = 'x' * 50000
        wire = transport.voice_tool_payload('review_market_session', source)
        self.assertFalse(wire['ok'])
        self.assertEqual(wire['status'], 'voice_synopsis_budget_exceeded')
        self.assertLessEqual(size(wire), 12000)
        self.assertNotIn('review', wire)
        self.assertNotIn('evidence_ref', wire['market_context'])
        self.assertEqual(wire['range_index'],
                         build_shift_synopsis(source['review'], source['asset'])['range_index'])
        self.assertEqual(wire['detail_request']['args']['anchor_start_ny'], ny('20:00'))

    def test_tiny_caps_return_only_honest_bounded_failure_or_explicitly_reject(self):
        _, source = captured(session())
        for budget in (12, 64, 256, 800):
            with self.subTest(budget=budget), patch.object(transport, 'SHIFT_SYNOPSIS_TARGET_CHARS', budget):
                wire = transport.voice_tool_payload('review_market_session', source)
                self.assertFalse(wire['ok'])
                self.assertNotIn('review', wire)
                self.assertLessEqual(size(wire), budget)
        with patch.object(transport, 'SHIFT_SYNOPSIS_TARGET_CHARS', 11):
            with self.assertRaisesRegex(ValueError, 'explicit failure'):
                transport.voice_tool_payload('review_market_session', source)

    def test_retained_selection_uses_same_compaction(self):
        source = oversized(session())
        source['source_tool'] = 'review_market_session'
        wire = transport.voice_tool_payload('select_market_context', source)
        self.assert_complete_view(source, wire)

    def test_backend_second_model_call_receives_all_facts_and_context_retains_raw(self):
        source = oversized(session(transitions=True))
        args = {'asset': 'NAS100', 'date_ny': '2026-10-05', 'shift': 'night'}
        call = NS(type='function_call', name='review_market_session',
                  arguments=json.dumps(args), call_id='synthetic-compaction')
        create = Mock(side_effect=[NS(output=[call], output_text=''),
                                   NS(output=[], output_text='checked')])
        def dispatch(user, name, values, token):
            if name == 'list_market_shifts':
                return {'ok': True, 'available_shifts': [{**args, 'temporal_status': 'completed',
                    'review_scope': 'full'}]}
            return deepcopy(source)
        backend = function('run_backend', dict(PENDING_JOURNAL_DELETIONS={}, time=time,
            member_context=lambda _: '', GTOP_GUILD_ID=1, client=NS(responses=NS(create=create)),
            BACKEND_MODEL='offline-test', BACKEND_PROMPT='', TOOLS=[], json=json,
            run_tool=dispatch))
        context = MarketConversation((1, 2, 'synthetic-authenticated-session'))
        self.assertEqual(backend([{'role': 'user', 'text': 'Review the NAS night shift.'}],
                         2, context, 1), 'checked')
        self.assertEqual(create.call_count, 2)
        outputs = [x for x in create.call_args.kwargs['input']
                   if isinstance(x, dict) and x.get('type') == 'function_call_output']
        self.assertEqual(len(outputs), 1)
        wire = json.loads(outputs[0]['output'])
        self.assertTrue(wire['ok'])
        self.assertLessEqual(size(wire), 12000)
        self.assertEqual(wire['review']['shift_synopsis']['ranges'],
                         build_shift_synopsis(source['review'], 'NAS100')['ranges'])
        self.assertEqual(next(iter(context.context_bank.entries.values()))['result']['review'],
                         source['review'])
        self.assertTrue(context._detail_index)


if __name__ == '__main__':
    unittest.main()
