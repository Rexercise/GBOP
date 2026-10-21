"""Synthetic regressions for the Oct 21 voice report; no live model or network.

Real file-backed SQLite, actual conversation/voice guards and AST-loaded bot/web
handlers. Model extraction is represented by explicit tool arguments. This does
not prove speech recognition, model tool selection, PostgreSQL locks or RLS.
"""
import asyncio
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from gbop_voice_web import journal_coach as coach
from gbop_voice_web.journal_inference import infer_story_context
from gbop_voice_web.voice_runtime import guarded_voice_tool, journal_write_result_reported
import test_journal_lifecycle_persistence as storage


class LiveJournalRegressions(unittest.TestCase):
    setUp = storage.JournalLifecyclePersistenceTests.setUp
    review = storage.JournalLifecyclePersistenceTests.review
    handlers = storage.JournalLifecyclePersistenceTests.handlers
    fresh = storage.JournalLifecyclePersistenceTests.fresh
    stage = storage.JournalLifecyclePersistenceTests.stage
    save = storage.JournalLifecyclePersistenceTests.save
    stored = storage.JournalLifecyclePersistenceTests.stored
    history = storage.JournalLifecyclePersistenceTests.history
    count = storage.JournalLifecyclePersistenceTests.count
    action = storage.JournalLifecyclePersistenceTests.action
    reopen = storage.JournalLifecyclePersistenceTests.reopen

    def test_voice_new_trade_gate_retains_narration_then_finalizes_same_draft_without_r(self):
        self.context.begin_turn(None)
        runner = lambda n, a: coach.coach_tool(self.db, 10, 20, n, a)
        first = self.context.run('stage_journal_story', {
            'new_draft': True, 'new_trade': True,
            'raw_story': 'I caught a bullish 9ate8 on gold.', 'story_json': '{}'}, runner)
        self.assertFalse(first['ok'])  # An unverified bypass is still rejected.
        self.assertTrue(first.get('persisted'), first)
        draft_id = first['draft_id']
        self.assertFalse(self.stored(first)['new_trade'])
        self.assertEqual((self.count('journals'), self.count('theses')), (0, 0))
        self.reopen()
        self.context.begin_turn(None)
        resumed = self.context.run('get_journal_story', {'draft_id':draft_id}, runner)
        self.assertFalse(resumed['ready_to_finalize'])
        blocked = self.context.run('save_journal_story', {'draft_id':draft_id,
            'confirmation_text':'Save this journal.'}, runner)
        self.assertEqual(blocked['status'], 'new_trade_intent_required', blocked)
        self.assertEqual((self.count('journals'), self.count('theses')), (0, 0))
        second = self.context.run('stage_journal_story', {
            'draft_id': draft_id, 'new_trade': True, 'story_json': '{}',
            'raw_story': 'Yes, this is a separate new trade. Save this journal.'}, runner)
        self.assertTrue(second['ok'], second)
        saved = self.save(second)
        self.assertEqual(saved['draft_id'], draft_id)
        self.assertEqual((self.count('journals'), self.count('theses'),
                          self.count('journal_story_drafts'), self.count('thesis_executions')), (1, 1, 1, 0))
        self.assertIsNone(self.conn.execute('SELECT result_r FROM journals').fetchone()[0])
        self.assertIn('I caught a bullish', self.stored(second)['raw_story'][0]['text'])
        other = self.action('get_journal_story', {'draft_id': draft_id}, 'Read my journal.',
                            user=30, context=self.fresh(user=30))
        self.assertFalse(other['ok'])

    def test_same_utterance_correction_keeps_target_distinct_from_exit(self):
        words = ("My objective was the Young Lefty high. No, no, the objective was the 8 o'clock high "
                 "because it was a 9ate8. I got out before that. I exited at the 9 o'clock high "
                 "during the 11 o'clock candle's distribution. Risk was 0.5.")
        result = infer_story_context(words)
        self.assertEqual(result['values'].get('objective'), "the 8 o'clock high", result)
        self.assertNotIn('reported_exit_at', result['values'])
        self.assertNotIn('result_r', result['values'])
        self.assertNotIn('risk_r', result['values'])
        self.assertEqual(result['raw_source'], words)

    def test_unresolved_targets_and_saved_facts_are_not_overwritten_by_inference(self):
        words = "My objective was the 8 o'clock high. My objective was the 9 o'clock high."
        ambiguous = infer_story_context(words)
        self.assertNotIn('objective', ambiguous['values'])
        self.assertTrue(any('multiple reported targets' in x for x in ambiguous['uncertainties']))
        saved = infer_story_context("No, the objective was the 9 o'clock high.",
                                    values={'objective': "8 o'clock high"})
        self.assertEqual(saved['values']['objective'], "8 o'clock high")

    def test_unverified_new_trade_cannot_duplicate_existing_matching_trade(self):
        opened = self.action('open_trade', {'asset':'XAUUSD','direction':'Bullish','play':'9ate8',
            'entry_model':'Model 1','tier':2,'risk_r':.5,'market_reference':'none'},
            'Open this execution.', operation_id='existing')
        self.assertTrue(opened['ok'], opened)
        self.context.begin_turn(None)
        runner = lambda n, a: coach.coach_tool(self.db, 10, 20, n, a)
        draft = self.context.run('stage_journal_story', {'new_trade':True, 'new_draft':True,
            'raw_story':'My gold trade was bullish.', 'story_json':'{}'}, runner)
        self.assertTrue(draft.get('persisted'), draft)
        self.context.begin_turn(None)
        result = self.context.run('save_journal_story', {'draft_id':draft['draft_id'],
            'confirmation_text':'Save this journal.'}, runner)
        self.assertEqual(result['status'], 'existing_trade_selection_required', result)
        self.assertEqual((self.count('theses'), self.count('journals')), (1, 1))
        before = self.count('journal_story_drafts')
        another = self.context.run('stage_journal_story', {'new_trade':True, 'new_draft':True,
            'raw_story':'No, this is not a new trade.', 'story_json':'{}'}, runner)
        self.assertFalse(another['ok'])
        self.assertEqual(self.count('journal_story_drafts'), before)

    def close_reported_position(self, path, exit_words='unknown'):
        # A journal-only position has no fabricated opening risk or executions.
        draft = self.stage({'asset': 'XAUUSD', 'direction': 'Bullish', 'play': '9ate8',
                            'objective': "8 o'clock high", 'entries': [{'entry_index': 1, 'status': 'open'}]},
                           'Journal my bullish gold trade, risk 0.5; units not specified.')
        saved = self.save(draft)
        # Existing close handlers operate on OPEN theses; establish that state
        # as synthetic setup only, never by inventing a live execution/risk.
        with self.conn:
            self.conn.execute("UPDATE theses SET status='OPEN' WHERE guild_id=10 AND user_id=20")
        identity = tuple(self.conn.execute('SELECT id,thesis_id FROM journals').fetchone())
        other_before = self.conn.execute('SELECT COUNT(*) FROM journals WHERE user_id=30').fetchone()[0]
        text = 'I got out before my target. Exact time and result are unknown.'
        if exit_words != 'unknown':
            text += ' ' + exit_words
        closed = self.action('close_trade', {'trade_id': saved['trade_number'], 'final_result_r': None,
            'summary': text, 'rule_adherence': 'unknown', 'study_note': 'Target touch is unverified.',
            'reported_exit_at': None, 'reported_exit_time_text': exit_words, 'reported_outcome': 'unknown',
            'market_reference': 'none'}, text, path=path)
        self.assertTrue(closed['ok'], closed)
        self.reopen()
        latest = self.action('get_journal_history', {'limit': 1}, 'Read my latest trade.')
        self.assertTrue(latest['ok'], latest)
        row = self.conn.execute('SELECT * FROM theses').fetchone()
        self.assertEqual(row['status'], 'CLOSED')
        self.assertIsNone(row['final_result_r'])
        self.assertEqual(tuple(self.conn.execute('SELECT id,thesis_id FROM journals').fetchone()), identity)
        metadata = json.loads(self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0])
        self.assertEqual(metadata.get('reported_exit_time_text'), exit_words)
        self.assertNotIn('reported_exit_at', metadata)
        self.assertIn('I got out before my target', str(latest))
        self.assertIn("8 o'clock high", str(latest))
        if exit_words != 'unknown':
            self.assertIn(exit_words, str(latest))
        self.assertEqual((self.count('journals'), self.count('theses'), self.count('thesis_executions')), (1, 1, 0))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals WHERE user_id=30').fetchone()[0], other_before)
        foreign = self.action('close_trade', {'trade_id': saved['trade_number']}, text,
                              path=path, user=30, context=self.fresh(user=30))
        self.assertFalse(foreign['ok'])

    def test_discord_explicit_exit_without_time_result_or_r(self):
        self.close_reported_position('bot.py')

    def test_web_explicit_exit_without_time_result_or_r(self):
        self.close_reported_position('gbop_voice_web/server.py')

    def test_actual_exit_level_and_candle_survive_latest_recall_separate_from_objective(self):
        self.close_reported_position('bot.py', "I exited at the 9 o'clock high during the 11 o'clock candle's distribution.")

    def test_two_members_matching_trade_numbers_remain_isolated(self):
        first = self.stage({'asset':'XAUUSD','direction':'Bullish','context_notes':'First member private facts.'},
                           'Journal my bullish gold trade.')
        self.save(first)
        second = self.stage({'asset':'XAUUSD','direction':'Bearish','context_notes':'Other member private facts.'},
                            'Journal my bearish gold trade.', user=30, context=self.fresh(user=30))
        self.save(second, user=30, context=self.fresh(user=30))
        before = [tuple(row) for row in self.conn.execute('SELECT * FROM journals WHERE user_id=30')]
        bad = self.action('stage_journal_story', {'draft_id':second['draft_id'], 'story_json':'{"direction":"Bullish"}'},
                          'Correct that draft.', context=self.fresh())
        self.assertFalse(bad['ok'])
        self.assertNotIn('Other member private facts', str(bad))
        self.assertEqual([tuple(row) for row in self.conn.execute('SELECT * FROM journals WHERE user_id=30')], before)
        self.assertNotIn('Other member private facts', str(self.history()))

    def invalid_event_result(self, path):
        opened = self.action('open_trade', {'asset':'XAUUSD','direction':'Bullish','play':'9ate8',
            'entry_model':'Model 1','tier':2,'risk_r':.5,'market_reference':'none'},
            'Record this separate trade with 0.5R risk.', operation_id='synthetic-open', path=path)
        self.assertTrue(opened['ok'], opened)
        async def exercise():
            session = NS(member=NS(id=20), websocket=object(), market_context=self.context,
                         _voice_turn_count=1, closed=False, authorize_tool=AsyncMock(return_value=None))
            async def event(value):
                return self.action('record_trade_event', {'trade_id':opened['trade_id'],
                    'event':'ENTRY_NOTE','details':'Reported approximate entry.', 'result_r':value,
                    'market_reference':'none'}, 'Add my entry note.', path=path)
            before = self.count('thesis_events')
            result = await guarded_voice_tool(session, 'record_trade_event', {}, 'invalid', lambda:event('null'))
            self.assertFalse(result['ok'])
            self.assertFalse(result.get('write_attempted'), result)
            self.assertEqual(session._journal_write_recovery['outcome']['status'], 'not_saved')
            self.assertEqual(self.count('thesis_events'), before)
            journal_write_result_reported(session, 'record_trade_event', 'invalid', result)
            session._voice_turn_count += 1
            result = await guarded_voice_tool(session, 'record_trade_event', {}, 'corrected', lambda:event(None))
            self.assertTrue(result['ok'], result)
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM thesis_events WHERE event='ENTRY_NOTE'").fetchone()[0], 1)
        asyncio.run(exercise())

    def test_discord_invalid_event_result_is_known_not_saved_and_does_not_poison_voice(self):
        self.invalid_event_result('bot.py')

    def test_web_invalid_event_result_is_known_not_saved_and_does_not_poison_voice(self):
        self.invalid_event_result('gbop_voice_web/server.py')


if __name__ == '__main__':
    unittest.main()
