"""Journal evidence bounds and existing market-engine output, all synthetic."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web.journal_review import snapshot_ranges, review_dimensions, MAX_FIELD_CHARS
from gbop_voice_web.journal_presentation import journal_tool_payload, JOURNAL_PRESENTATION_MAX_CHARS
from gbop_voice_web.journal_context import merge_metadata
from gbop_voice_web.journal_coach import clean_metadata
from gbop_voice_web.double_purge import double_purge_evidence
import test_double_purge as market


class JournalReviewTests(unittest.TestCase):
    def test_actual_double_purge_engine_sequence_is_preserved_exactly(self):
        bars = market.fixture('11:10')
        source = market.review(bars, '11:10')
        source['double_purge'] = double_purge_evidence(source, bars, market.t('11:10'), 60)
        self.assertEqual(source['double_purge']['status'], 'observed')
        before = deepcopy(source)
        saved = snapshot_ranges(source)
        purge = saved['range_history'][0]['double_purge']
        self.assertEqual(purge['sequence'], source['double_purge']['sequence'])
        for key in ('original_completion_preserved', 'range_start_ny', 'range_timeframe', 'confirmed_at_ny'):
            self.assertEqual(purge[key], source['double_purge'][key])
        self.assertEqual(purge['original_outcome']['status'], source['double_purge']['original_outcome']['status'])
        self.assertTrue(saved['range_history_omissions'])  # Uncopied detail is explicitly unknown.
        seq = saved['range_history'][0]['double_purge']['sequence']
        self.assertEqual(seq['opposing_delivery']['bar_open_ny'], market.ny('10:05'))
        self.assertEqual(seq['source_return_inside']['known_at_ny'], market.ny('10:07'))
        self.assertEqual(seq['selected_timeframe_return_inside']['known_at_ny'], market.ny('11:00'))
        self.assertFalse(seq['selected_timeframe_return_inside']['exact_tick_time_known'])
        self.assertEqual(source, before)
        source['double_purge']['status'] = 'changed after retrieval'
        self.assertEqual(saved['range_history'][0]['double_purge']['status'], 'observed')

    def test_field_limit_omits_whole_value_and_labels_missing_evidence(self):
        events = [{'kind': 'synthetic', 'note': 'x' * MAX_FIELD_CHARS}]
        saved = snapshot_ranges({'anchor_start_ny': market.ny('09:00'), 'events': events,
                                 'invalidated_at_ny': market.ny('11:00')})
        self.assertNotIn('events', saved['range_history'][0])
        self.assertEqual(saved['range_history'][0]['invalidated_at_ny'], market.ny('11:00'))
        self.assertIn('ranges.0.events: size limit', saved['range_history_omissions'])
        dims = review_dimensions({'metadata': {'market_review': saved}})
        self.assertTrue(dims['setup_validity']['history_incomplete'])
        self.assertIsNone(dims['actual_outcome']['result_r'])

    def test_range_limit_keeps_identities_separate_and_reports_omission(self):
        rows = [{'label': f'Synthetic range {i}', 'anchor_start_ny': str(i)} for i in range(15)]
        saved = snapshot_ranges({'shift_story': {'ranges': rows}})
        self.assertEqual(len(saved['range_history']), 12)
        self.assertEqual(saved['range_history_source_count'], 15)
        self.assertEqual([r['label'] for r in saved['range_history']], [r['label'] for r in rows[:12]])
        self.assertTrue(saved['range_history_omissions'])

    def test_older_snapshot_never_claims_full_history_or_setup_grade(self):
        row = {'metadata': {'market_review': {'market_facts': {'range_outcomes': [
            {'anchor_start_ny': market.ny('09:00'), 'status': 'opposing_liquidity_delivered'}]}}},
            'rule_adherence': 'I made money so the setup must have been valid', 'result_r': 3}
        dims = review_dimensions(row)
        self.assertEqual(dims['setup_validity']['assessment'], 'not_assessed')
        self.assertTrue(dims['setup_validity']['history_incomplete'])
        self.assertEqual(dims['plan_adherence']['value'], 'unknown')
        self.assertEqual(dims['actual_outcome']['reported_outcome'], 'unknown')
        self.assertEqual(dims['actual_outcome']['result_r'], 3)

    def test_zero_result_and_partial_adherence_are_independent_of_setup_delivery(self):
        dims = review_dimensions({'result_r': 0, 'rule_adherence': 'partial',
            'trade_facts': {'status': 'CLOSED'}, 'metadata': {'reported_outcome': 'breakeven'}})
        self.assertEqual(dims['actual_outcome']['result_r'], 0)
        self.assertEqual(dims['actual_outcome']['record_status'], 'CLOSED')
        self.assertEqual(dims['plan_adherence']['value'], 'partial')
        self.assertEqual(dims['setup_validity']['ranges'], [])

    def test_changed_member_scope_keeps_original_evidence_marked(self):
        dims = review_dimensions({'metadata': {'market_review': snapshot_ranges({'label': 'Original range'}),
            'provenance': {'association_status': 'member_corrected_scope'}}})
        self.assertEqual(dims['setup_validity']['association_status'], 'member_corrected_scope')
        self.assertEqual(dims['setup_validity']['assessment'], 'not_assessed')

    def test_large_evidence_keeps_bounded_read_and_complete_source(self):
        review = snapshot_ranges({'events': [{'note': 'x' * 20000}], 'label': 'Synthetic range'})
        row = {'metadata': {'market_review': review}, 'trade_number': 1}
        row['review_dimensions'] = review_dimensions(row)
        original = deepcopy(row)
        result = journal_tool_payload('get_journal_history', {'ok': True, 'journals': [row], 'next_offset': 1})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=True)), JOURNAL_PRESENTATION_MAX_CHARS)
        self.assertEqual(result['journals'][0]['metadata']['market_review']['range_history_count'], 1)
        self.assertEqual(row, original)

    def test_server_evidence_does_not_consume_member_fact_budget_or_allow_forgery(self):
        review = snapshot_ranges({'events': [{'note': 'x' * 20000}]})
        review['selection'] = {'asset': 'NAS100'}
        result = merge_metadata({}, {'transcription': 'm' * 22000}, review)
        self.assertEqual(result['transcription'], 'm' * 22000)
        self.assertEqual(result['market_review']['range_history'], review['range_history'])
        with self.assertRaises(ValueError):
            clean_metadata({'market_review': review})
        with self.assertRaises(ValueError):
            merge_metadata({}, {'transcription': 'm' * 48000}, review)

    def test_malformed_old_review_does_not_hide_known_result(self):
        for bad in ('unreadable', {'range_history': 'invalid'}, {'market_facts': 'invalid'}):
            dims = review_dimensions({'metadata': {'market_review': bad}, 'result_r': -1})
            self.assertEqual(dims['actual_outcome']['result_r'], -1)
            self.assertTrue(dims['setup_validity']['history_incomplete'])
            self.assertEqual(dims['setup_validity']['assessment'], 'not_assessed')


if __name__ == '__main__':
    unittest.main()
