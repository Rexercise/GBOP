"""Twenty synthetic lifecycle scenarios through real journal/transport handlers.

SQLite is isolated per scenario; network connections are prohibited. Supplied
model arguments/transcriptions and market rows are fixtures, not tests of a model
or image recognition. No Discord delivery or real member records are involved.
"""
from copy import deepcopy
import json
import socket
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_coach as coach, journal_drafts, journal_recall as recall
from gbop_voice_web import trade_photos as photos
from gbop_voice_web.journal_presentation import journal_tool_payload
from gbop_voice_web.market_conversation import MarketConversation
import test_contextual_journals as fixture
import test_journal_direction_context as direction_fixture


class JournalLifecycleAcceptanceTests(unittest.TestCase):
    review = fixture.ContextualJournalTests.review
    handlers = fixture.ContextualJournalTests.handlers
    verified_setup = direction_fixture.JournalDirectionContextTests.verified_setup

    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, 'connect', side_effect=AssertionError('Acceptance suite forbids network')).start()
        fixture.ContextualJournalTests.setUp(self)
        self.addCleanup(self.conn.close)
        self.context = self.fresh()
        self.envs = {path: self.handlers(path) for path in ('bot.py', 'gbop_voice_web/server.py')}

    def fresh(self, user=20, surface='discord_text'):
        return MarketConversation((10, user, surface), auth_provider=(self.db, 10, user))

    def action(self, name, args, text='Journal my reported trade.', *, context=None,
               user=20, path='bot.py', operation_id=None):
        context = context or self.context
        context.begin_turn(text)
        def runner(n, a):
            if n == 'get_journal_history':
                return recall.history(self.db, 10, user, a)
            if n in coach.COACH_NAMES:
                return coach.coach_tool(self.db, 10, user, n, a)
            if n == 'annotate_trade_photo':
                return photos.annotate(self.db, 10, user, a)
            return self.envs[path][('ai_' if path == 'bot.py' else 'tool_') + n](user, a)
        return context.run(name, args, runner, operation_id=operation_id)

    def stage(self, values=None, text='Journal my reported trade.', **kwargs):
        options = {key: kwargs.pop(key) for key in ('context', 'user') if key in kwargs}
        return self.action('stage_journal_story', {'story_json': json.dumps(values or {}), **kwargs}, text, **options)

    def save(self, draft, **kwargs):
        result = self.action('save_journal_story', {'draft_id': draft['draft_id']}, 'Save this journal.', **kwargs)
        self.assertTrue(result['ok'], result)
        return result

    def stored(self, draft, user=20):
        with self.db() as conn:
            return journal_drafts.read(conn, 10, user, draft['draft_id'])[0]

    def history(self, number=1, **kwargs):
        result = self.action('get_journal_history', {'trade_number': number}, 'Read my journal.', **kwargs)
        self.assertTrue(result['ok'], result)
        return result

    def count(self, table):
        return self.conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]

    def opened(self, **kwargs):
        result = self.action('open_trade', dict(asset='NAS100', direction='Bearish', play='Young Lefty',
            entry_model='Soup', risk_r=.1, tier=1, market_reference='none'), 'Record my short trade.', **kwargs)
        self.assertTrue(result['ok'], result)
        return result['trade_id']

    def closed(self, number, **kwargs):
        result = self.action('close_trade', dict(trade_id=number, final_result_r=None,
            summary='I exited the whole position; R unknown.', rule_adherence='yes', study_note='Kept my plan.',
            market_reference='none'), 'I closed all of Trade #' + str(number) + '.', **kwargs)
        self.assertTrue(result['ok'], result)
        return result

    def test_01_partial_narrative_is_durable_before_extraction(self):
        text = 'Journal my NAS trade. I entered short but have not finished the story.'
        self.context.begin_turn(text)
        self.assertEqual(self.count('journal_story_drafts'), 1)
        self.context.close()
        result = self.action('get_journal_story', {}, 'Continue my journal.', context=self.fresh())
        self.assertTrue(result['persisted'], result)
        self.assertIn(text, result['raw_story_text'])
        self.assertEqual(result['story']['direction'], 'Bearish')
        self.assertEqual((self.count('journals'), self.count('thesis_executions')), (0, 0))

    def test_02_explicit_direction_needs_no_direction_question(self):
        for word in ('bullish', 'bearish'):
            with self.subTest(direction=word):
                result = self.stage(text=f'Journal my NAS trade. It was a {word} setup. This is a separate trade.', new_draft=True)
                self.assertEqual(result['story']['direction'], word.title())
                self.assertNotIn('bullish (long)', result['next_question'] or '')

    def test_03_inference_requires_matching_verified_scope(self):
        for word in ('bullish', 'bearish'):
            self.context = self.fresh()
            self.verified_setup(word)
            draft = self.stage(text='Journal my NAS Young Lefty trade from this reviewed setup. This is a separate trade.', new_draft=True)
            self.assertEqual(draft['story']['direction'], word.title())
            self.assertEqual(draft['provenance']['direction']['source'], 'verified_setup')
        unrelated = self.stage({'asset': 'EURUSD', 'trade_date': '2040-04-12'},
            'Journal my EURUSD Young Lefty trade. This is a separate trade.', new_draft=True)
        self.assertNotIn('direction', unrelated['story'])

    def test_04_ambiguity_asks_once_and_keeps_the_rest(self):
        draft = self.stage({'asset': 'NAS100', 'play': '9ate8', 'context_notes': 'Entry price unreadable.',
            'entries': [{'entry_index': 1, 'notes': 'Waited for my signal.'}]})
        self.assertEqual(draft['next_question'], 'Was your position bullish (long) or bearish (short)?')
        self.context.complete_response(draft['next_question'], completed=True)
        next_turn = self.stage({}, draft_id=draft['draft_id'])
        self.assertNotEqual(next_turn['next_question'], draft['next_question'])
        self.assertEqual(next_turn['story']['entries'], draft['story']['entries'])
        answer = self.stage({}, 'Bearish.', draft_id=draft['draft_id'])
        self.assertEqual(answer['story']['context_notes'], 'Entry price unreadable.')
        self.assertEqual(answer['story']['direction'], 'Bearish')

    def test_05_correction_fences_delayed_draft_writer(self):
        first = self.stage(text='Journal my NAS trade. I entered long.')
        old = self.fresh(surface='discord_voice')
        self.action('get_journal_story', {'draft_id': first['draft_id']}, 'Read my draft.', context=old)
        corrected = self.stage({}, 'Correction: I was short, not long.', draft_id=first['draft_id'])
        delayed = self.stage({'direction': 'Bullish'}, 'Update the draft.', draft_id=first['draft_id'], context=old)
        self.assertFalse(delayed['ok'], delayed)
        later = self.stage({'direction': 'Bullish', 'entries': [{'entry_index': 2, 'entry_model': 'Soup'}]},
            draft_id=first['draft_id'])
        self.assertEqual(later['story']['direction'], 'Bearish')
        self.assertEqual(self.stored(first)['values']['direction'], 'Bearish')
        self.assertTrue(self.stored(corrected)['corrections'])

    def test_06_saved_correction_changes_only_selected_record(self):
        first = self.stage({'session': 'night', 'time_zone': 'America/New_York'},
            text='Journal my NAS trade. I entered long.')
        self.save(first)
        # A saved summary from before session/timezone were rendered must also
        # receive the correction, preserving its original narration suffix.
        description = self.conn.execute('SELECT description FROM journals').fetchone()[0]
        legacy = '\n'.join(line for line in description.split('\n')
                           if not line.startswith(('Reported session:', 'Reported timezone:')))
        self.conn.execute('UPDATE journals SET description=?', (legacy,))
        second = self.stage({'asset': 'EURUSD', 'direction': 'Bullish'},
            'Journal a separate trade on EURUSD.', new_draft=True, new_trade=True)
        self.save(second)
        before = self.history(2)['journals'][0]
        edited = self.action('save_journal_entry', {'trade_number': 1, 'metadata_json': '{"direction":"Bearish"}'},
            'Correct Trade #1: I was short, not long.')
        self.assertTrue(edited['ok'], edited)
        corrected = self.history(1)['journals'][0]
        self.assertEqual(corrected['trade_facts']['direction'], 'Bearish')
        self.assertIn('NAS100 · Bearish', corrected['summary'])
        self.assertIn('Reported timezone: America/New_York', corrected['summary'])
        self.assertIn('I entered long.', corrected['summary'])  # Original narration remains.
        self.assertEqual(self.history(2)['journals'][0], before)
        resumed = self.action('get_journal_story', {'draft_id': first['draft_id']}, 'Read this draft.', context=self.fresh())
        self.assertEqual(resumed['story']['direction'], 'Bearish')

    def test_07_close_without_r_is_closed_not_zero(self):
        for path in self.envs:
            with self.subTest(transport=path):
                number = self.opened(path=path)
                result = self.closed(number, path=path)
                self.assertIsNone(result['final_result_r'])
                row = self.history(number)['journals'][0]
                self.assertEqual(row['trade_facts']['status'], 'CLOSED')
                self.assertIsNone(row['result_r'])
        performance = coach.performance(self.db, 10, 20, {})
        self.assertEqual(performance['summary']['known_outcomes'], 0)
        self.assertEqual(performance['summary']['missing_outcomes'], 2)

    def test_08_later_r_updates_one_canonical_record(self):
        number = self.opened()
        self.closed(number)
        identity = tuple(self.conn.execute('SELECT id,thesis_id FROM journals').fetchone())
        result = self.action('save_journal_entry', {'trade_number': number, 'result_r': 2.5}, 'My final result was 2.5R.')
        self.assertTrue(result['ok'], result)
        self.assertEqual(tuple(self.conn.execute('SELECT id,thesis_id FROM journals').fetchone()), identity)
        self.assertEqual((self.count('journals'), self.count('theses'), self.count('thesis_executions')), (1, 1, 1))
        row = self.history(number)['journals'][0]
        self.assertEqual(row['result_r'], 2.5)
        self.assertEqual(row['trade_facts']['status'], 'CLOSED')

    def test_09_commit_then_lost_reply_reconciles_once(self):
        draft = self.stage(text='Journal my NAS trade. I entered short.')
        self.context.begin_turn('Save this journal.')
        calls = []
        def interrupted(n, a):
            calls.append(n)
            with patch('gbop_voice_web.voice_runtime.journal_write_committed'):
                result = coach.coach_tool(self.db, 10, 20, n, a)
            self.assertTrue(result['ok'], result)
            raise RuntimeError('Synthetic lost reply after commit')
        args = {'draft_id': draft['draft_id']}
        saved = self.context.run('save_journal_story', args, interrupted)
        self.assertTrue(saved['reconciliation_verified'], saved)
        self.assertEqual(self.context.run('save_journal_story', args, interrupted), saved)
        self.context.close()
        again = self.save(draft, context=self.fresh())
        self.assertEqual(again['trade_number'], saved['trade_number'])
        self.assertEqual((len(calls), self.count('journals'), self.count('theses')), (1, 1, 1))

    def test_10_distinct_identical_fills_are_not_retries(self):
        for path in self.envs:
            number = self.opened(path=path)
            args = dict(trade_id=number, entry_model='Soup', tier=1, risk_r=.1, market_reference='none')
            results = [self.action('add_entry', args, 'I added another identical fill.', path=path,
                operation_id=path + str(i)) for i in (1, 2)]
            self.assertTrue(all(r['ok'] for r in results), results)
            self.assertNotEqual(results[0]['execution_id'], results[1]['execution_id'])
            self.assertEqual(results[1]['persisted_entry_count'], 3)
            replay = self.action('add_entry', args, 'Check the saved fill.', path=path, operation_id=path + '2')
            self.assertEqual(replay['execution_id'], results[1]['execution_id'])
        self.assertEqual(self.count('thesis_executions'), 6)

    def test_11_partial_exit_keeps_runner_open_then_closes(self):
        number = self.opened()
        partial = self.action('record_trade_event', dict(trade_id=number, event='Partial exit',
            details='Closed half; remaining half is an open runner.', result_r=None, market_reference='none'),
            'I closed half and left the runner open.')
        self.assertTrue(partial['ok'], partial)
        draft = self.stage({'asset': 'NAS100', 'direction': 'Bearish', 'reported_outcome': 'open',
            'entries': [{'entry_index': 1, 'status': 'partially_closed', 'notes': 'Half remains open.'}]},
            trade_number=number)
        self.save(draft)
        row = self.history(number)['journals'][0]
        self.assertEqual(row['trade_facts']['status'], 'OPEN')
        self.assertIsNone(row['result_r'])
        self.assertIn('open runner', json.dumps(row))
        self.closed(number)
        self.assertEqual(self.history(number)['journals'][0]['trade_facts']['status'], 'CLOSED')
        self.assertEqual(self.count('journals'), 1)

    def test_12_notes_feelings_and_images_stay_on_intended_thesis(self):
        first, second = self.opened(), self.opened()
        image = photos.save_upload(self.db, 10, 20, 'synthetic-source', b'\x89PNG\r\n\x1a\nfixture')['photo_id']
        saved = self.action('save_journal_entry', {'trade_number': first, 'photo_id': image,
            'study_note': 'Waited for the planned entry.'}, 'Add this note and picture to Trade #1.')
        self.assertTrue(saved['ok'], saved)
        feeling = self.action('record_trade_feeling', {'trade_number': first, 'stage': 'open',
            'feeling': 'calm', 'reported_at': None, 'correction_of': None, 'skip': False}, 'On Trade #1 I felt calm at entry.')
        self.assertTrue(feeling['ok'], feeling)
        one, two = self.history(first)['journals'][0], self.history(second)['journals'][0]
        self.assertEqual(one['study_note'], 'Waited for the planned entry.')
        self.assertEqual(one['metadata']['feeling_history'][0]['feeling'], 'calm')
        self.assertIn(image, json.dumps(one))
        self.assertNotIn(image, json.dumps(two))
        self.assertNotIn('feeling_history', two['metadata'])
        wrong = self.action('save_journal_entry', {'trade_number': second, 'photo_id': image}, 'Attach this source to Trade #2.')
        self.assertFalse(wrong['ok'], wrong)

    def test_13_unreadable_image_fields_remain_unknown(self):
        image = photos.save_upload(self.db, 10, 20, 'unclear-source', b'\x89PNG\r\n\x1a\nfixture')['photo_id']
        result = self.action('save_journal_entry', {'photo_id': image, 'description': 'NAS journal; other writing [unclear].',
            'result_r': None, 'metadata_json': json.dumps({'kind': 'trade', 'asset': 'NAS100',
                'transcription': 'NAS; entry [unclear]; R [unclear]', 'uncertainties': 'Direction and amounts unreadable.'})})
        self.assertTrue(result['ok'], result)
        row = self.history()['journals'][0]
        self.assertIsNone(row['result_r'])
        self.assertEqual(row['trade_facts']['direction'], 'Not specified')
        self.assertNotIn('entry_price', row['metadata'])
        self.assertIn('[unclear]', json.dumps(row))
        self.assertEqual(self.count('thesis_executions'), 0)

    def test_14_text_to_voice_reconnect_keeps_owned_context(self):
        draft = self.stage(text='Journal my NAS trade. I entered short.')
        voice = self.fresh(surface='discord_voice')
        resumed = self.action('get_journal_story', {}, None, context=voice)
        self.assertEqual(resumed['draft_id'], draft['draft_id'])
        corrected = self.stage({}, None, context=voice, draft_id=draft['draft_id'], raw_story='Correction: I was long, not short.')
        self.assertEqual(corrected['story']['direction'], 'Bullish')
        self.context.close(); voice.close()
        browser = self.fresh(surface='browser_voice')
        resumed = self.action('get_journal_story', {}, None, context=browser)
        self.assertEqual(resumed['story']['direction'], 'Bullish')
        self.assertIn('Correction: I was long', resumed['raw_story_text'])
        self.assertEqual(self.count('journal_story_drafts'), 1)

    def test_15_latest_trade_uses_execution_time_not_save_order(self):
        for date, clock in (('2040-02-08', '10:00'), ('2040-02-07', '11:00')):
            draft = self.stage({'asset': 'NAS100', 'direction': 'Bearish', 'reported_entry_at': date+'T'+clock+':00-05:00'},
                'Journal a separate trade.', new_draft=True, new_trade=True)
            self.save(draft)
        trade = self.action('get_journal_history', {'latest': 'trade', 'date_basis': 'trade'}, 'Read the latest actual trade.')
        saved = self.action('get_journal_history', {'latest': 'journal', 'date_basis': 'saved'}, 'Read the latest saved journal.')
        self.assertEqual(trade['journals'][0]['trade_number'], 1)
        self.assertEqual(saved['journals'][0]['trade_number'], 2)
        self.assertNotEqual(trade['journals'][0]['reported_trade_date'], trade['journals'][0]['saved_at'])

    def test_16_midnight_date_session_and_timezone_are_explicit(self):
        values = {'asset': 'NAS100', 'direction': 'Bearish', 'session': 'night', 'trade_date': '2040-02-06',
            'time_zone': 'America/New_York', 'reported_entry_at': '2040-02-06T23:50:00-05:00',
            'reported_exit_at': '2040-02-07T00:20:00-05:00',
            'reported_exit_time_text': 'Around 12:20 AM the next day'}
        draft = self.stage(values)
        self.assertIn('America/New_York', draft['draft_summary'])
        self.assertIn('night', draft['draft_summary'])
        self.save(draft)
        row = self.history()['journals'][0]
        for key in ('trade_date', 'session', 'time_zone', 'reported_entry_at', 'reported_exit_at'):
            self.assertEqual(row['metadata'][key], values[key])
        unknown = self.stage({}, 'Journal a separate trade on NAS today around midnight.', new_draft=True, new_trade=True)
        self.assertNotIn('trade_date', unknown['story'])
        self.assertNotIn('reported_entry_at', unknown['story'])

    def test_17_two_members_never_share_drafts_records_or_photos(self):
        draft = self.stage(text='Journal my NAS trade. I entered short.')
        self.save(draft)
        other = self.fresh(30)
        result = self.action('get_journal_story', {'draft_id': draft['draft_id']}, 'Read my draft.', context=other, user=30)
        self.assertFalse(result['ok'], result)
        rows = self.action('get_journal_history', {}, 'Read my journals.', context=other, user=30)
        self.assertEqual(rows['journals'], [])
        photo = photos.save_upload(self.db, 10, 20, 'private-source', b'\x89PNG\r\n\x1a\nfixture')['photo_id']
        bad = self.action('save_journal_entry', {'photo_id': photo, 'description': 'Foreign source'}, context=other, user=30)
        self.assertFalse(bad['ok'], bad)
        self.assertEqual(self.count('journals'), 1)

    def test_18_revocation_invalidates_old_and_new_sessions(self):
        draft = self.stage(text='Journal my NAS trade. I entered short.')
        self.save(draft)
        self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
        self.conn.commit()
        before = list(self.conn.iterdump())
        for context in (self.context, self.fresh(surface='discord_voice'), self.fresh(surface='browser_voice')):
            for name, args in (('get_journal_story', {'draft_id': draft['draft_id']}),
                               ('get_journal_history', {}), ('save_journal_story', {'draft_id': draft['draft_id']})):
                result = self.action(name, args, 'Read or save my journal.', context=context)
                self.assertFalse(result['ok'], result)
                self.assertNotIn('NAS100', json.dumps(result))
        self.assertEqual(list(self.conn.iterdump()), before)

    def range_review(self):
        rows = []
        for label, hour in (('Young Lefty', '07:00'), ('9ate8', '08:00')):
            rows.append({'label': label, 'anchor_start_ny': fixture.NY(hour),
                'anchor': {'start_ny': fixture.NY(hour), 'timeframe': 'H1', 'complete': True},
                'direction_observed': 'bearish', 'observation_coverage': {'complete': True},
                'objectives': {'midpoint': {'status': 'observed', 'first_touch': {'bar_open_ny': fixture.NY('09:05')}}},
                'invalidated_at_ny': fixture.NY('10:20'),
                'double_purge': {'status': 'observed', 'first_side': 'buy', 'second_side': 'sell',
                    'first_purge_at_ny': fixture.NY('09:00'), 'second_purge_at_ny': fixture.NY('10:10')},
                'events': [{'kind': 'midpoint_observed', 'bar_open_ny': fixture.NY('09:05')},
                           {'kind': 'range_invalidated', 'bar_open_ny': fixture.NY('10:20')}]})
        self.context.begin_turn(None)
        result = self.context.run('review_market_session', dict(asset='NAS100', date_ny='2026-10-02',
            shift='day', context_action='switch'), lambda n,a: {'ok': True, 'asset': 'NAS100',
                'review': {'date_ny': '2026-10-02', 'shift': 'day', 'shift_story': {'ranges': deepcopy(rows)}}})
        self.assertTrue(result['ok'], result)
        return rows

    def test_19_setup_history_keeps_distinct_range_chronology(self):
        source = self.range_review()
        result = self.action('save_journal_entry', {'description': 'I traded the reviewed setup; execution unknown.',
            'metadata_json': '{"kind":"trade"}', 'market_reference': 'selected_review'}, 'That was my trade.')
        self.assertTrue(result['ok'], result)
        self.context.close()
        row = self.history(context=self.fresh())['journals'][0]
        saved = row['metadata']['market_review']['range_history']
        self.assertEqual([r['anchor_start_ny'] for r in saved], [r['anchor_start_ny'] for r in source])
        for actual, expected in zip(saved, source):
            for key in ('objectives', 'invalidated_at_ny', 'double_purge', 'events'):
                self.assertEqual(actual[key], expected[key])
        self.assertIsNone(row['result_r'])
        self.assertEqual(self.count('thesis_executions'), 0)
        rendered = '\n'.join(recall.messages(self.history(context=self.fresh())))
        self.assertIn('double purge', rendered)
        self.assertIn('09:05', rendered)
        self.assertIn('10:20', rendered)

    def test_20_review_separates_setup_plan_and_actual_outcome(self):
        self.range_review()
        result = self.action('save_journal_entry', {'description': 'Reviewed setup delivered; I exited at a loss off plan.',
            'result_r': -1, 'rule_adherence': 'no', 'metadata_json': '{"kind":"trade","reported_outcome":"loss"}',
            'market_reference': 'selected_review'}, 'That was my trade. I lost 1R and did not follow my plan.')
        self.assertTrue(result['ok'], result)
        history = self.history()
        row = history['journals'][0]
        dimensions = row['review_dimensions']
        self.assertEqual(dimensions['plan_adherence']['value'], 'no')
        self.assertEqual(dimensions['actual_outcome']['result_r'], -1)
        self.assertEqual(dimensions['actual_outcome']['reported_outcome'], 'loss')
        self.assertEqual(dimensions['setup_validity']['assessment'], 'not_assessed')
        self.assertEqual(len(dimensions['setup_validity']['ranges']), 2)
        self.assertNotIn('self_grade', row['metadata'])
        compact = journal_tool_payload('get_journal_history', history)
        self.assertEqual(compact['journals'][0]['review_dimensions']['actual_outcome']['result_r'], -1)
        self.assertIn('Setup validity', history['context_text'])
        self.assertIn('Plan adherence', history['context_text'])
        self.assertIn('Actual outcome', history['context_text'])


if __name__ == '__main__':
    unittest.main()
