"""Natural discard consent through real dispatchers; synthetic SQLite only.

Provider responses are scripted at the network boundary. Production parsers,
turn fences, tool dispatch, local playback receipts, and storage run unchanged.
"""
import ast
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from gbop_voice_web import journal_coach as coach, journal_drafts
from gbop_voice_web.journal_discard import _confirm, is_discard_request
from gbop_voice_web.voice_payload import voice_tool_payload
import test_discard_voice_transition as voice_fixture
import test_journal_discard_flow as fixture
import test_journal_story as story_fixture
from test_voice_latency import method


ROOT = Path(__file__).resolve().parents[1]
NATURAL_ASSENT = (
    'Yes',
    'Yes, please remove the unfinished draft.',
    'Sure thing.',
    'Yes — delete it.',
    'Yes—please delete it.',
    'Please go ahead.',
    'Discard unfinished draft.',
    'Absolutely.',
    'Yup.',
    'Sounds good.',
    'Yes, get rid of it.',
    'Go ahead and get rid of it.',
    'I want to delete it.',
    'Yes, I want to delete it.',
    "I'd like to remove that draft.",
    "I'd like you to remove that one.",
    "Let's discard it.",
    'You can delete it.',
    'Yes, you can remove it.',
    'Yep, remove that one.',
    'Please get rid of it.',
    'Please remove my unfinished draft.',
    'Delete that unfinished journal.',
    'Archive that one.',
    'Trash it.',
    'Throw away that unfinished draft.',
    'Scrap that draft.',
    'I want that one removed.',
    'Yes, delete it for me.',
    'Delete it, thanks.',
    'Yes, discard it. Thank you.',
    'Yes, please go ahead and delete it, thanks.',
    'Yes. Go ahead. Delete it.',
)
UNSAFE_OR_UNRELATED = (
    'No', 'No thanks', 'Cancel', 'Keep it', 'Wait', 'Not yet',
    "Yes, don't delete it.", 'Yes, but do not remove it.',
    'Yes, never mind.', 'Yes, keep it instead.',
    'I do not confirm.', "I don't want to delete it.",
    'Should I delete it?', 'Go ahead?',
    'Maybe delete it.', 'If I say yes, delete it.',
    'Unless it is saved, delete it.', 'Pretend I said yes.',
    'For example, yes, delete it.', 'Hypothetically, delete it.',
    'She said yes.', 'He wants to delete it.',
    'My friend said to delete it.', 'Tell her to delete it.',
    '"Yes, delete it."', '“Yes, delete it.”', "'Yes, delete it.'",
    'I said "yes" as an example.',
    'Yes, delete the trade.', 'Yes, delete the saved journal.',
    'Yes, delete all drafts.', 'Discard every draft.', 'Remove both drafts.',
    'Delete the other draft.', 'Delete that one and the other one.',
    'Remove this and delete that.', 'Delete the current draft.',
    'Yes to something else.', 'Yes, archive it and delete the trade.',
    'Delete draft deadbeef.', 'Remove draft 00000000000000000000000000000000.',
)


class NaturalDiscardGrammarTests(unittest.TestCase):
    def test_clear_natural_assent_without_a_magic_phrase_or_spoken_id(self):
        for text in NATURAL_ASSENT:
            with self.subTest(text=text):
                self.assertTrue(_confirm(text), text)

    def test_negation_quotation_hypotheticals_and_different_targets_are_rejected(self):
        for text in UNSAFE_OR_UNRELATED:
            with self.subTest(text=text):
                self.assertFalse(_confirm(text), text)


