"""Synthetic offline conversations only; no production messages or journal writes."""
import ast
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from gbop_voice_web import journal_coach as coach, trade_feelings as feelings
from gbop_voice_web.market_conversation import MarketConversation
import test_trade_feelings as fixture

ROOT = Path(__file__).resolve().parents[1]
WORDS = 'Felt relaxed on the first entry. Confidence started to fade after the later add.'
NOTE = 'Note for Trade #1: ' + WORDS
QUESTION = 'Should I record this as a mid-trade feeling for Trade #1?'
QUALIFIED_WORDS = ('I felt very calm in the first test setup and calm in the second setup '
    'because everything was quite orderly, but I started to feel a little less calm '
    'after entering the second setup.')
QUALIFIED_NOTE = 'Save this as a mid-trade feeling for Trade #2: ' + QUALIFIED_WORDS


class FeelingConversationTests(unittest.TestCase):
    setUp = fixture.TradeFeelingTests.setUp
    tearDown = fixture.TradeFeelingTests.tearDown
    review = fixture.TradeFeelingTests.review
    handlers = fixture.TradeFeelingTests.handlers
    action = fixture.TradeFeelingTests.action
    opened = fixture.TradeFeelingTests.opened
    metadata = fixture.TradeFeelingTests.metadata

    def args(self, **changes):
        return dict(trade_number=1, feeling=WORDS, stage='mid', reported_at=None,
                    correction_of=None, skip=False, **changes)

    def run_save(self, args=None):
        return self.context.run('record_trade_feeling', args or self.args(),
            lambda n, a: coach.coach_tool(self.db, 10, 20, n, a))

    def pending(self, *, text=NOTE, question=QUESTION, completed=True):
        self.context.begin_turn(text)
        self.context.complete_response(question, completed=completed)

    def test_natural_multisentence_note_and_source_spelling_are_preserved(self):
        self.opened()
        words = 'Felt relaxed during the trade. Confidence started to fade later.'
        self.context.begin_turn('Note for Trade #1: ' + words)
        result = self.run_save({**self.args(), 'feeling': words.lower()})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['feeling']['feeling'], words)
        self.assertEqual(result['feeling']['stage'], 'mid')
        self.assertIsNone(result['feeling']['reported_at'])
        self.assertIsNone(result['feeling']['correction_of'])
        self.assertEqual(len(self.metadata()['feeling_history']), 1)

    def test_explicit_became_feeling_note_keeps_full_exact_words_after_reconnect(self):
        self.opened()
        words = 'I felt uncertain during the trade. I became more relaxed once I followed my original plan.'
        self.context.begin_turn('Save this as a mid-trade feeling for Trade #1: ' + words)
        result = self.run_save({**self.args(), 'feeling': words})
        self.assertTrue(result['ok'], result)
        self.context.close()
        self.context = MarketConversation((10,20,'fresh-became-session'),auth_provider=(self.db,10,20))
        self.assertEqual(self.metadata()['feeling_history'][0]['feeling'], words)
        self.assertEqual(self.metadata()['feeling_history'][0]['stage'], 'mid')
        self.assertIsNone(self.metadata()['feeling_history'][0]['reported_at'])

    def test_became_note_stage_conflict_needs_one_exact_confirmation(self):
        self.opened()
        words = 'I felt uncertain before entry. I became more relaxed once I followed my original plan.'
        self.context.begin_turn('Save this as a mid-trade feeling for Trade #1: ' + words)
        args = {**self.args(), 'feeling': words}
        self.assertFalse(self.run_save(args)['ok'])
        self.assertNotIn('feeling_history', self.metadata())
        self.context.complete_response(QUESTION, completed=True)
        self.context.begin_turn('Yes')
        result = self.run_save(args)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['feeling']['feeling'], words)
        self.context.complete_response('Saved the exact note.')
        self.context.begin_turn('Yes')
        self.assertFalse(self.run_save(args)['ok'])
        self.assertEqual(len(self.metadata()['feeling_history']),1)

    def test_became_note_cannot_be_paraphrased_retargeted_or_given_a_time(self):
        self.opened(); self.opened()
        words = 'I became a little less calm during the trade.'
        for changes in ({'feeling':'calm'}, {'feeling':'less calm'}, {'trade_number':2},
                {'stage':'open'}, {'reported_at':'2026-10-01T10:00:00Z'}, {'correction_of':1}):
            with self.subTest(changes=changes):
                self.context.begin_turn('Save this as a mid-trade feeling for Trade #1: '+words)
                self.assertFalse(self.run_save({**self.args(),'feeling':words,**changes})['ok'])
        self.assertNotIn('feeling_history',self.metadata(1))
        self.assertNotIn('feeling_history',self.metadata(2))

    def test_became_grammar_does_not_authorize_unrequested_save_or_guess_stage(self):
        self.opened()
        for text in ('I became more relaxed during the trade.',
                'Note for Trade #1: I became more relaxed.'):
            self.context.begin_turn(text)
            words = text.split(': ',1)[-1]
            self.assertFalse(self.run_save({**self.args(),'feeling':words})['ok'])
        self.assertNotIn('feeling_history',self.metadata())

    def test_became_note_requires_own_factual_unquoted_unnegated_save_request(self):
        self.opened()
        for words in ('My friend became relaxed during the trade.',
                'I became aware that my friend was nervous during the trade.',
                'I became calm during the trade and my friend became nervous.',
                'If I became more relaxed during the trade.',
                'I might have become more relaxed during the trade.',
                'Did I become more relaxed during the trade?',
                'My coach said "I became more relaxed during the trade."',
                'I became more relaxed during the trade. Do not save this.',
                'I became more relaxed during the trade. Preview only.'):
            with self.subTest(words=words):
                self.context.begin_turn('Save this as a mid-trade feeling for Trade #1: '+words)
                self.assertFalse(self.run_save({**self.args(),'feeling':words})['ok'])
        self.assertNotIn('feeling_history',self.metadata())

    def test_became_note_keeps_authenticated_member_binding(self):
        self.opened()
        words = 'I became more relaxed during the trade.'
        self.context.begin_turn('Save this as a mid-trade feeling for Trade #1: '+words)
        result = self.context.run('record_trade_feeling',{**self.args(),'feeling':words},
            lambda name,args:coach.coach_tool(self.db,10,30,name,args))
        self.assertFalse(result['ok'])
        self.assertNotIn('feeling_history',self.metadata())

    def test_direct_first_person_multisentence_and_correction_keep_history(self):
        self.opened()
        words = 'I felt relaxed at entry. My confidence began to fade after adding.'
        self.context.begin_turn(words)
        first = self.run_save({**self.args(), 'feeling': words})
        self.assertTrue(first['ok'], first)
        revised = 'I felt uneasy at entry. My confidence began to improve after adding.'
        self.context.begin_turn('Correct that: ' + revised)
        result = self.run_save({**self.args(), 'feeling': revised, 'correction_of': 1})
        self.assertTrue(result['ok'], result)
        self.assertEqual([r['feeling'] for r in self.metadata()['feeling_history']], [words, revised])
        self.assertEqual(result['feeling']['correction_of'], 1)

    def test_stage_clarification_yes_and_repeated_confirm_never_duplicate(self):
        self.opened()
        self.pending()
        self.assertNotIn('feeling_history', self.metadata())
        self.context.begin_turn('Yes')
        first = self.run_save()
        self.assertTrue(first['ok'], first)
        self.assertEqual(self.run_save(), first)
        self.context.complete_response('Saved your note.')
        self.context.begin_turn('Yes')
        self.assertFalse(self.run_save()['ok'])
        self.assertEqual(len(self.metadata()['feeling_history']), 1)

    def test_clarification_without_market_selection_and_fragment_reply(self):
        self.opened()
        self.context.selected = self.context.requested = None
        self.pending()
        replacement = 'Relaxed during the trade. Confidence started to fade later.'
        self.context.begin_turn(replacement)
        saved = self.run_save({**self.args(), 'feeling': replacement})
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(saved['feeling']['feeling'], replacement)

    def test_pending_scope_values_cannot_be_changed_by_tool_args(self):
        self.opened(); self.opened()
        for changed in ({'trade_number': 2}, {'stage': 'close'}, {'feeling': 'relaxed'},
                        {'reported_at': '2026-10-01T10:00:00Z'}, {'correction_of': 1}):
            with self.subTest(changed=changed):
                self.pending()
                self.context.begin_turn('Yes')
                self.assertFalse(self.run_save({**self.args(), **changed})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_missing_partial_stale_multiple_or_unrelated_questions_do_not_authorize_yes(self):
        self.opened()
        for question, completed in ((QUESTION, False), ('Would you like to close Trade #1?', True),
                ('Should I save a mid-trade feeling for Trade #2?', True),
                ('Should I record this as an opening or mid-trade feeling for Trade #1?', True),
                (QUESTION + ' Should I also close Trade #1?', True)):
            with self.subTest(question=question, completed=completed):
                self.pending(question=question, completed=completed)
                self.context.begin_turn('Yes')
                self.assertFalse(self.run_save()['ok'])
        self.context.begin_turn(NOTE)
        generation = self.context.generation
        self.context.begin_turn('Show my trade history.')
        self.context.complete_response(QUESTION, generation=generation)
        self.context.begin_turn('Yes')
        self.assertFalse(self.run_save()['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_expiry_cancel_reconnect_auth_change_and_unrelated_turn_discard_source(self):
        self.opened()
        for change in ('expired', 'cancelled', 'reconnected', 'auth_changed', 'unrelated', 'owner_changed'):
            with self.subTest(change=change):
                self.context = MarketConversation((10, 20, 'fresh'), auth_provider=(self.db, 10, 20))
                self.context.bind_auth(self.db, 10, 20)
                self.pending()
                if change == 'expired':
                    self.context._feeling_note['recorded_at'] = (feelings._now()-timedelta(minutes=16)).isoformat()
                elif change == 'cancelled':
                    self.context.invalidate()
                elif change == 'reconnected':
                    self.context = MarketConversation((10, 20, 'other'), auth_provider=(self.db, 10, 20))
                elif change == 'auth_changed':
                    self.conn.execute("UPDATE members SET updated_at='auth-v2' WHERE user_id=20")
                elif change == 'unrelated':
                    self.context.begin_turn('Show my recent trades.')
                else:
                    self.context.owner = (10, 30, 'fresh')
                self.context.begin_turn('Yes')
                self.assertFalse(self.run_save()['ok'])
                self.conn.execute("UPDATE members SET updated_at='auth-v1' WHERE user_id=20")
        self.assertNotIn('feeling_history', self.metadata())

    def test_preview_hypothetical_quotes_and_third_party_sources_never_save(self):
        self.opened()
        for text, phrase in (
                ('Preview only: I felt relaxed at entry.', 'relaxed'),
                ('Do not save this. I felt relaxed at entry.', 'relaxed'),
                ('If I felt relaxed, would that be good?', 'relaxed'),
                ('My coach said "I felt relaxed at entry".', 'relaxed'),
                ("My coach wrote 'I felt relaxed at entry'.", 'relaxed'),
                ('Note for Trade #1: She felt relaxed at entry.', 'relaxed'),
                ('Note for Trade #1: Confident in his execution.', 'Confident in his execution.'),
                ('Note for Trade #1: I feel calm and Bob felt nervous.', 'I feel calm and Bob felt nervous.'),
                ('I feel focused. Someone else feels relaxed.', 'relaxed'),
                ('The trade was profitable.', 'confident'),
                ('Note for Trade #1: ' + WORDS + ' Preview only.', WORDS)):
            with self.subTest(text=text):
                self.context.begin_turn(text)
                self.assertFalse(self.run_save({**self.args(), 'feeling': phrase})['ok'])
                self.context.complete_response(QUESTION)
                self.context.begin_turn('Yes')
                self.assertFalse(self.run_save({**self.args(), 'feeling': phrase})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_negation_and_qualifiers_cannot_be_lost_or_wrong_trade_selected(self):
        self.opened(); self.opened()
        for text, phrase, number in (
                ('Note for Trade #1: Felt not confident at entry.', 'confident', 1),
                ('Note for Trade #1: Felt slightly anxious at entry.', 'anxious', 1),
                (NOTE, WORDS, 2),
                ('I felt relaxed at entry.', '.', 1)):
            self.context.begin_turn(text)
            self.assertFalse(self.run_save({**self.args(), 'feeling': phrase, 'trade_number': number})['ok'])
        words = 'Felt not confident at entry.'
        self.context.begin_turn('Note for Trade #1: ' + words)
        self.assertTrue(self.run_save({**self.args(), 'feeling': words, 'stage': 'open'})['ok'])
        self.assertEqual(self.metadata()['feeling_history'][0]['feeling'], words)

    def test_new_note_cannot_invent_stage_time_or_save_other_subjects(self):
        self.opened()
        words = 'Felt calm at entry.'
        for changes in ({'stage': 'close'}, {'stage': 'open', 'reported_at': '2026-10-01T12:34:00Z'},
                        {'stage': 'open', 'correction_of': 1}):
            self.context.begin_turn('Note for Trade #1: ' + words)
            self.assertFalse(self.run_save({**self.args(), 'feeling': words, **changes})['ok'])
        for words in ('Felt Bob was nervous during the trade.', 'Feeling the market is calm.',
                      'I feel calm and Bob felt nervous.'):
            self.context.begin_turn('Note for Trade #1: ' + words)
            self.assertFalse(self.run_save({**self.args(), 'feeling': 'nervous'})['ok'])
        for words, adjective in (('Felt anything but calm at entry.', 'calm'),
                ('Felt not at all confident at entry.', 'confident'),
                ('Felt confident but not really calm at entry.', 'calm')):
            self.context.begin_turn('Note for Trade #1: ' + words)
            self.assertFalse(self.run_save({**self.args(), 'feeling': adjective, 'stage': 'open'})['ok'])
        self.context.begin_turn(NOTE)
        self.assertFalse(self.run_save()['ok'])  # Mixed stages need one clarification.
        self.assertNotIn('feeling_history', self.metadata())

    def test_explicit_timestamp_is_preserved_through_confirmation(self):
        self.opened()
        words = 'Felt calm during the trade at 2026-10-01T12:34:00Z.'
        self.pending(text='Note for Trade #1: ' + words)
        self.context.begin_turn('Yes')
        saved = self.run_save({**self.args(), 'feeling': words, 'reported_at': '2026-10-01T12:34:00Z'})
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(saved['feeling']['reported_at'], '2026-10-01T12:34:00Z')
        self.assertNotEqual(saved['feeling']['reported_at'], saved['feeling']['recorded_at'])

    def test_timestamped_duplicate_consumes_yes_before_arguments_can_change(self):
        self.opened()
        words = 'Felt calm during the trade at 2026-10-01T12:34:00Z.'
        args = {**self.args(), 'feeling': words, 'reported_at': '2026-10-01T12:34:00Z'}
        self.context.begin_turn('Note for Trade #1: ' + words)
        self.assertTrue(self.run_save(args)['ok'])
        self.pending(text='Note for Trade #1: ' + words)
        self.context.begin_turn('Yes')
        duplicate = self.run_save(args)
        self.assertTrue(duplicate['deduplicated'])
        self.assertFalse(self.run_save({**args, 'reported_at': None})['ok'])
        self.assertEqual(len(self.metadata()['feeling_history']), 1)

    def test_latest_question_receipt_replay_and_negative_questions_fail_closed(self):
        self.opened()
        for question in ('Should I not record this note as a mid-trade feeling for Trade #1?',
                         'Should I preview how to save this note as a mid-trade feeling for Trade #1?',
                         QUESTION + ' I will only preview it.',
                         QUESTION + ' I will not save anything.'):
            self.pending(question=question)
            self.context.begin_turn('Yes')
            self.assertFalse(self.run_save()['ok'])
        self.pending()
        self.context.complete_response('Would you like to review the chart instead?', response_id='later')
        self.context.begin_turn('Yes')
        self.assertFalse(self.run_save()['ok'])
        self.context.begin_turn(NOTE)
        self.context.complete_response(QUESTION, response_id='one')
        self.context.complete_response('Should I save this as a closing feeling for Trade #1?', response_id='one')
        self.context.begin_turn('Yes')
        self.assertFalse(self.run_save({**self.args(), 'stage': 'close'})['ok'])
        self.assertTrue(self.run_save()['ok'])

    def test_new_words_override_prompt_stage_and_reply_time_expires(self):
        self.opened()
        words = 'Felt calm during the trade.'
        self.context.begin_turn('Note for Trade #1: ' + words)
        self.assertFalse(self.run_save({**self.args(), 'stage': 'open', 'feeling': words})['ok'])
        self.pending()
        self.context.begin_turn('Felt calm at entry.')
        self.assertFalse(self.run_save({**self.args(), 'feeling': 'Felt calm at entry.'})['ok'])
        self.pending()
        self.context.begin_turn('Calm during the trade.')
        with patch.object(feelings, '_now', return_value=feelings._now()+timedelta(minutes=20)):
            self.assertFalse(self.run_save({**self.args(), 'feeling': 'Calm during the trade.'})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_browser_advance_then_delegate_preserves_only_completed_ordinary_reply(self):
        self.opened()
        for mode in ('ordinary', 'interrupt', 'partial', 'double_advance', 'closed', 'late_cancel', 'skipped_client_turn', 'wrong_receipt'):
            with self.subTest(mode=mode):
                self.context = MarketConversation((10,20,'browser'), auth_provider=(self.db,10,20))
                self.context.begin_turn(NOTE, client_turn=1)
                self.context.complete_response(QUESTION, completed=mode != 'partial', response_id='question')
                count = len(self.metadata().get('feeling_history', []))
                if mode == 'wrong_receipt':
                    self.context.advance_client_turn(2, continuation=True, response_id='other')
                    self.context.begin_turn('Yes', client_turn=2)
                elif mode == 'skipped_client_turn':
                    self.context.begin_turn('Yes', client_turn=3)
                elif mode == 'late_cancel':
                    self.context.begin_turn('Yes', client_turn=2)
                    self.context.advance_client_turn(2, continuation=True, response_id='question')
                else:
                    self.context.advance_client_turn(2, continuation=mode != 'interrupt', response_id='question')
                    if mode == 'double_advance':
                        self.context.advance_client_turn(3, continuation=True, response_id='question')
                    if mode == 'closed':
                        self.context.close()
                    self.context.begin_turn('Yes', client_turn=3 if mode == 'double_advance' else 2)
                saved = self.run_save()
                self.assertEqual(saved['ok'], mode in ('ordinary', 'late_cancel'), saved)
                self.assertEqual(len(self.metadata().get('feeling_history', [])), count + int(saved['ok']))

    def test_first_person_attribution_is_not_another_persons_emotion(self):
        self.opened()
        for text in ('I feel Bob is nervous.', 'I feel that Bob is nervous.',
                     'I feel the market is calm.', 'I felt like Bob was nervous.'):
            self.context.begin_turn(text)
            phrase = 'calm' if 'calm' in text else 'nervous'
            self.assertFalse(self.run_save({**self.args(), 'feeling': phrase})['ok'])
        for text, phrase in (('I feel nervous about Bob.', 'nervous'),
                ('I feel calm while Bob felt nervous.', 'calm'),
                ('I feel a bit out of sorts.', 'a bit out of sorts')):
            self.context.begin_turn(text)
            saved = self.run_save({**self.args(), 'feeling': phrase})
            self.assertTrue(saved['ok'], saved)
            self.assertEqual(saved['feeling']['feeling'], phrase)
        for text, other_feeling in (('I feel calm while Bob felt nervous.', 'nervous'),
                ('I feel calm and my partner feels anxious.', 'anxious'),
                ('I feel calm and Mary Jane is nervous.', 'nervous'),
                ("I feel calm and my partner's mood is anxious.", 'anxious')):
            self.context.begin_turn(text)
            self.assertFalse(self.run_save({**self.args(), 'feeling': other_feeling})['ok'])
            self.assertTrue(self.run_save({**self.args(), 'feeling': 'calm'})['ok'])

    def test_delegate_first_with_interruption_fence_cannot_save_before_late_cancel(self):
        self.opened()
        self.context = MarketConversation((10,20,'browser'), auth_provider=(self.db,10,20))
        self.context.begin_turn(NOTE, client_turn=1)
        self.context.complete_response(QUESTION, response_id='question')
        node = next(n for n in ast.parse((ROOT/'gbop_voice_web/server.py').read_text()).body
                    if getattr(n, 'name', None) == 'delegate')
        node.decorator_list = []
        async def inline(fn, *args):
            return fn(*args)
        def backend(history, user, context, turn):
            context.begin_turn('Yes', client_turn=turn)
            return self.run_save()
        env = dict(Request=object, DelegateRequest=object, HTTPException=HTTPException,
            require_authenticated_user=AsyncMock(return_value=dict(user_id=20,
                live_session_id='browser', market_context=self.context)),
            asyncio=NS(to_thread=inline), run_backend=backend)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'server.py', 'exec'), env)
        body = NS(session_id='browser', turn_id=2, continuation=False,
                  reply_to_response_id=None, history=[], delegation_id='synthetic')
        result = asyncio.run(env['delegate'](object(), body))
        self.assertFalse(result['result']['ok'])
        self.context.advance_client_turn(2, continuation=False)
        self.assertNotIn('feeling_history', self.metadata())

    def dispatcher(self, path):
        """Run the real text/backend loop with scripted model output, not NLP mocks."""
        name = 'ai_run_turn' if path == 'bot.py' else 'run_backend'
        node = next(n for n in ast.parse((ROOT/path).read_text()).body if getattr(n, 'name', None) == name)
        queue, outputs = [], []
        def create(**kwargs):
            outputs[:] = [json.loads(item['output']) for item in kwargs['input']
                          if isinstance(item, dict) and item.get('type') == 'function_call_output']
            return queue.pop(0)
        def execute(user, tool, args, *rest):
            return coach.coach_tool(self.db, 10, user, tool, args)
        client = NS(responses=NS(create=create))
        env = dict(db=self.db, GTOP_GUILD_ID=10, GBOP_AI_TOOLS=coach.COACH_TOOLS,
            init_ai_db=lambda: None, ai_recent_messages=lambda *a, **k: [], ai_member_context=lambda _: '',
            GTOP_AI_PROMPT=coach.COACH_PROMPT, OPENAI_MODEL='offline-test', ai_client=client,
            ai_execute_tool=execute, json=json, PENDING_JOURNAL_DELETIONS={}, member_context=lambda _: '',
            client=client, BACKEND_MODEL='offline-test', BACKEND_PROMPT=coach.COACH_PROMPT,
            TOOLS=coach.COACH_TOOLS, run_tool=execute)
        exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), env)
        def turn(text, *, save=False, arguments=None):
            if save:
                queue.append(NS(output=[NS(type='function_call', name='record_trade_feeling',
                    arguments=json.dumps(arguments or self.args()), call_id='synthetic')], output_text=''))
            queue.append(NS(output=[], output_text='Saved.' if save else QUESTION))
            with patch('gbop_voice_web.market_conversation.TEXT_MARKET_CONTEXTS.get', return_value=self.context):
                if path == 'bot.py':
                    answer = env[name](20, text, conversation_id='synthetic')
                else:
                    turn_id = self.context.client_turn + 1
                    proof = (getattr(self.context, '_feeling_clarification', None) or {}).get('response_id')
                    self.context.advance_client_turn(turn_id, continuation=bool(proof), response_id=proof)
                    answer = env[name]([{'role': 'user', 'text': text}], 20, self.context, turn_id)
            # This is the existing transport receipt hook, after delivery.
            self.context.complete_response(answer, completed=True)
            return deepcopy(outputs)
        return turn

    def test_stage_qualified_note_keeps_full_changing_feeling_in_both_dispatchers(self):
        for _ in range(2):
            self.opened()
        for path in ('bot.py', 'gbop_voice_web/server.py'):
            with self.subTest(path=path):
                self.context = MarketConversation((10, 20, path), auth_provider=(self.db, 10, 20))
                count = len(self.metadata(2).get('feeling_history', []))
                args = {**self.args(), 'trade_number': 2, 'feeling': QUALIFIED_WORDS}
                outputs = self.dispatcher(path)(QUALIFIED_NOTE, save=True,
                    arguments=args)
                self.assertTrue(outputs[-1]['ok'], outputs)
                self.assertTrue(self.run_save(args)['ok'])  # Same-turn retry is idempotent.
                reports = self.metadata(2)['feeling_history']
                self.assertEqual(len(reports), count + 1)
                self.assertEqual(reports[-1]['feeling'], QUALIFIED_WORDS)
                self.assertEqual(reports[-1]['stage'], 'mid')
                self.assertIsNone(reports[-1]['reported_at'])
                self.assertIsNone(reports[-1]['correction_of'])
                for other in range(1, 2):
                    self.assertNotIn('feeling_history', self.metadata(other))

    def test_stage_qualified_note_requires_full_source_and_exact_target(self):
        for _ in range(2):
            self.opened()
        args = {**self.args(), 'trade_number': 2, 'feeling': QUALIFIED_WORDS}
        for changed in ({'feeling': 'very calm'}, {'feeling': 'a little less calm'},
                {'feeling': QUALIFIED_WORDS.split(', but')[0]}, {'trade_number': 1}, {'stage': 'open'},
                {'reported_at': '2026-01-01T12:00:00Z'}, {'correction_of': 1}):
            with self.subTest(changed=changed):
                self.context.begin_turn(QUALIFIED_NOTE)
                self.assertFalse(self.run_save({**args, **changed})['ok'])
        for number in range(1, 3):
            self.assertNotIn('feeling_history', self.metadata(number))

    def test_stage_qualified_wrappers_ground_each_stage_without_inventing_time(self):
        self.opened()
        words = 'I began to feel a little less confident.'
        for label, stage in (('opening', 'open'), ('entry', 'open'), ('adding', 'add'),
                ('add-entry', 'add'), ('mid-trade', 'mid'), ('closing', 'close')):
            with self.subTest(label=label):
                self.context.begin_turn(f'Save this as an {label} feeling for Trade #1: ' + words)
                result = self.run_save({**self.args(), 'feeling': words, 'stage': stage})
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['feeling']['feeling'], words)
                self.assertEqual(result['feeling']['stage'], stage)
                self.assertIsNone(result['feeling']['reported_at'])

    def test_wrapper_and_body_stage_conflicts_require_clarification(self):
        self.opened()
        words = 'I started to feel uneasy at entry.'
        for stage in ('mid', 'open'):
            self.context.begin_turn('Save this as a mid-trade feeling for Trade #1: ' + words)
            self.assertFalse(self.run_save({**self.args(), 'feeling': words, 'stage': stage})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_wrapper_stage_survives_comma_and_dash_separators_and_prior_prompt(self):
        words = 'I started to feel calm.'
        for separator in (',', '-'):
            for label, stage in (('opening', 'open'), ('adding', 'add'), ('closing', 'close')):
                with self.subTest(separator=separator, label=label):
                    number = self.opened()['trade_id']
                    self.context.begin_turn(f'Save this as a {label} feeling for Trade #{number}{separator} ' + words)
                    args = {**self.args(), 'trade_number': number, 'feeling': words}
                    wrong_stage = 'open' if stage != 'open' else 'close'
                    self.assertFalse(self.run_save({**args, 'stage': wrong_stage})['ok'])
                    result = self.run_save({**args, 'stage': stage})
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(self.metadata(number)['feeling_history'][0]['stage'], stage)

    def test_stage_qualified_fragment_preserves_modifiers(self):
        self.opened()
        words = 'Felt a little uneasy.'
        text = 'Save this as a mid-trade feeling for Trade #1: ' + words
        self.context.begin_turn(text)
        self.assertFalse(self.run_save({**self.args(), 'feeling': 'uneasy'})['ok'])
        result = self.run_save({**self.args(), 'feeling': words})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['feeling']['feeling'], words)
        self.assertEqual(result['feeling']['stage'], 'mid')

    def test_new_feeling_prefix_does_not_admit_other_people_or_nonfactual_reports(self):
        self.opened()
        for words in ('If I started to feel nervous.', 'I could have started to feel nervous.',
                'I started to feel Bob was nervous.', 'I began to feel that my partner was nervous.',
                'I started to feel calm and Bob felt nervous.', 'Did I start to feel nervous?',
                'I started to feel nervous. Do not save this.', 'I started to feel nervous. Preview only.',
                'My coach said "I started to feel nervous."'):
            with self.subTest(words=words):
                self.context.begin_turn('Save this as a mid-trade feeling for Trade #1: ' + words)
                self.assertFalse(self.run_save({**self.args(), 'feeling': words})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_factual_reports_keep_hypothetical_and_person_context(self):
        self.opened()
        for words in (
                'I felt nervous at entry because I thought I might lose.',
                'I felt anxious at entry when my friend joined the trade.',
                'Felt nervous at entry because I thought I might lose.',
                'Felt anxious at entry when my friend joined the trade.'):
            with self.subTest(words=words):
                self.context.begin_turn('Note for Trade #1: ' + words)
                saved = self.run_save({**self.args(), 'feeling': words, 'stage': 'open'})
                self.assertTrue(saved['ok'], saved)
                self.assertEqual(saved['feeling']['feeling'], words)
        for words, phrase in (
                ('I felt nervous at entry because I thought I might lose.', 'nervous'),
                ('I felt anxious when my friend joined the trade.', 'anxious')):
            self.context.begin_turn(words)
            self.assertTrue(self.run_save({**self.args(), 'feeling': phrase, 'stage': 'open'})['ok'])

    def test_context_only_feeling_stage_and_time_cannot_supply_source(self):
        self.opened()
        for text in ('I felt calm because my friend felt nervous.',
                     'I felt calm because I thought I might feel nervous.',
                     'I felt calm about what might make me nervous.'):
            self.context.begin_turn(text)
            self.assertFalse(self.run_save({**self.args(), 'feeling': 'nervous'})['ok'])
        words = 'Felt anxious because my friend joined at entry at 2026-10-01T12:34:00Z.'
        self.context.begin_turn('Note for Trade #1: ' + words)
        self.assertFalse(self.run_save({**self.args(), 'feeling': words, 'stage': 'open'})['ok'])
        self.context.complete_response(QUESTION)
        self.context.begin_turn('Yes')
        self.assertFalse(self.run_save({**self.args(), 'feeling': words,
            'reported_at': '2026-10-01T12:34:00Z'})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_context_does_not_legitimize_hypothetical_or_third_party_head(self):
        self.opened()
        for words in ('If I felt nervous because I might lose.',
                'My friend felt anxious when I joined the trade.',
                'She felt anxious because I was nervous.',
                'I might feel anxious when my friend joins.',
                'I feel when my friend felt calm.',
                'I felt calm because this is a preview only.',
                'I felt calm because I thought about it. Do not save this.'):
            self.context.begin_turn('Note for Trade #1: ' + words)
            self.assertFalse(self.run_save({**self.args(), 'feeling': words, 'stage': 'open'})['ok'])
            self.context.complete_response(QUESTION)
            self.context.begin_turn('Yes')
            self.assertFalse(self.run_save({**self.args(), 'feeling': words})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_direct_negated_report_cannot_save_opposite_adjective(self):
        self.opened()
        for modifier in ('anything but', 'not really', 'not at all', 'not very', 'not even'):
            words = 'I felt ' + modifier + ' calm at entry.'
            self.context.begin_turn(words)
            self.assertFalse(self.run_save({**self.args(), 'feeling': 'calm', 'stage': 'open'})['ok'])
            saved = self.run_save({**self.args(), 'feeling': words, 'stage': 'open'})
            self.assertTrue(saved['ok'], saved)
            self.assertEqual(saved['feeling']['feeling'], words)

    def test_contracted_multisentence_note_keeps_exact_words_through_yes(self):
        self.opened()
        for prefix in ("I'm feeling", 'I’m feeling', "I've been feeling", 'I have been feeling'):
            words = prefix + ' nervous at entry. My confidence started to fade after adding.'
            self.pending(text='Note for Trade #1: ' + words)
            self.context.begin_turn('Yes')
            result = self.run_save({**self.args(), 'feeling': words})
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['feeling']['feeling'], words)
            self.assertIsNone(result['feeling']['reported_at'])

    def test_questions_never_become_notes_or_prompt_replies(self):
        self.opened()
        for question in ('Do I feel calm?', 'Do I feel calm', 'I feel calm?',
                         "I'm feeling calm?", 'Have I been feeling calm?'):
            for prefix in ('', 'Note for Trade #1: '):
                with self.subTest(question=question, prefix=prefix):
                    self.context.begin_turn(prefix + question)
                    self.assertFalse(self.run_save({**self.args(), 'feeling': 'calm'})['ok'])
                    self.assertFalse(self.run_save({**self.args(), 'feeling': question})['ok'])
                    self.context.complete_response(QUESTION)
                    self.context.begin_turn('Yes')
                    self.assertFalse(self.run_save({**self.args(), 'feeling': question})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_contracted_source_is_separate_from_unrelated_questions(self):
        self.opened()
        for message, phrase in (
                ("What is the price? I've been feeling confident.", "I've been feeling confident."),
                ("I'm feeling calm. What is the price?", "I'm feeling calm.")):
            self.context.begin_turn(message)
            result = self.run_save({**self.args(), 'feeling': phrase, 'stage': 'open'})
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['feeling']['feeling'], phrase)

    def test_contracted_reports_do_not_bypass_attribution_or_negation(self):
        self.opened()
        for prefix in ("I'm feeling", "I've been feeling", 'I have been feeling'):
            for text, phrase in ((prefix + ' Bob is nervous.', 'nervous'),
                    ('If ' + prefix + ' calm, should I stop?', 'calm'),
                    (prefix + ' anything but calm at entry.', 'calm'),
                    (prefix + ' calm because my friend felt nervous.', 'nervous')):
                self.context.begin_turn(text)
                self.assertFalse(self.run_save({**self.args(), 'feeling': phrase})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_other_subject_predicates_cannot_borrow_member_feeling(self):
        self.opened()
        for predicate in ('seems', 'seemed', 'looks', 'looked', 'sounds', 'sounded', 'appears', 'became', 'has been', 'had been'):
            for text in ('I feel calm and Bob ' + predicate + ' nervous.',
                         'Note for Trade #1: Felt calm and Bob ' + predicate + ' nervous at entry.'):
                self.context.begin_turn(text)
                self.assertFalse(self.run_save({**self.args(), 'feeling': 'nervous'})['ok'])
                if text.startswith('Note'):
                    self.assertFalse(self.run_save({**self.args(), 'feeling': text.split(': ', 1)[1], 'stage': 'open'})['ok'])
        for text in ("I feel calm and Bob's nervous.", 'I feel calm and Bob’s nervous.',
                     "Note for Trade #1: Felt calm and Bob's nervous at entry."):
            self.context.begin_turn(text)
            self.assertFalse(self.run_save({**self.args(), 'feeling': 'nervous'})['ok'])
        for text in ('I feel Bob looks nervous.', "I'm feeling Bob's nervous.",
                     'I feel Bob seems nervous.', 'I feel Bob has been nervous.'):
            self.context.begin_turn(text)
            self.assertFalse(self.run_save({**self.args(), 'feeling': 'nervous'})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_unlisted_negation_modifiers_are_kept_without_losing_contrast(self):
        self.opened()
        for modifier in ('not especially', 'not entirely', 'not remotely', 'not in the least bit',
                         'nowhere near', 'the opposite of', 'the reverse of', 'far from', 'hardly'):
            text = 'I felt ' + modifier + ' calm at entry.'
            self.context.begin_turn(text)
            self.assertFalse(self.run_save({**self.args(), 'feeling': 'calm', 'stage': 'open'})['ok'])
            saved = self.run_save({**self.args(), 'feeling': text, 'stage': 'open'})
            self.assertTrue(saved['ok'], saved)
        self.context.begin_turn('I felt not anxious but calm at entry.')
        result = self.run_save({**self.args(), 'feeling': 'calm', 'stage': 'open'})
        self.assertTrue(result['ok'], result)

    def test_information_deferral_and_history_questions_do_not_authorize_save(self):
        self.opened()
        for question in (
                'Would you like me to explain how to save this as a mid-trade feeling for Trade #1?',
                'Should I show you how to record this as a mid-trade feeling for Trade #1?',
                'Should I wait to save this as a mid-trade feeling for Trade #1?',
                'Did I save this as a mid-trade feeling for Trade #1?',
                'Should I save this as a mid-trade feeling for Trade #1 later?',
                'Should I save this as a mid-trade feeling for Trade #1 if you approve it later?',
                'Should I save this as a mid-trade feeling for Trade #1 and close the trade?',
                QUESTION + ' I will not actually save anything.',
                QUESTION + ' I won’t actually save anything.'):
            self.pending(question=question)
            self.context.begin_turn('Yes')
            self.assertFalse(self.run_save()['ok'], question)
        self.assertNotIn('feeling_history', self.metadata())
        for prefix in ('Should I', 'Would you like me to', 'Do you want me to', 'Is it okay for me to'):
            self.pending(question=prefix + ' save this as a mid-trade feeling for Trade #1?')
            self.context.begin_turn('Yes')
            self.assertTrue(self.run_save()['ok'])

    def test_clarification_stage_labels_keep_add_entry_distinct(self):
        self.opened()
        for label, stage in (('opening', 'open'), ('entry', 'open'), ('adding', 'add'),
                ('add-entry', 'add'), ('add entry', 'add'), ('add', 'add'),
                ('mid-trade', 'mid'), ('closing', 'close')):
            self.pending(question=f'Should I save this as an {label} feeling for Trade #1?')
            self.context.begin_turn('Yes')
            result = self.run_save({**self.args(), 'stage': stage})
            self.assertTrue(result['ok'], (label, result))
            self.assertEqual(result['feeling']['stage'], stage)
        self.pending(question='Should I add a feeling for Trade #1?')
        self.context.begin_turn('Yes')
        self.assertFalse(self.run_save({**self.args(), 'stage': 'add'})['ok'])

    def test_bounded_opt_out_wording_never_saves_or_stages_a_note(self):
        self.opened()
        for refusal in ('do not actually save this', 'do not really record this',
                'don’t ever save this', 'please avoid saving this', 'refrain from recording this',
                'hold off on saving this', 'wait before saving this', 'wait to save this',
                'I will not actually save this', 'I won’t actually save this', 'I prefer not to save this'):
            words = 'Felt nervous at entry, but ' + refusal + '.'
            self.context.begin_turn('Note for Trade #1: ' + words)
            self.assertFalse(self.run_save({**self.args(), 'feeling': words, 'stage': 'open'})['ok'])
            self.context.complete_response(QUESTION)
            self.context.begin_turn('Yes')
            self.assertFalse(self.run_save({**self.args(), 'feeling': words})['ok'])
        self.assertNotIn('feeling_history', self.metadata())

    def test_actual_discord_and_browser_dispatch_clarification_confirm_and_repeat(self):
        self.opened()
        for path in ('bot.py', 'gbop_voice_web/server.py'):
            with self.subTest(path=path):
                self.context = MarketConversation((10, 20, path), auth_provider=(self.db, 10, 20))
                count = len(self.metadata().get('feeling_history', []))
                turn = self.dispatcher(path)
                turn(NOTE)
                self.assertEqual(len(self.metadata().get('feeling_history', [])), count)
                outputs = turn('Yes', save=True)
                self.assertTrue(outputs[-1]['ok'], outputs)
                self.assertEqual(self.metadata()['feeling_history'][-1]['feeling'], WORDS)
                self.assertEqual(self.metadata()['feeling_history'][-1]['stage'], 'mid')
                self.assertIsNone(self.metadata()['feeling_history'][-1]['reported_at'])
                repeated = turn('Yes', save=True)
                self.assertFalse(repeated[-1]['ok'], repeated)
                self.assertEqual(len(self.metadata()['feeling_history']), count+1)


if __name__ == '__main__':
    unittest.main()
