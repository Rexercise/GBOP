"""Bounded private presentation over offline saved-provenance fixtures only."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from gbop_voice_web.journal_context import merge_metadata, review_snapshot
from gbop_voice_web.journal_presentation import (
    JOURNAL_PRESENTATION_MAX_CHARS, journal_tool_payload, metadata_summary,
)
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.market_data import attach_lifecycle
from gbop_voice_web.candle_evidence import crt_review, parse_time


def ny(clock):
    return f'2026-10-02T{clock}:00-04:00'


def size(value):
    return len(json.dumps(value, separators=(',', ':')))


class JournalPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((Path(__file__).parent / 'fixtures/market_replays/friday_2026_10_02_m1.json').read_text())
        source = next(item for item in fixture['instruments'] if item['asset'] == 'NAS100')
        bars = [dict(zip(('time', 'open', 'high', 'low', 'close'), row)) for row in source['candles']]
        end = parse_time(ny('12:00'))
        review = attach_lifecycle(crt_review(bars, parse_time(ny('09:00')), end, 'H1', 60), bars, end, 60)
        result = {'ok': True, 'asset': 'NAS100', 'review': review}
        selection = {'asset': 'NAS100', 'date_ny': '2026-10-02', 'shift': 'day',
                     'anchor_start_ny': ny('09:00'), 'anchor_timeframe': 'H1', 'through_ny': ny('12:00')}
        evidence = MarketConversation._evidence('review_market_crt', result, selection)
        cls.review = review_snapshot(result, selection, evidence,
                                     {'detail_candle_start_ny': ny('10:00')}, 'offline-test', 1)

    def metadata(self):
        return merge_metadata({}, {
            'transcription': 'Full member-written journal. ' * 800,
            'reported_entry_at': ny('10:07'), 'reported_exit_at': ny('10:19'),
            'reported_outcome': 'stopped_out', 'entry_price': '31010 NAS100 points',
            'kind': 'trade'}, deepcopy(self.review))

    def record(self, number=1):
        return dict(journal_number=number, journal_id=100+number,
            summary='Member-reported narrative ' * 150, study_note='Preserve my full note. ' * 100,
            rule_adherence='partial', result_r=None, created_at='2026-10-03T19:00:00+00:00',
            metadata=self.metadata())

    def test_snapshot_is_immutable_and_critical_market_axes_survive(self):
        raw = self.metadata()
        before = deepcopy(raw)
        result = metadata_summary(raw)
        self.assertEqual(raw, before)
        market = result['market_review']
        self.assertEqual(market['scope_id'], self.review['scope_id'])
        self.assertEqual(market['evidence_id'], self.review['evidence_id'])
        self.assertNotIn('conversation_id', market)
        self.assertNotIn('review_generation', market)
        candle = market['selected_candle']
        self.assertEqual(candle['bar_open_ny'], ny('10:00'))
        self.assertEqual(candle['low'], 30930.59)
        self.assertEqual(candle['csd']['reference_boundary'], 'low')
        self.assertEqual(candle['csd']['evidence']['bar_open_ny'], ny('11:00'))
        self.assertEqual(candle['csd']['evidence']['confirmed_at_ny'], ny('11:05'))
        self.assertEqual(candle['model1_crt_invalidating_close']['bar_close_ny'], ny('10:20'))
        soup = candle['super_soup_structure']
        self.assertEqual(soup['local_crt_outcome'], 'midpoint_delivered_then_invalidated')
        self.assertEqual(soup['local_function_outcome'], 'opposing_liquidity_delivered')
        own = soup['local_function_objectives']['opposing_liquidity']
        self.assertEqual(own['source_interval']['bar_open_ny'], ny('10:59'))
        self.assertEqual(own['relative_to_model1_invalidation'], 'after_model1_invalidation')

    def test_current_snapshot_mode_and_cutoff_survive_recall_presentation(self):
        raw = self.metadata()
        raw['market_review']['source_tool'] = 'review_market_crt'
        raw['market_review']['selection'].update(
            review_mode='current_market', as_of_ny=ny('11:25'),
            through_ny=ny('11:24'), assigned_timeframe='M5',
            evidence_status='available')
        shown = metadata_summary(raw)['market_review']['selection']
        self.assertEqual(shown['review_mode'], 'current_market')
        self.assertEqual(shown['as_of_ny'], ny('11:25'))
        self.assertEqual(shown['through_ny'], ny('11:24'))
        self.assertEqual(shown['assigned_timeframe'], 'M5')

    def test_reported_times_results_and_logging_time_are_not_conflated(self):
        source = self.record()
        result = journal_tool_payload('save_journal_entry', {**source, 'ok': True, 'saved': True})
        self.assertEqual(result['created_at'], source['created_at'])
        self.assertEqual(result['metadata']['reported_entry_at'], ny('10:07'))
        self.assertEqual(result['metadata']['reported_exit_at'], ny('10:19'))
        self.assertEqual(result['metadata']['reported_outcome'], 'stopped_out')
        self.assertIsNone(result['result_r'])
        self.assertNotIn('pnl', result['metadata'])
        self.assertTrue(result['saved'])

    def test_large_save_is_bounded_without_touching_source_or_warning(self):
        source = {**self.record(), 'ok': True, 'saved': True,
                  'warnings': ['Tier 2 allocation exceeded; member-reported execution still saved.']}
        before = deepcopy(source)
        result = journal_tool_payload('save_journal_entry', source)
        self.assertGreater(size(source), JOURNAL_PRESENTATION_MAX_CHARS)
        self.assertLessEqual(size(result), JOURNAL_PRESENTATION_MAX_CHARS)
        self.assertEqual(source, before)
        self.assertEqual(result['warnings'], source['warnings'])
        self.assertEqual(set(result['text_previews']), {'summary', 'study_note'})
        self.assertIn('transcription', result['metadata']['presentation']['metadata_omitted_fields'])
        self.assertIn('send_journal_history', result['text_preview_note'])
        self.assertTrue(result['metadata']['market_review']['detail_omitted'])

    def test_history_page_preserves_counts_and_first_unsupplied_offset(self):
        rows = [self.record(i) for i in range(20)]
        source = {'ok': True, 'journals': rows, 'journal_count': 200, 'trade_count': 80,
                  'has_more': True, 'next_offset': 55}
        before = deepcopy(source)
        result = journal_tool_payload('get_journal_history', source)
        self.assertLessEqual(size(result), JOURNAL_PRESENTATION_MAX_CHARS)
        self.assertEqual(source, before)
        returned = len(result['journals'])
        self.assertGreater(returned, 0)
        self.assertLess(returned, 20)
        self.assertEqual(result['next_offset'], 35+returned)
        self.assertEqual(result['journal_count'], 200)
        self.assertEqual([r['journal_number'] for r in result['journals']], list(range(returned)))
        self.assertTrue(result['has_more'])

    def test_setup_page_never_skips_omitted_records(self):
        source = {'ok': True, 'entries': [self.record(i) for i in range(10)],
                  'has_more': False, 'next_offset': 30}
        result = journal_tool_payload('find_journal_setups', source)
        self.assertLessEqual(size(result), JOURNAL_PRESENTATION_MAX_CHARS)
        returned = len(result['entries'])
        self.assertEqual(result['next_offset'], 20+returned)
        self.assertTrue(result['has_more'])
        self.assertEqual(result['journal_view']['records_omitted_from_this_page'], 10-returned)

    def test_unbounded_extra_data_never_turns_saved_success_into_failure(self):
        result = journal_tool_payload('save_journal_entry', {
            'ok': True, 'saved': True, 'journal_number': 3, 'result_r': None,
            'warnings': ['Recorded risk exceeds the member\'s Tier 2 allocation.'],
            'risk_warning': 'The member has already exceeded their thesis budget.',
            'rule_adherence': 'partial', 'advisory_status': 'risk_exceeded',
            'unknown_provider_field': 'x'*100000})
        self.assertTrue(result['ok'])
        self.assertTrue(result['saved'])
        self.assertEqual(result['journal_number'], 3)
        self.assertEqual(result['journal_view']['kind'], 'journal_details_omitted')
        self.assertEqual(result['warnings'], ['Recorded risk exceeds the member\'s Tier 2 allocation.'])
        self.assertEqual(result['risk_warning'], 'The member has already exceeded their thesis budget.')
        self.assertEqual(result['rule_adherence'], 'partial')
        self.assertEqual(result['advisory_status'], 'risk_exceeded')
        self.assertLessEqual(size(result), JOURNAL_PRESENTATION_MAX_CHARS)

    def test_double_compaction_preserves_correction_provenance_and_page(self):
        record = self.record()
        record['metadata'] = merge_metadata(record['metadata'], {'reported_exit_at': ny('10:18')})
        source = {'ok': True, 'journals': [record for _ in range(20)],
                  'next_offset': 30, 'has_more': True, 'journal_count': 50}
        first = journal_tool_payload('get_journal_history', source)
        self.assertEqual(first, journal_tool_payload('get_journal_history', first))
        meta = first['journals'][0]['metadata']
        self.assertEqual(meta['provenance']['correction_count'], 1)
        self.assertEqual(meta, metadata_summary(meta))
        # Also support another read path's existing summary without our marker.
        prior = deepcopy(meta)
        prior.pop('presentation')
        again = metadata_summary(prior)
        self.assertEqual(again['provenance']['correction_count'], 1)
        self.assertEqual(again['provenance']['latest_correction'], meta['provenance']['latest_correction'])

    def test_unbounded_warning_is_explicitly_omitted_not_silently_lost(self):
        result = journal_tool_payload('save_journal_entry', {
            'ok': True, 'saved': True, 'warnings': ['A risk warning.', 'x'*100000]})
        self.assertTrue(result['saved'])
        self.assertEqual(result['warnings'], ['A risk warning.'])
        self.assertTrue(result['warnings_details_omitted'])
        self.assertEqual(result['warnings_count'], 2)
        self.assertIn('does not mean no warning', result['advisory_note'])
        self.assertLessEqual(size(result), JOURNAL_PRESENTATION_MAX_CHARS)

    def test_oversized_structured_value_is_omitted_not_changed_into_a_fact(self):
        result = metadata_summary({'reported_entry_at': 'invalid'*200, 'asset': 'NAS100'})
        self.assertNotIn('reported_entry_at', result)
        self.assertIn('reported_entry_at', result['presentation']['metadata_omitted_fields'])
        self.assertEqual(result['asset'], 'NAS100')

    def test_small_failure_and_unrelated_tools_keep_meaning(self):
        error = {'ok': False, 'error': 'Member access revoked.'}
        result = journal_tool_payload('save_journal_entry', error)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], error['error'])
        market = {'ok': True, 'review': {'unrelated': [1, 2, 3]}}
        self.assertIs(journal_tool_payload('review_market_crt', market), market)

    def test_voice_hook_uses_same_bounded_presentation(self):
        from gbop_voice_web.voice_payload import voice_tool_payload
        source = {'ok': True, 'saved': True, **self.record()}
        result = voice_tool_payload('save_journal_entry', source)
        self.assertEqual(result['journal_view']['kind'], 'bounded_saved_journal_summary')
        self.assertLessEqual(size(result), JOURNAL_PRESENTATION_MAX_CHARS)


if __name__ == '__main__':
    unittest.main()
