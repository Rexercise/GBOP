"""Single-confirmation regressions, synthetic drafts only; no production access."""
from copy import deepcopy
import unittest

from gbop_voice_web import journal_coach as coach
from gbop_voice_web.journal_discard import _confirm
import test_journal_discard_flow as fixture


class DiscardConfirmationOnceTests(unittest.TestCase):
    setUp=fixture.JournalDiscardFlowTests.setUp
    tearDown=fixture.JournalDiscardFlowTests.tearDown
    review=fixture.JournalDiscardFlowTests.review
    handlers=fixture.JournalDiscardFlowTests.handlers
    call=fixture.JournalDiscardFlowTests.call
    stage=fixture.JournalDiscardFlowTests.stage
    saved=fixture.JournalDiscardFlowTests.saved
    fresh=fixture.JournalDiscardFlowTests.fresh
    stored=fixture.JournalDiscardFlowTests.stored
    preview=fixture.JournalDiscardFlowTests.preview
    discard=fixture.JournalDiscardFlowTests.discard

    def test_same_prompt_words_survive_voice_punctuation_changes(self):
        first=self.stage();preview=self.preview(first,deliver=False)
        spoken=preview['confirmation_prompt'].replace('"','').replace('(','').replace(')','')
        self.context.complete_response(spoken,response_id='voice-transcript')
        self.assertTrue(self.context._journal_discard_preview['delivered'])
        self.assertTrue(self.discard(first,text='Yes, go ahead.')['discarded'])

    def test_curly_quotes_are_not_a_new_question(self):
        first=self.stage();preview=self.preview(first,deliver=False)
        self.context.complete_response(preview['confirmation_prompt'].replace('"','“'),response_id='quotes')
        self.assertTrue(self.discard(first,text='Yes, I confirm.')['discarded'])

    def test_missing_words_and_partial_playback_still_require_a_real_preview(self):
        first=self.stage();preview=self.preview(first,deliver=False)
        self.context.complete_response(preview['confirmation_prompt'].split('?')[0]+'?',response_id='missing-disclosure')
        self.assertFalse(self.context._journal_discard_preview['delivered'])
        self.context.complete_response(preview['confirmation_prompt'],response_id='partial',completed=False)
        self.assertFalse(self.discard(first)['ok'])
        self.assertEqual(self.stored(first['draft_id'])['draft_status'],'unfinished')

    def test_redundant_prepare_preserves_the_same_delivered_preview_and_yes(self):
        first=self.stage();self.preview(first);pending=deepcopy(self.context._journal_discard_preview)
        self.context.begin_turn('Yes, go ahead.')
        repeated=self.context.run('prepare_journal_discard',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertTrue(repeated['confirmation_already_delivered'])
        self.assertNotIn('confirmation_prompt',repeated)
        self.assertEqual(self.context._journal_discard_preview,pending)
        result=self.context.run('discard_journal_story',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertTrue(result['discarded'],result)

    def test_repeated_prepare_before_delivery_does_not_move_expiry(self):
        first=self.stage();preview=self.preview(first,deliver=False);pending=deepcopy(self.context._journal_discard_preview)
        repeated=self.context.run('prepare_journal_discard',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertEqual(repeated['confirmation_prompt'],preview['confirmation_prompt'])
        self.assertEqual(self.context._journal_discard_preview,pending)

    def test_undelivered_preview_in_next_turn_gets_a_fresh_deliverable_receipt(self):
        first=self.stage();self.preview(first,deliver=False)
        old_generation=self.context._journal_discard_preview['generation']
        self.context.begin_turn('Yes, go ahead.')
        repeated=self.context.run('prepare_journal_discard',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertIn('confirmation_prompt',repeated)
        self.assertEqual(self.context._journal_discard_preview['generation'],old_generation+1)
        self.assertFalse(self.context._journal_discard_preview['delivered'])
        self.context.complete_response(repeated['confirmation_prompt'],response_id='fresh-preview')
        self.assertTrue(self.context._journal_discard_preview['delivered'])
        self.assertTrue(self.discard(first,text='Yes, please go ahead.')['discarded'])

    def test_actual_edit_invalidates_prior_confirmation(self):
        first=self.stage();self.preview(first);other=self.fresh()
        self.call('get_journal_story',{'draft_id':first['draft_id']},'Read it.',context=other)
        self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"New synthetic fact"}'},'New synthetic fact.',context=other)
        self.assertFalse(self.discard(first,text='Yes, go ahead.')['ok'])
        repeated=self.context.run('prepare_journal_discard',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertIn('confirmation_prompt',repeated)
        self.assertFalse(self.context._journal_discard_preview['delivered'])
        self.assertEqual(self.stored(first['draft_id'])['draft_status'],'unfinished')

    def test_affirmatives_remain_bounded_and_unambiguous(self):
        for value in ('Yes','Sure','Okay','Yes. Go ahead.','Yes, please do.','Yes, go ahead and discard it.','Yes, go ahead.','Yes, I confirm.','I confirm','Yes, discard it.','Please delete that draft.','Yes, please do it.','Yes, please go ahead.','Yes, discard it, please.','Yes, go ahead and delete the draft.'):
            with self.subTest(value=value):self.assertTrue(_confirm(value))
        for value in ('No','Yes, but do not delete it.','I do not confirm','Go ahead?','Yes, delete the trade','Yes, discard every draft','Maybe','She said yes','Yes to something else'):
            with self.subTest(value=value):self.assertFalse(_confirm(value))


if __name__=='__main__':unittest.main()
