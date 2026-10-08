"""Synthetic saves and spoken corrections; no member data or API calls."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import journal_coach as coach
from gbop_voice_web.market_conversation import MarketConversation
import test_journal_story as fixture


class OptionalJournalRTests(unittest.TestCase):
    setUp = fixture.JournalStoryTests.setUp
    tearDown = fixture.JournalStoryTests.tearDown
    review = fixture.JournalStoryTests.review
    handlers = fixture.JournalStoryTests.handlers
    call = fixture.JournalStoryTests.call
    stage = fixture.JournalStoryTests.stage
    saved = fixture.JournalStoryTests.saved

    def single(self):
        story = deepcopy(fixture.STORY)
        story['entries'] = [{'entry_index': 1, 'entry_model': 'Super Soup'}]
        return story

    def row(self):
        return self.conn.execute('SELECT * FROM journals').fetchone()

    def test_single_entry_without_r_saves_unknown_and_counts_no_outcome(self):
        saved = self.saved(self.stage(self.single()))
        self.assertTrue(saved['ok'], saved)
        self.assertIn('R unknown', saved['description'])
        self.assertIsNone(self.row()['result_r'])
        self.assertEqual(coach.performance(self.db, 10, 20, {})['summary']['known_outcomes'], 0)

    def test_add_then_clear_saved_r_preserves_one_trade_cash_and_audit(self):
        draft = self.stage(self.single())
        self.assertTrue(self.saved(draft)['ok'])
        original_id = self.row()['id']
        added = self.stage({'result_r': 3, 'entries': [{'entry_index': 1,
            'risk_r': .75, 'risk_text': '150 USD', 'pnl_text': '+450 USD'}]}, draft_id=draft['draft_id'])
        self.assertEqual(self.saved(added)['result_r'], 3)
        cleared = self.stage({'clear_fields': ['result_r', 'entries.1.risk_r']}, draft_id=draft['draft_id'])
        self.assertTrue(cleared['ok'], cleared)
        result = self.saved(cleared)
        self.assertTrue(result['ok'], result)
        self.assertIsNone(result['result_r'])
        self.assertNotIn('risk_r', cleared['story']['entries'][0])
        self.assertEqual(cleared['story']['entries'][0]['risk_text'], '150 USD')
        self.assertEqual(cleared['story']['entries'][0]['pnl_text'], '+450 USD')
        self.assertEqual(self.row()['id'], original_id)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM theses').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM thesis_executions').fetchone()[0], 0)
        self.assertIsNone(self.conn.execute('SELECT final_result_r FROM theses').fetchone()[0])
        self.assertTrue(self.context._journal_story['corrections'])

    def test_zero_is_known_and_signed_results_are_preserved(self):
        draft = self.stage(self.single())
        for reported in (0, -1.5, 2):
            changed = self.stage({'result_r': reported}, draft_id=draft['draft_id'])
            saved = self.saved(changed)
            self.assertTrue(saved['ok'], saved)
            self.assertEqual(saved['result_r'], reported)
            self.assertIn(f'Overall result: {reported:g}R', saved['description'])

    def test_null_patch_preserves_known_r_explicit_clear_retracts(self):
        draft = self.stage({**self.single(), 'result_r': 2})
        self.assertTrue(self.saved(draft)['ok'])
        patch = self.stage({'result_r': None}, draft_id=draft['draft_id'])
        self.assertEqual(self.saved(patch)['result_r'], 2)
        patch = self.stage({'clear_fields': ['result_r']}, draft_id=draft['draft_id'])
        self.assertIsNone(self.saved(patch)['result_r'])

    def test_reconnected_voice_can_clear_the_same_saved_story(self):
        draft = self.stage()
        self.assertTrue(self.saved(draft)['ok'])
        fresh = MarketConversation((10, 20, 'new-voice'), auth_provider=(self.db, 10, 20))
        resumed = self.call('get_journal_story', {'draft_id': draft['draft_id']}, text=None, context=fresh)
        self.assertTrue(resumed['ok'], resumed)
        cleared = self.call('stage_journal_story', {'draft_id': draft['draft_id'],
            'raw_story': 'Remove the risk R from entry one.',
            'story_json': json.dumps({'clear_fields': ['entries.1.risk_r']})}, text=None, context=fresh)
        self.assertTrue(cleared['ok'], cleared)
        saved = self.call('save_journal_story', {'draft_id': draft['draft_id'],
            'confirmation_text': 'Save the journal.'}, text=None, context=fresh)
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(saved['trade_number'], 1)
        self.assertIn('entry risk: unknown', saved['description'])

    def test_selected_execution_keeps_its_recorded_risk_when_narrative_r_cleared(self):
        env = self.handlers('bot.py')
        opened = env['ai_open_trade'](20, dict(asset='NAS100', direction='Bearish',
            play='Young Lefty', entry_model='Blessed Thief', risk_r=.75))
        draft = self.stage(trade_number=opened['trade_id'])
        cleared = self.stage({'clear_fields': ['entries.1.risk_r']}, draft_id=draft['draft_id'])
        self.assertTrue(cleared['ok'], cleared)
        self.assertTrue(self.saved(cleared)['ok'])
        self.assertEqual(self.conn.execute('SELECT risk_r FROM thesis_executions').fetchone()[0], .75)

    def test_rejected_finalize_does_not_poison_later_authorized_retry(self):
        draft = self.stage(self.single())
        denied = self.call('save_journal_story', {'draft_id': draft['draft_id']}, text='I am still talking.')
        self.assertFalse(denied['ok'])
        saved = self.saved(draft)
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 1)

    def test_uncertain_write_stays_blocked_against_duplicate_retry(self):
        self.context.begin_turn('Save this journal.')
        calls = []
        def interrupted(name, args):
            calls.append(name)
            raise RuntimeError('synthetic interrupted transport')
        args = {'description': 'Synthetic journal'}
        with self.assertRaises(RuntimeError):
            self.context.run('save_journal_entry', args, interrupted)
        result = self.context.run('save_journal_entry', args, interrupted)
        self.assertEqual(result['status'], 'journal_outcome_uncertain')
        self.assertEqual(len(calls), 1)

    def test_invalid_result_is_retained_as_narration_without_numeric_performance(self):
        for invalid in (True, 'unknown', float('nan'), float('inf')):
            staged = self.stage({**self.single(), 'result_r': invalid})
            self.assertFalse(staged['ok'])
            self.assertEqual(staged['status'], 'narration_saved_needs_correction')
            self.assertTrue(staged['persisted'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 0)
