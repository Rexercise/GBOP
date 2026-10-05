"""Synthetic, read-only cases for concise cutoff-bound variant explanations."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.candle_evidence import crt_review, parse_time
from gbop_voice_web.market_data import MARKET_PROMPT, LIVE_MARKET_PROMPT, session_review
from gbop_voice_web.shift_narrative import attach_directional_outcome, classify_structure, range_objectives
from gbop_voice_web.shift_synopsis import build_other_ranges, build_shift_synopsis
from gbop_voice_web.variant_explanation import variant_clause, variant_explanation
from gbop_voice_web.voice_payload import voice_tool_payload
from test_shift_narrative import candles
from test_shift_review import fixture


def ny(hour):
    return '2026-10-02T' + hour + ':00-04:00'


def base():
    return candles([(100, 110, 90, 100)] + [(106, 109, 104, 106)] * 3)


def review(data, cutoff='12:00', anchor='08:00'):
    end = parse_time(ny(cutoff))
    # Source tools must enforce the same frozen cutoff as the classifier.
    data = [b for b in data if b['time'] + 300 <= end]
    row = crt_review(data, parse_time(ny(anchor)), end, 'H1', 300)
    attach_directional_outcome(row, data, end, 300)
    return row


def detail(row):
    return row['variant_evidence']['explanation']


def candidates(row):
    return [c['code'] for c in detail(row).get('candidates', [])]


class VariantExplanationTests(unittest.TestCase):
    def test_completed_v1_to_v6_use_existing_classifier_and_candle_reasons(self):
        cases = []
        v1 = base(); v1[12]['high'] = 112; v1[25]['low'] = 89
        cases.append(('V1', v1, ('8:00 AM range', '9:00 AM manipulation back inside', '10:00 AM distribution')))
        v2 = base(); v2[12]['high'] = 112; v2[14]['low'] = 89
        cases.append(('V2', v2, ('9:00 AM H1 manipulated then distributed', '8:00 AM range')))
        v3 = base(); v3[12]['high'] = 112; v3[37]['low'] = 89
        cases.append(('V3', v3, ('11:00 AM', 'candle 4')))
        v4 = base(); v4[24]['high'] = 112; v4[26]['low'] = 89
        cases.append(('V4', v4, ('9:00 AM was inside', '10:00 AM manipulation returned inside')))
        v5 = base(); v5[36]['high'] = 112; v5[38]['low'] = 89
        cases.append(('V5', v5, ('9:00 AM/10:00 AM', '2 inside candles')))
        v6 = base(); v6[12]['high'] = 112; v6[24]['high'] = 113; v6[37]['low'] = 89
        cases.append(('V6', v6, ('10:00 AM swept', "9:00 AM's extreme", 'before midpoint delivery')))
        for code, data, snippets in cases:
            with self.subTest(code=code):
                row = review(data)
                observed = row['variant_evidence']
                unmodified = classify_structure({**row, 'direction_observed': row['observed_direction'],
                    'objectives': range_objectives(row), 'invalidated_at_ny': row.get('invalidated_at_ny')}, data, parse_time(ny('12:00')), 300)
                self.assertEqual({k: v for k, v in observed.items() if k != 'explanation'}, unmodified)
                self.assertIn(code, [v['code'] for v in observed['labels']])
                self.assertEqual(detail(row)['status'], 'completed')
                self.assertEqual(candidates(row), [])
                text = variant_clause(observed, include_known=True)
                self.assertIn('because', text)
                for snippet in snippets:
                    self.assertIn(snippet, text)
                self.assertFalse(observed['entry_confirmed'])

    def test_completed_named_range_explanation_is_automatic_in_text_and_voice(self):
        source = session_review(fixture(), '2026-10-02', 'day', 300)
        before = deepcopy(source)
        text = source['shift_synopsis']['spoken_summary']
        for phrase in ('V1 Textbook because 9:00 AM range', '10:00 AM manipulation back inside',
                       '11:00 AM distribution', 'established at 12:00 PM H1 close'):
            self.assertIn(phrase, text)
        self.assertLess(text.index('9ate8'), text.index('Young Lefty'))
        self.assertLess(text.index('Young Lefty'), text.index('The 9:00 AM H1 range'))
        voice = voice_tool_payload('review_market_session', {'ok': True, 'review': source})
        self.assertTrue(voice['ok'])
        self.assertEqual(voice['review']['shift_synopsis']['spoken_summary'], text)
        self.assertEqual(source, before)
        for prompt in (MARKET_PROMPT, LIVE_MARKET_PROMPT):
            for phrase in ('Automatically name supported variants', 'pending candidates', 'what each still needs'):
                self.assertIn(phrase, prompt)

    def test_first_forming_manipulation_keeps_multiple_candidates_conditional(self):
        data = base(); data[12]['high'] = 112
        row = review(data, '09:30')
        self.assertEqual(row['variant_evidence']['labels'], [])
        self.assertEqual(candidates(row), ['V2', 'V1', 'V3'])
        self.assertIn('9:00 AM purged the 8:00 AM range', detail(row)['reason'])
        for candidate in detail(row).get('candidates', []):
            self.assertTrue(candidate['requires'])
        text = variant_clause(row['variant_evidence'])
        self.assertIn('pending because', text)
        self.assertNotIn('established at', text)

    def test_closed_manipulation_leaves_v1_or_v3_not_v2(self):
        data = base(); data[12]['high'] = 112
        row = review(data, '10:00')
        self.assertEqual(candidates(row), ['V1', 'V3'])
        self.assertIn('closed back inside', detail(row)['reason'])
        self.assertIn('candle 3', detail(row).get('candidates', [])[0]['requires'])
        self.assertIn('after candle 3', detail(row).get('candidates', [])[1]['requires'])
        after_third = review(data, '11:00')
        self.assertEqual(candidates(after_third), ['V3'])

    def test_partial_delivery_is_not_a_completed_variant(self):
        data = base(); data[12]['high'] = 112; data[25]['low'] = 99
        row = review(data, '10:30')
        self.assertEqual(row['directional_outcome']['status'], 'midpoint_only')
        self.assertEqual(candidates(row), ['V1', 'V3'])
        self.assertNotEqual(detail(row)['status'], 'completed')

    def test_v2_target_touch_awaits_manipulation_h1_close(self):
        data = base(); data[12]['high'] = 112; data[14]['low'] = 89
        row = review(data, '09:30')
        self.assertEqual(row['directional_outcome']['status'], 'opposing_liquidity_delivered')
        self.assertEqual(row['variant_evidence']['labels'], [])
        self.assertEqual(candidates(row), ['V2'])
        self.assertIn('10:00 AM close', detail(row).get('candidates', [])[0]['requires'])
        self.assertIsNone(detail(row).get('known_at_ny'))
        complete = review(data, '10:00')
        self.assertEqual(detail(complete)['status'], 'completed')
        self.assertEqual(detail(complete)['known_at_ny'], ny('10:00'))

    def test_v1_target_touch_and_h1_classification_are_different_times(self):
        data = base(); data[12]['high'] = 112; data[25]['low'] = 89
        row = review(data, '10:30')
        self.assertEqual(row['directional_outcome']['opposing_liquidity']['evidence']['bar_close_ny'], ny('10:10'))
        self.assertEqual(candidates(row), ['V1'])
        self.assertIn('11:00 AM close', detail(row).get('candidates', [])[0]['requires'])
        complete = review(data, '11:00')
        self.assertEqual(detail(complete)['known_at_ny'], ny('11:00'))
        self.assertEqual(complete['variant_evidence']['labels'][0]['code'], 'V1')

    def test_inside_bar_candidates_and_observed_structure_are_not_full_delivery(self):
        for count, code, cutoff in ((1, 'V4', '10:30'), (2, 'V5', '11:30')):
            with self.subTest(code=code):
                data = base(); data[(count + 1) * 12]['high'] = 112
                row = review(data, cutoff)
                self.assertEqual(candidates(row), [code])
                self.assertIn(f'{count} complete inside H1', detail(row)['reason'])
                complete = review(data)
                self.assertEqual([v['code'] for v in complete['variant_evidence']['labels']], [code])
                self.assertEqual(detail(complete)['status'], 'structure_observed')
                self.assertIn('delivery remains pending', variant_clause(complete['variant_evidence']))

    def test_resoup_candidate_needs_observed_extreme_sweep_and_later_close(self):
        data = base(); data[12]['high'] = 112; data[24]['high'] = 113
        row = review(data, '10:30')
        self.assertIn('V6', candidates(row))
        self.assertIn("10:00 AM swept 9:00 AM's extreme", detail(row)['reason'])
        candidate = next(c for c in detail(row).get('candidates', []) if c['code'] == 'V6')
        self.assertIn('close inside before midpoint', candidate['requires'])
        data[24]['high'] = 111
        self.assertNotIn('V6', candidates(review(data, '10:30')))
        data[24]['high'] = 113; data[25]['low'] = 99
        self.assertNotIn('V6', candidates(review(data, '10:30')))

    def test_no_purge_or_same_source_tie_does_not_invent_candidates(self):
        self.assertEqual(candidates(review(base())), [])
        data = base(); data[12].update(high=112, low=89)
        row = review(data)
        self.assertEqual(candidates(row), [])
        self.assertEqual(row['variant_evidence']['labels'], [])
        self.assertEqual(detail(row)['status'], 'unverified')

    def test_missing_prefix_cannot_establish_a_unique_pending_variant(self):
        data = base(); data[12]['high'] = 112; del data[5]
        row = review(data)
        self.assertEqual(candidates(row), [])
        self.assertEqual(detail(row)['status'], 'unverified')
        data = base(); data[12]['high'] = 112; del data[11]
        self.assertEqual(candidates(review(data)), [])
        data = base(); data[13]['high'] = 112; del data[12]
        row = review(data)
        self.assertEqual(candidates(row), [])
        self.assertEqual(detail(row)['status'], 'unverified')

    def test_gap_after_first_purge_does_not_reopen_impossible_past_hour_paths(self):
        data = base(); data[12]['high'] = 112; del data[18]
        row = review(data)
        self.assertEqual(candidates(row), [])
        self.assertEqual(detail(row)['status'], 'unverified')
        self.assertEqual(variant_clause(row['variant_evidence']), 'variant unverified')

    def test_invalidation_cancels_pending_candidates_without_erasing_completion(self):
        data = base(); data[12]['high'] = 112
        data[23].update(high=113, close=113)
        failed = review(data)
        self.assertEqual(candidates(failed), [])
        self.assertEqual(detail(failed)['status'], 'not_established')
        self.assertNotIn('pending', variant_clause(failed['variant_evidence']))
        data[14]['low'] = 89
        completed = review(data)
        self.assertEqual(detail(completed)['status'], 'completed')
        self.assertEqual(completed['variant_evidence']['labels'][0]['code'], 'V2')
        self.assertTrue(completed['directional_outcome']['delivery_before_later_invalidation'])

    def test_later_invalidation_does_not_reset_anchor_or_erase_explanation(self):
        data = fixture(); data[-1].update(high=111, close=111)
        result = session_review(data, '2026-10-02', 'day', 300)
        followup = build_other_ranges(result, 'NAS100', continue_active=True, anchor_start_ny=ny('09:00'))
        context = followup['active_range_context']
        self.assertEqual(context['anchor_start_ny'], ny('09:00'))
        self.assertEqual(context['conclusion']['status'], 'opposing_liquidity_delivered')
        self.assertIn('V1 Textbook because 9:00 AM range', followup['spoken_summary'])
        self.assertIn('after recorded delivery', followup['spoken_summary'])
        self.assertEqual(context['next_selected_range']['to_anchor_ny'], ny('11:00'))

    def test_no_lookahead_even_with_post_cutoff_source_bars_supplied(self):
        data = base(); data[12]['high'] = 112; data[25]['low'] = 89
        row = review(data, '10:00')
        before = detail(row)
        direct = variant_explanation({**row, 'objectives': range_objectives(row), 'invalidated_at_ny': row.get('invalidated_at_ny')}, data, parse_time(ny('10:00')), 300)
        self.assertEqual(direct, before)
        self.assertEqual([v['code'] for v in direct['candidates']], ['V1', 'V3'])
        self.assertNotIn('10:05', json.dumps(direct))


    def test_structure_identity_keeps_its_close_and_does_not_deny_later_delivery(self):
        for code in ('V4', 'V6'):
            data = base()
            if code == 'V6':
                data[12]['high'] = 112
                data[24]['high'] = 113
            else:
                data[24]['high'] = 112
            data[37]['low'] = 89
            partial = review(data, '11:30')
            self.assertEqual(partial['directional_outcome']['status'], 'opposing_liquidity_delivered')
            self.assertEqual(detail(partial)['status'], 'structure_observed')
            self.assertIn('Opposing liquidity delivered', detail(partial)['remaining'])
            self.assertEqual(detail(partial)['known_at_ny'], ny('11:00'))
            completed = review(data)
            # V6 now coexists with V3, which needs the later distribution H1.
            self.assertEqual(detail(completed)['known_at_ny'], ny('11:00' if code == 'V4' else '12:00'))
            self.assertEqual(detail(completed)['status'], 'completed')

    def test_same_source_exact_boundary_touch_cannot_be_repaired_by_later_touches(self):
        data = base(); data[12].update(high=112, low=90)
        data[25]['low'] = 89
        for cutoff in ('09:30', '10:00', '12:00'):
            row = review(data, cutoff)
            self.assertEqual(row['directional_outcome']['opposing_liquidity']['status'], 'same_bar_order_unknown')
            self.assertEqual(candidates(row), [])
            self.assertEqual(detail(row)['status'], 'unverified')

    def test_observed_v4_can_also_offer_pending_v6_on_actual_partial_resoup(self):
        data = base(); data[24]['high'] = 112; data[36]['high'] = 113
        row = review(data, '11:30')
        self.assertEqual([v['code'] for v in row['variant_evidence']['labels']], ['V4'])
        self.assertEqual(candidates(row), ['V6'])
        self.assertIn('V6 re-soup pending', variant_clause(row['variant_evidence']))

    def test_late_gap_keeps_established_v4_without_a_stale_v6_candidate(self):
        data = base(); data[24]['high'] = 112; data[36]['high'] = 113; del data[42]
        row = review(data)
        self.assertEqual([v['code'] for v in row['variant_evidence']['labels']], ['V4'])
        self.assertEqual(candidates(row), [])
        self.assertEqual(detail(row)['status'], 'structure_observed')

    def test_known_v6_is_not_reoffered_as_pending_on_another_repeat_sweep(self):
        data = base(); data[12]['high'] = 112; data[24]['high'] = 113; data[36]['high'] = 114
        row = review(data, '11:30')
        self.assertEqual([v['code'] for v in row['variant_evidence']['labels']], ['V6'])
        self.assertEqual(candidates(row), [])
        self.assertEqual(detail(row)['known_at_ny'], ny('11:00'))
        self.assertNotIn('V6 re-soup pending', variant_clause(row['variant_evidence']))

    def test_legacy_review_without_explanation_still_uses_its_labels(self):
        source = session_review(fixture(), '2026-10-02', 'day', 300)
        for row in source['shift_story']['ranges']:
            row['variant_evidence'].pop('explanation', None)
        synopsis = build_shift_synopsis(source)
        self.assertIn('V1 Textbook', synopsis['spoken_summary'])
        self.assertNotIn('because 9:00 AM range', synopsis['spoken_summary'])

    def test_live_current_pending_paths_survive_compact_voice_with_frozen_scope(self):
        import test_current_voice_payload as current_fixture
        from test_voice_payload_budget import expanded
        case = current_fixture.CurrentVoicePayloadTests(); case.setUp()
        self.addCleanup(case.doCleanups)
        # Expand synthetic M5 rows to the M1 storage resolution, preserving
        # ordered source intervals. No external feed or member data is used.
        data = base(); data[12]['high'] = 112
        rows = [{**bar, 'time': bar['time'] + n * 60} for bar in data for n in range(5)]
        case.replay.store('NAS100', rows)
        source = case.current('NAS100', '09:30')
        before = deepcopy(source)
        wire = voice_tool_payload('review_current_market', source)
        self.assertTrue(wire['ok'], wire)
        current = expanded(wire, wire)['review']
        fact = next(r for r in current['ranges'] if r['anchor_start_ny'] == ny('08:00'))
        self.assertEqual(fact['role'], 'selected_range')
        self.assertEqual(fact['variant']['labels'], [])
        self.assertEqual([c['code'] for c in fact['variant']['explanation']['candidates']], ['V2', 'V1', 'V3'])
        self.assertIn('9:00 AM purged the 8:00 AM range', fact['variant']['explanation']['reason'])
        self.assertEqual(fact['detail_request']['args']['through_ny'], ny('09:30'))
        self.assertLessEqual(len(json.dumps(wire, separators=(',', ':'))), 16000)
        self.assertEqual(source, before)
        rows[60]['low'] = 90  # exact-boundary, unordered first target touch
        case.replay.store('NAS100', rows)
        uncertain = case.current('NAS100', '09:30')
        fact = next(r for r in uncertain['review']['ranges'] if r['anchor_start_ny'] == ny('08:00'))
        self.assertEqual(fact['variant']['status'], 'unverified')
        self.assertEqual(fact['variant']['explanation']['status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