class NaturalDiscardDispatchTests(unittest.TestCase):
    setUp = fixture.JournalDiscardFlowTests.setUp
    tearDown = fixture.JournalDiscardFlowTests.tearDown
    review = fixture.JournalDiscardFlowTests.review
    handlers = fixture.JournalDiscardFlowTests.handlers
    call = fixture.JournalDiscardFlowTests.call
    stage = fixture.JournalDiscardFlowTests.stage
    saved = fixture.JournalDiscardFlowTests.saved
    fresh = fixture.JournalDiscardFlowTests.fresh
    stored = fixture.JournalDiscardFlowTests.stored
    preview = fixture.JournalDiscardFlowTests.preview
    discard = fixture.JournalDiscardFlowTests.discard

    def new_draft(self):
        draft = self.call('stage_journal_story', {'new_draft': True,
            'story_json': json.dumps({**story_fixture.STORY, 'title': 'Synthetic practice'})},
            text='Start a new journal draft.')
        self.assertTrue(draft['ok'], draft)
        return draft

    def dispatcher(self, path):
        """Actual typed or browser-backend loop with scripted model tool calls."""
        name = 'ai_run_turn' if path == 'bot.py' else 'run_backend'
        node = next(n for n in ast.parse((ROOT / path).read_text()).body
                    if getattr(n, 'name', None) == name)
        pending, outputs = [], []

        def create(**kwargs):
            outputs[:] = [json.loads(item['output']) for item in kwargs['input']
                          if isinstance(item, dict) and item.get('type') == 'function_call_output']
            if pending:
                tool, args = pending.pop(0)
                return NS(output=[NS(type='function_call', name=tool,
                    arguments=json.dumps(args), call_id='synthetic-discard')], output_text='')
            result = outputs[-1]
            answer = result.get('confirmation_prompt') or (
                'The draft was discarded and can be restored.' if result.get('discarded')
                else result.get('error', 'The draft was left unchanged.'))
            return NS(output=[], output_text=answer)

        def execute(user, tool, args, *rest):
            return coach.coach_tool(self.db, 10, user, tool, args)

        client = NS(responses=NS(create=create))
        env = dict(db=self.db, GTOP_GUILD_ID=10, GBOP_AI_TOOLS=coach.COACH_TOOLS,
            init_ai_db=lambda: None, ai_recent_messages=lambda *a, **k: [],
            ai_member_context=lambda _: '', GTOP_AI_PROMPT=coach.COACH_PROMPT,
            OPENAI_MODEL='offline-test', ai_client=client, ai_execute_tool=execute,
            json=json, PENDING_JOURNAL_DELETIONS={}, member_context=lambda _: '',
            client=client, BACKEND_MODEL='offline-test', BACKEND_PROMPT=coach.COACH_PROMPT,
            TOOLS=coach.COACH_TOOLS, run_tool=execute)
        exec(compile(ast.Module(body=[node], type_ignores=[]), path, 'exec'), env)

        def turn(text, tool, args):
            pending.append((tool, args))
            with patch('gbop_voice_web.market_conversation.TEXT_MARKET_CONTEXTS.get',
                       return_value=self.context):
                if path == 'bot.py':
                    answer = env[name](20, text, conversation_id='synthetic')
                else:
                    turn_id = self.context.client_turn + 1
                    proof = (getattr(self.context, '_journal_discard_preview', None) or {}).get('response_id')
                    self.context.advance_client_turn(turn_id, continuation=bool(proof), response_id=proof)
                    answer = env[name]([{'role': 'user', 'text': text}], 20, self.context, turn_id)
            # The separate transport hook is invoked only after accepted delivery.
            self.context.complete_response(answer, response_id=f'delivered-{self.context.generation}')
            return deepcopy(outputs[-1])
        return turn

    def archived(self, draft_id):
        with self.db() as conn:
            return journal_drafts.read(conn, 10, 20, draft_id, include_discarded=True)[0]

    def test_natural_assent_survives_actual_text_and_browser_dispatchers(self):
        for path in ('bot.py', 'gbop_voice_web/server.py'):
            turn = self.dispatcher(path)
            for text in NATURAL_ASSENT:
                with self.subTest(path=path, text=text):
                    self.context = self.fresh()
                    draft = self.new_draft()
                    before = self.stored(draft['draft_id'])
                    preview = turn('Discard this unfinished draft.', 'prepare_journal_discard',
                                   {'draft_id': draft['draft_id']})
                    self.assertIn('confirmation_prompt', preview)
                    self.assertTrue(self.context._journal_discard_preview['delivered'])
                    result = turn(text, 'discard_journal_story', {'draft_id': draft['draft_id'],
                        'confirmation_text': None})
                    self.assertTrue(result.get('discarded'), result)
                    archived = self.archived(draft['draft_id'])
                    self.assertEqual(archived['draft_status'], 'discarded')
                    self.assertEqual(archived['raw_story'], before['raw_story'])
                    self.assertEqual(archived['values'], before['values'])
                    for table in ('journals', 'theses', 'thesis_executions'):
                        self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_typed_cancellation_cannot_be_replaced_by_model_assent(self):
        for path in ('bot.py', 'gbop_voice_web/server.py'):
            turn = self.dispatcher(path)
            for text in ('No, keep it.', 'If I say yes, delete it.', 'She said yes.',
                         'Yes, delete the other draft.'):
                with self.subTest(path=path, text=text):
                    self.context = self.fresh()
                    draft = self.new_draft()
                    turn('Discard this unfinished draft.', 'prepare_journal_discard',
                         {'draft_id': draft['draft_id']})
                    result = turn(text, 'discard_journal_story', {'draft_id': draft['draft_id'],
                        'confirmation_text': 'Yes, get rid of it.'})
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(self.stored(draft['draft_id'])['draft_status'], 'unfinished')

    def test_natural_assent_cannot_replace_preview_freshness_or_exact_target(self):
        for mode in ('undelivered', 'same_turn', 'expired', 'intervening', 'reconnect',
                     'wrong_draft', 'changed_revision'):
            with self.subTest(mode=mode):
                self.context = self.fresh()
                draft = self.new_draft()
                self.preview(draft, deliver=mode != 'undelivered')
                target = draft['draft_id']
                if mode == 'expired':
                    self.context._journal_discard_preview['expires_at'] = 0
                elif mode == 'intervening':
                    self.context.begin_turn('What is the weather?')
                elif mode == 'reconnect':
                    self.context = self.fresh()
                elif mode == 'wrong_draft':
                    other = self.fresh()
                    second = self.call('stage_journal_story', {'new_draft': True,
                        'story_json': '{"title":"Another synthetic draft"}'},
                        text='Start a new draft.', context=other)
                    target = second['draft_id']
                elif mode == 'changed_revision':
                    other = self.fresh()
                    self.call('get_journal_story', {'draft_id': target}, 'Read it.', context=other)
                    self.call('stage_journal_story', {'draft_id': target,
                        'story_json': '{"context_notes":"Synthetic newer detail"}'},
                        'Synthetic newer detail.', context=other)
                if mode != 'same_turn':
                    self.context.begin_turn('Yes, get rid of it.')
                result = self.context.run('discard_journal_story', {'draft_id': target,
                    'confirmation_text': 'Yes, get rid of it.'},
                    lambda name, args: coach.coach_tool(self.db, 10, 20, name, args))
                self.assertFalse(result['ok'], result)
                self.assertEqual(self.stored(draft['draft_id'])['draft_status'], 'unfinished')

    def test_same_turn_replacement_cannot_reuse_an_identical_title_receipt(self):
        first=self.stage()
        second=self.call('stage_journal_story',{'new_draft':True,'story_json':json.dumps({'title':self.stored(first['draft_id'])['values']['title'],'context_notes':'Other synthetic story'})},'Start a new draft.')
        preview=self.preview(first,deliver=False)
        pending=deepcopy(self.context._journal_discard_preview)
        replacement=self.context.run('prepare_journal_discard',{'draft_id':second['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertFalse(replacement['ok'],replacement)
        self.assertEqual(self.context._journal_discard_preview,pending)
        self.context.complete_response(preview['confirmation_prompt'],response_id='first-only')
        self.assertFalse(self.discard(second,text='Sure thing.')['ok'])
        self.assertEqual(self.stored(second['draft_id'])['draft_status'],'unfinished')

    def test_same_turn_revision_replacement_requires_a_new_turn(self):
        first=self.stage();preview=self.preview(first,deliver=False)
        pending=deepcopy(self.context._journal_discard_preview)
        other=self.fresh()
        self.call('get_journal_story',{'draft_id':first['draft_id']},'Read it.',context=other)
        self.call('stage_journal_story',{'draft_id':first['draft_id'],'story_json':'{"context_notes":"New fact after preview"}'},'New fact after preview.',context=other)
        replacement=self.context.run('prepare_journal_discard',{'draft_id':first['draft_id']},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
        self.assertFalse(replacement['ok'],replacement)
        self.assertEqual(self.context._journal_discard_preview,pending)
        self.context.complete_response(preview['confirmation_prompt'],response_id='old-facts')
        self.assertFalse(self.discard(first,text='Absolutely.')['ok'])
        self.assertEqual(self.stored(first['draft_id'])['draft_status'],'unfinished')

    def test_preview_identifies_title_without_requiring_a_spoken_internal_id(self):
        draft = self.new_draft()
        preview = self.preview(draft)
        self.assertEqual(preview['draft']['draft_id'], draft['draft_id'])
        self.assertIn('Synthetic practice', preview['confirmation_prompt'])
        self.assertNotIn(draft['draft_id'], preview['confirmation_prompt'])
        self.assertNotIn(draft['draft_id'][-8:], preview['confirmation_prompt'])
        self.assertIn('restor', preview['confirmation_prompt'].casefold())
        result = self.discard(draft, text='Sure thing, get rid of it, thanks.')
        self.assertTrue(result.get('discarded'), result)

    def test_natural_action_and_passive_requests_cannot_be_staged_as_narration(self):
        for text in ('Get rid of that one.', 'Please throw away my unfinished draft.',
                     'Scrap that draft.', 'Trash it.', 'I want that one removed.',
                     "I'd like my unfinished journal archived."):
            for voice in (False, True):
                with self.subTest(text=text, voice=voice):
                    self.context = self.fresh()
                    draft = self.new_draft()
                    before = self.stored(draft['draft_id'])
                    result = self.call('stage_journal_story', {'draft_id': draft['draft_id'],
                        'story_json': '{"context_notes":"Must not overwrite narration"}',
                        'raw_story': text if voice else None}, text=None if voice else text)
                    self.assertFalse(result['ok'], result)
                    self.assertIn('Do not record', result['error'])
                    self.assertEqual(self.stored(draft['draft_id']), before)

    def test_receipt_bound_assent_is_not_narration_and_can_still_discard_same_turn(self):
        for text in ('Sure thing.', 'Sounds good.', 'Absolutely.'):
            for voice in (False, True):
                with self.subTest(text=text, voice=voice):
                    self.context = self.fresh()
                    draft = self.new_draft()
                    before = self.stored(draft['draft_id'])
                    self.preview(draft)
                    self.context.begin_turn(None if voice else text)
                    result = self.context.run('stage_journal_story', {'draft_id': draft['draft_id'],
                        'story_json': '{}', 'raw_story': text if voice else None},
                        lambda name, args: coach.coach_tool(self.db, 10, 20, name, args))
                    self.assertFalse(result['ok'], result)
                    self.assertIn('Do not record', result['error'])
                    self.assertEqual(self.stored(draft['draft_id']), before)
                    result = self.context.run('discard_journal_story', {'draft_id': draft['draft_id'],
                        'confirmation_text': text if voice else None},
                        lambda name, args: coach.coach_tool(self.db, 10, 20, name, args))
                    self.assertTrue(result.get('discarded'), result)

    def test_pending_cancellation_is_not_recorded_as_narration(self):
        for text in ('No, keep it.', 'Cancel.', 'No thanks.', 'Leave it alone.', 'No, thanks, keep it.', 'Wait, not yet.'):
            for voice in (False,True):
                with self.subTest(text=text,voice=voice):
                    self.tearDown();self.setUp()
                    first=self.stage();self.preview(first)
                    before=deepcopy(self.stored(first['draft_id']))
                    self.context.begin_turn(None if voice else text)
                    result=self.context.run('stage_journal_story',{'draft_id':first['draft_id'],'raw_story':text,'story_json':'{}'},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                    self.assertFalse(result['ok'],result)
                    self.assertEqual(self.stored(first['draft_id']),before)

    def test_voice_negative_cannot_be_replaced_by_same_turn_tool_yes(self):
        for first_tool in ('discard_journal_story','stage_journal_story'):
            for text in ('No, keep it.','Do not delete it.','Please do not discard that draft.',
                         'Yes, but do not delete it.','Hypothetically, delete the draft.',
                         '"Yes, delete the draft."'):
                with self.subTest(first_tool=first_tool,text=text):
                    self.tearDown();self.setUp()
                    first=self.stage();self.preview(first);before=deepcopy(self.stored(first['draft_id']))
                    self.context.begin_turn(None)
                    args={'draft_id':first['draft_id'],'confirmation_text':text}
                    if first_tool=='stage_journal_story':args.update(raw_story=text,story_json='{}')
                    result=self.context.run(first_tool,args,lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                    self.assertFalse(result['ok'],result)
                    retry=self.context.run('discard_journal_story',{'draft_id':first['draft_id'],'confirmation_text':'Yes'},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                    self.assertFalse(retry['ok'],retry)
                    staged=self.context.run('stage_journal_story',{'draft_id':first['draft_id'],'raw_story':text,'story_json':'{}'},lambda n,a:coach.coach_tool(self.db,10,20,n,a))
                    self.assertFalse(staged['ok'],staged)
                    self.assertEqual(self.stored(first['draft_id']),before)

    def test_normal_journal_correction_and_finalization_are_not_discard_commands(self):
        draft = self.new_draft()
        for text in ('Sure thing.', 'Sounds good.', 'Absolutely.'):
            self.assertFalse(is_discard_request(text, self.context), text)
        result = self.call('stage_journal_story', {'draft_id': draft['draft_id'],
            'story_json': '{"clear_fields":["entries.1.risk_r"]}'},
            'Remove the R from entry 1 of this journal.')
        self.assertTrue(result['ok'], result)
        self.assertNotIn('risk_r', self.stored(draft['draft_id'])['values']['entries'][0])
        self.assertEqual(self.stored(draft['draft_id'])['draft_status'], 'unfinished')
        result = self.saved(draft)
        self.assertTrue(result.get('saved'), result)
        self.assertEqual(self.stored(draft['draft_id'])['draft_status'], 'finalized')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM journals').fetchone()[0], 1)


class NaturalDiscardVoiceTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the existing fixture's real Discord receiver/playback implementation
    # without inheriting and re-running all of its test cases.
    events = voice_fixture.DiscardVoiceTransitionTests.events
    play_preview = voice_fixture.DiscardVoiceTransitionTests.play_preview
    speech = voice_fixture.DiscardVoiceTransitionTests.speech
    asyncTearDown = voice_fixture.DiscardVoiceTransitionTests.asyncTearDown

    def setUp(self):
        voice_fixture.DiscardVoiceTransitionTests.setUp(self)
        self.session._market_base_instructions = coach.COACH_PROMPT
        self.session.authorize_tool = AsyncMock(return_value=None)

        async def inline(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        def execute(user, tool, args):
            return coach.coach_tool(self.fixture.db, 10, user, tool, args)

        invoke = method('execute_tool', dict(asyncio=NS(to_thread=inline), json=json,
            time=time, ai_execute_tool=execute, voice_tool_payload=voice_tool_payload,
            GBOP_REALTIME_MAX_OUTPUT_TOKENS=700))
        self.session.execute_tool = MethodType(invoke, self.session)

    async def provider_toolcall(self, text, *, call_id='natural-discard'):
        await self.events(
            {'type': 'response.created', 'response': {'id': call_id + '-response'}},
            {'type': 'response.output_item.done', 'response_id': call_id + '-response',
             'item': {'type': 'function_call', 'name': 'discard_journal_story',
                      'call_id': call_id, 'arguments': json.dumps({
                          'draft_id': self.draft['draft_id'], 'confirmation_text': text})}})
        await asyncio.wait_for(asyncio.gather(*list(self.work.tasks)), timeout=3)
        outputs = [json.loads(call.args[0]['item']['output'])
                   for call in self.session.send_event.await_args_list
                   if call.args[0].get('type') == 'conversation.item.create'
                   and call.args[0].get('item', {}).get('call_id') == call_id]
        self.assertEqual(len(outputs), 1, self.session.send_event.await_args_list)
        return outputs[0]

    async def test_actual_receiver_accepts_natural_reply_after_playback_and_speech(self):
        await self.play_preview()
        await self.speech()
        self.assertIsNone(self.context._client_text)
        result = await self.provider_toolcall('Yeah, go ahead and get rid of it, thanks.')
        self.assertTrue(result.get('discarded'), result)
        self.assertTrue(result.get('restorable'), result)
        with self.fixture.db() as conn:
            row = journal_drafts.read(conn, 10, 20, self.draft['draft_id'], include_discarded=True)[0]
        self.assertEqual(row['draft_status'], 'discarded')
        for table in ('journals', 'theses', 'thesis_executions'):
            self.assertEqual(self.fixture.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    async def test_actual_receiver_rejects_negative_reply(self):
        await self.play_preview()
        await self.speech()
        result = await self.provider_toolcall('Yes, but do not delete it.')
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.fixture.stored(self.draft['draft_id'])['draft_status'], 'unfinished')

    async def test_actual_receiver_still_requires_completed_playback(self):
        await self.play_preview(drain=False)
        await self.speech()
        result = await self.provider_toolcall('Yes, get rid of it.')
        self.assertFalse(result['ok'], result)
        self.assertEqual(self.fixture.stored(self.draft['draft_id'])['draft_status'], 'unfinished')


if __name__ == '__main__':
    unittest.main()
