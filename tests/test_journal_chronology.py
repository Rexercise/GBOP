"""In-memory chronology and presentation fixtures; no member records or network."""
import json
import unittest

from gbop_voice_web import journal_recall as recall
from gbop_voice_web.journal_presentation import metadata_summary
from tests import test_unified_journal_recall as fixtures


class JournalChronologyTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.UnifiedRecallTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.conn, self.db = fixture.conn, fixture.db
        self.detail, self.event = fixture.detail, fixture.event

    def dated_pair(self, first, second):
        self.detail(100, metadata=first)
        self.event('journal_canonical_v1', {'journal_id':100})
        self.conn.execute("INSERT INTO journals VALUES(110,40,10,20,'Other trade','',NULL,'','2026-10-09')")
        self.detail(110, metadata=second)
        self.event('journal_canonical_v1', {'journal_id':110}, thesis=40)

    def test_approximate_legacy_time_does_not_shadow_valid_trade_date(self):
        self.dated_pair({'trade_date':'2026-10-07', 'reported_entry_at':'around 9 AM'},
                        {'trade_date':'2026-10-06', 'reported_entry_time_text':'about 10 AM'})
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'trade'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['journals'][0]['trade_number'],1)
        self.assertIn('around 9 AM', result['context_text'])

    def test_unknown_chronology_labels_separate_saved_suggestion(self):
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'trade'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'trade_date_ambiguous')
        fallback = result['latest_saved']
        self.assertIn('Latest saved', fallback['label'])
        self.assertFalse(fallback['is_latest_trade'])
        self.assertEqual(fallback['detail_request']['args'], {'trade_number':1})
        self.assertNotIn('journals', result)

    def test_explicit_saved_selection_never_claims_actual_latest(self):
        self.dated_pair({'trade_date':'2026-10-07'}, {'trade_date':'2026-09-01'})
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'saved'})
        self.assertEqual(result['journals'][0]['trade_number'],2)
        self.assertIn('Latest saved record',result['selection_note'])
        self.assertIn('does not establish the latest actual trade',result['selection_note'])

    def test_day_only_comparison_preserves_calendar_chronology_without_zone(self):
        self.dated_pair({'trade_date':'2026-10-07'}, {'trade_date':'2026-10-06'})
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'trade'})
        self.assertEqual(result['journals'][0]['trade_number'],1)

    def test_mixed_unknown_zone_and_exact_day_never_assumes_new_york(self):
        self.dated_pair({'trade_date':'2026-10-07'},
                        {'reported_entry_at':'2026-10-08T01:00:00+00:00'})
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'trade'})
        self.assertEqual(result['status'],'trade_date_ambiguous')
        # With the member's actual zone known, the dates no longer overlap.
        self.conn.execute('UPDATE journal_details SET metadata=? WHERE journal_id=100',
            (json.dumps({'trade_date':'2026-10-07','time_zone':'Asia/Tokyo'}),))
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'trade'})
        self.assertEqual(result['journals'][0]['trade_number'],2)

    def test_known_member_zone_controls_final_mixed_precision_sort(self):
        self.dated_pair({'trade_date':'2026-10-07', 'time_zone':'Etc/GMT+12'},
                        {'reported_entry_at':'2026-10-07T11:00:00+00:00'})
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'trade'})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['journals'][0]['trade_number'],1)

    def test_cross_midnight_entry_instant_orders_trade_not_exit_or_saved_time(self):
        self.dated_pair({'reported_entry_at':'2026-10-06T23:50:00-04:00',
                         'reported_exit_at':'2026-10-07T00:20:00-04:00',
                         'reported_entry_time_text':'about 11:50 PM',
                         'reported_exit_time_text':'around 12:20 AM the next day'},
                        {'reported_entry_at':'2026-10-06T23:40:00-04:00'})
        result = recall.history(self.db,10,20,{'latest':'trade','date_basis':'trade'})
        self.assertEqual(result['journals'][0]['trade_number'],1)
        self.assertIn('around 12:20 AM the next day',result['context_text'])

    def test_first_story_entry_timestamp_is_valid_chronology_but_add_is_not(self):
        row = {'metadata':{'journal_story':json.dumps({'entries':[
            {'entry_index':1,'reported_entry_at':'2026-10-06T23:50:00-04:00'},
            {'entry_index':2,'reported_entry_at':'2026-10-07T00:20:00-04:00'}]})}}
        self.assertEqual(recall._reported_trade_date(row),'2026-10-06T23:50:00-04:00')
        row['metadata']['journal_story']=json.dumps({'entries':[
            {'entry_index':2,'reported_entry_at':'2026-10-07T00:20:00-04:00'}]})
        self.assertIsNone(recall._reported_trade_date(row))

    def test_internal_receipts_are_suppressed_but_member_event_notes_remain(self):
        self.event('journal_execution_v1:synthetic',{'operation_key':'do not display receipt'})
        self.event('execution_receipt_v1',{'operation_key':'do not display old receipt'})
        self.event('entry',{'note':'Entered around 9 AM, my own report.'})
        result=recall.history(self.db,10,20,{'trade_number':1})
        self.assertNotIn('operation_key',result['context_text'])
        self.assertIn('Entered around 9 AM',result['context_text'])
        self.assertEqual(result['journals'][0]['update_count'],1)

    def test_finalized_inference_provenance_survives_bounded_and_full_recall(self):
        meta={'asset':'NAS100','trade_date':'2026-10-06','title':'NAS100 journal',
              'reported_entry_time_text':'around 11:50 PM','reported_exit_time_text':'12:20 AM the next day',
              'story_provenance':{'asset':{'kind':'inferred','source':'normalized_narration','evidence':'NAS'},
                  'trade_date':{'kind':'inferred','source':'relative_date','evidence':'today','timezone':'America/New_York'},
                  'reported_entry_time_text':{'kind':'explicit','source':'narration'}}}
        self.detail(100,metadata=meta)
        self.event('journal_canonical_v1',{'journal_id':100})
        shown=metadata_summary(meta)
        self.assertEqual(shown['story_provenance']['asset'],meta['story_provenance']['asset'])
        self.assertEqual(shown['reported_exit_time_text'],'12:20 AM the next day')
        result=recall.history(self.db,10,20,{'trade_number':1})
        self.assertIn('Inferred instrument: NAS100',result['context_text'])
        self.assertIn('Inferred trade date: 2026-10-06',result['context_text'])
        self.assertIn('Reported entry wording: around 11:50 PM',result['context_text'])


if __name__ == '__main__':
    unittest.main()
