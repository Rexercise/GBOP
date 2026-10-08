"""Real conversation + journal-handler synthetic handoffs; no production database."""
import json
import unittest

from gbop_voice_web import journal_coach as coach, member_continuity as continuity
from gbop_voice_web.market_conversation import MarketConversation
import test_progressive_journal as fixture


class MemberContinuityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ProgressiveJournalTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.db = self.case.db
        self.conn = self.case.conn
        continuity.init_continuity(self.db)
        self.text = self.case.context

    def fresh(self, slot='voice2', user=20, guild=10):
        return MarketConversation((guild, user, slot), auth_provider=(self.db, guild, user))

    def call(self, context, name, args):
        return context.run(name, args,
            lambda n, a: coach.coach_tool(self.db, 10, 20, n, a))

    def test_text_narration_hydrates_both_voices_and_corrects_same_unfinished_draft(self):
        self.text.begin_turn('Journal my NAS trade. I entered short on the second sweep around nine this morning.')
        first = continuity.hydrate(self.text)['unfinished_drafts'][0]
        self.text.close()
        voice1 = self.fresh('voice1')
        self.assertEqual(continuity.hydrate(voice1)['unfinished_drafts'][0]['draft_id'], first['draft_id'])
        voice1.begin_turn(None)
        loaded = self.call(voice1, 'get_journal_story', {'draft_id': first['draft_id']})
        self.assertIn('second sweep', loaded['raw_story_text'])
        corrected = self.call(voice1, 'stage_journal_story', {'draft_id': first['draft_id'],
            'raw_story': 'Actually my entry was on the third sweep, not the second.',
            'story_json': json.dumps({'context_notes': 'Entry was on the third sweep.'})})
        self.assertTrue(corrected['ok'], corrected)
        voice2 = self.fresh('voice2')
        projection = continuity.hydrate(voice2)
        self.assertIn('third sweep', projection['unfinished_drafts'][0]['reported_facts']['context_notes'])
        voice2.begin_turn(None)
        loaded_again = self.call(voice2, 'get_journal_story', {'draft_id': first['draft_id']})
        self.assertEqual(loaded_again['draft_id'], first['draft_id'])
        self.assertIn('third sweep', loaded_again['raw_story_text'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)

    def test_newer_voice_fences_old_actual_draft_write_without_losing_progress(self):
        self.text.begin_turn('Journal my NAS trade. I entered short around nine.')
        draft = continuity.hydrate(self.text)['unfinished_drafts'][0]
        newest = self.fresh('voice2')
        newest.begin_turn(None)
        old = self.call(self.text, 'stage_journal_story', {'draft_id': draft['draft_id'],
            'story_json': '{"context_notes":"Old stale correction"}'})
        self.assertFalse(old['ok'], old)
        self.assertNotIn('Old stale correction', self.case.stored(draft['draft_id'])['values'].get('context_notes', ''))
        self.assertTrue(self.call(newest, 'get_journal_story', {'draft_id': draft['draft_id']})['ok'])

    def test_audio_optout_is_durable_before_stage_and_generic_journal_request_cannot_resume(self):
        voice = self.fresh()
        voice.begin_turn(None)
        result = self.call(voice, 'stage_journal_story', {'story_json': '{"asset":"NAS100"}',
            'raw_story': 'Do not record this. I entered short after nine.'})
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['persisted'])
        fresh = self.fresh('voice1')
        self.assertTrue(continuity.hydrate(fresh)['recording_paused'])
        fresh.begin_turn('Journal my NAS trade. I entered short around ten.')
        result = self.call(fresh, 'stage_journal_story', {'story_json': '{"asset":"NAS100"}'})
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['persisted'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journal_story_drafts').fetchone()[0], 0)

    def test_actual_execution_receipt_and_last_reference_survive_handoff_without_replay(self):
        handlers = self.case.handlers('bot.py')
        self.text.begin_turn('I entered one NAS short with 0.25R risk.')
        result = self.text.run('open_trade', dict(asset='NAS100', direction='Bearish', play='Young Lefty',
            entry_model='Blessed Thief', risk_r=.25),
            lambda n, a: handlers['ai_open_trade'](20, a), operation_id='one-owned-execution')
        self.assertTrue(result['ok'], result)
        before = self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0]
        fresh = self.fresh()
        hydrated = continuity.hydrate(fresh)
        self.assertEqual(hydrated['last_referenced_trade']['trade_number'], 1)
        self.assertTrue(any(row['kind'] == 'execution_saved' for row in hydrated['verified_recent_writes']))
        fresh.begin_turn(None)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], before)
        self.assertEqual(before, 1)


if __name__ == '__main__':
    unittest.main()
