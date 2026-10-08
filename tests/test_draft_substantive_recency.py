"""Synthetic journal draft clocks; no live reads or writes."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from gbop_voice_web import journal_drafts as drafts
import test_contextual_journals as fixture


class DraftSubstantiveRecencyTests(unittest.TestCase):
    setUp=fixture.ContextualJournalTests.setUp
    tearDown=fixture.ContextualJournalTests.tearDown
    review=fixture.ContextualJournalTests.review

    def save(self, value, at):
        with patch.object(drafts,'stamp',return_value=at),self.db() as conn:
            return drafts.write(conn,10,20,value,expected_revision=value.get('storage_revision'))

    def initial(self):
        return self.save({'id':'synthetic-draft','values':{'asset':'NAS100','entries':[]},
            'raw_story':[{'text':'I entered NAS short after the sweep.','recorded_at':'2026-10-07T10:00:00Z'}],
            'asked':[],'provenance':{}},'2026-10-07T10:01:00Z')

    def test_bookkeeping_and_pure_resume_do_not_move_substantive_clock(self):
        first=self.initial();at=first['substantive_updated_at']
        update=deepcopy(first);update.update(asked=['date'],recording_paused=True,save_authorized=False)
        second=self.save(update,'2026-10-07T11:00:00Z')
        self.assertEqual(second['substantive_updated_at'],at)
        self.assertEqual(second['updated_at'],'2026-10-07T11:00:00Z')
        second['raw_story'].append({'text':'Resume my journal.','recorded_at':'2026-10-07T12:00:00Z'})
        third=self.save(second,'2026-10-07T12:00:00Z')
        self.assertEqual(third['substantive_updated_at'],at)
        self.assertEqual(len(third['raw_story']),2)

    def test_content_correction_and_new_narration_advance_clock(self):
        first=self.initial();first['values']['direction']='Bearish'
        second=self.save(first,'2026-10-07T11:00:00Z')
        self.assertEqual(second['substantive_updated_at'],'2026-10-07T11:00:00Z')
        second['raw_story'].append({'text':'I felt rushed at the second entry.','recorded_at':'2026-10-07T12:00:00Z'})
        third=self.save(second,'2026-10-07T12:00:00Z')
        self.assertEqual(third['substantive_updated_at'],'2026-10-07T12:00:00Z')

    def test_timestamp_only_churn_and_forged_clock_do_not_override_server(self):
        first=self.initial();first['raw_story'][0]['recorded_at']='2099-01-01T00:00:00Z'
        first['substantive_updated_at']='2099-01-01T00:00:00Z'
        later=self.save(first,'2026-10-07T11:00:00Z')
        self.assertEqual(later['substantive_updated_at'],'2026-10-07T10:01:00Z')

    def test_empty_control_draft_has_no_content_clock(self):
        first=self.save({'id':'empty-draft','values':{'entries':[],'title':'Reported trade journal'},
            'provenance':{'title':{'source':'derived_title'}},'raw_story':[{'text':'Start my journal.'}]},
            '2026-10-07T10:00:00Z')
        self.assertNotIn('substantive_updated_at',first)

    def test_control_filter_preserves_actual_trading_statements(self):
        control=('Resume my journal.','Please read Trade #5.','Can you save this draft?','yes')
        facts=('Journal my NAS trade.','Resume my journal. I felt nervous.',
               'I exited around midnight.','Journal trade date 2026-10-07.','感到紧张','ΩΩΩ','Save 我的交易','Journal résumé')
        raw={'raw_story':[{'text':t} for t in control+facts]}
        self.assertEqual([p['text'] for p in drafts.substantive_raw_passages(raw)],list(facts))

    def test_nonmapping_legacy_provenance_does_not_hide_content(self):
        content=drafts.substantive_content({'values':{'title':'My note'},'provenance':{'title':'legacy'}})
        self.assertEqual(content['values']['title'],'My note')

if __name__=='__main__':unittest.main()
