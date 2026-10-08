"""Synthetic-only unified recall/delivery; no production writes or network."""
import json
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import httpx

from gbop_voice_web import journal_recall as recall
from gbop_voice_web.delivery_receipts import DeliveryBinding, delivery_status, canonical_arguments
from gbop_voice_web.journal_presentation import journal_tool_payload, JOURNAL_PRESENTATION_MAX_CHARS
from gbop_voice_web.market_conversation import MarketConversation
from tests import test_unified_journal_recall as fixtures


class BundleTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.UnifiedRecallTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.conn, self.db = fixture.conn, fixture.db
        self.photo, self.detail, self.event = fixture.photo, fixture.detail, fixture.event
        self.conn.executescript('''
          CREATE TABLE members(guild_id INT,user_id INT,activated INT,revoked INT);
          INSERT INTO members VALUES(10,20,1,0),(10,30,1,0),(11,20,1,0);
          CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT,lease_until BIGINT,last_tick BIGINT,state TEXT);
        ''')
        self.context = NS(owner=(10,20,'discord'), session_id='bundle-test', generation=1,
                          _lock=threading.RLock(), _auth_revision=None)
        self.context.current = lambda n: n == self.context.generation
        self.args = {'trade_number': 1, '_delivery_binding': DeliveryBinding(self.context, 1)}
        self.channel = NS(status_code=200, json=lambda: {'id': 'private'})
        self.message = NS(status_code=200, json=lambda: {'id': 'accepted'})
        self.detail(100, 'shared', {'trade_date': '2026-10-02', 'emotion': 'Legacy uncertainty',
            'feeling_history': [{'id': n, 'stage': 'during', 'feeling': f'Feeling {n}',
                                'recorded_at': '2026-10-03'} for n in range(1, 10)],
            'self_grade': {'version': 1, 'type': 'type1', 'member_reported': True,
                'source': 'member_reported', 'adherence': 'followed_plan', 'outcome': 'profit',
                'predefined_stop': None, 'off_plan_reason': None, 'note': 'My own assessment'},
            'transcription': 'Full source manuscript', 'entry_price': 123, 'exit_reason': 'Planned exit'})
        self.event('journal_canonical_v1', {'journal_id': 100})

    def send(self, args=None, side_effect=None):
        client = Mock()
        client.post.side_effect = side_effect or (lambda path, **kw: self.channel if path == '/users/@me/channels' else self.message)
        with patch.dict('os.environ', {'DISCORD_TOKEN': 'synthetic'}), patch('httpx.Client') as factory:
            factory.return_value.__enter__.return_value = client
            result = recall.send_history(self.db, 10, 20, self.args if args is None else args)
        return result, client

    def texts(self, client):
        return '\n'.join(c.kwargs['json']['content'] for c in client.post.call_args_list
                         if 'content' in c.kwargs.get('json', {}))

    def images(self, client):
        return [c for c in client.post.call_args_list if 'files' in c.kwargs]

    def test_default_bundle_has_every_context_component_and_one_image(self):
        before = self.conn.total_changes
        source = recall.history(self.db, 10, 20, {'trade_number': 1})
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(source['photo_count'], 1)
        with patch('gbop_voice_web.trade_photos.init_photos', side_effect=AssertionError('No schema writes')):
            result, client = self.send()
        self.assertEqual(result['status'], 'delivered')
        self.assertEqual(result['photo_sent_count'], 1)
        self.assertGreater(result['text_sent_count'], 0)
        self.assertEqual(result['sent_count'], result['text_sent_count'] + result['photo_sent_count'])
        for term in ('Synthetic journal', 'Original note', 'Full source manuscript', 'Feeling 1',
                     'Feeling 9', 'Member SELF grade: Type 1', 'My own assessment', '123', 'Planned exit'):
            self.assertIn(term, self.texts(client))
        self.assertEqual(client.post.call_args_list[0].kwargs['json'], {'recipient_id': '20'})
        self.assertNotIn('image_base64', json.dumps(result))
        self.assertNotIn('Feeling 1', json.dumps(delivery_status(self.db, 10, 20, {})))

    def test_complete_photo_union_beyond_pages_deduplicates_and_scopes_member(self):
        for n in range(12):
            self.photo(f'extra-{n:02}', thesis=25)
        self.photo('metadata-source')
        self.photo('event-source')
        self.photo('other-record', thesis=40)
        self.photo('wrong-user', thesis=25, user=30)
        self.photo('wrong-guild', thesis=25, guild=11)
        self.conn.execute("UPDATE journal_details SET metadata=? WHERE journal_id=100", (json.dumps({
            'source_attachments': [{'photo_id': 'metadata-source'}, {'photo_id': 'shared'},
                                   {'photo_id': 'wrong-user'}, {'photo_id': 'wrong-guild'}]}),))
        self.event('journal_source_v1', {'journal_id': 100, 'photo_id': 'event-source'})
        self.event('journal_source_v1', {'journal_id': 100, 'photo_id': 'shared'})
        result, client = self.send()
        self.assertEqual(result['photo_sent_count'], 15)
        names = [c.kwargs['files']['files[0]'][0] for c in self.images(client)]
        self.assertEqual(len(names), len(set(names)))
        for forbidden in ('other-record', 'wrong-user', 'wrong-guild'):
            self.assertNotIn(forbidden, ' '.join(names))
        self.assertFalse(result['has_more'])

    def test_legacy_direct_and_owned_thesis_sources_union(self):
        self.conn.execute("INSERT INTO journals VALUES(110,25,10,20,'Preserved source','',NULL,'','2026-10-02')")
        self.photo('legacy-source')
        self.detail(110, 'legacy-source')
        result, client = self.send({**self.args, 'trade_number': None, 'legacy_journal_number': 2})
        self.assertEqual(result['photo_sent_count'], 2)
        self.assertIn('Preserved source', self.texts(client))

    def test_unlinked_legacy_does_not_borrow_other_record_photos(self):
        self.conn.execute("INSERT INTO journals VALUES(110,NULL,10,20,'Legacy only','',NULL,'','2026-10-04')")
        self.photo('legacy-only')
        self.detail(110, 'legacy-only')
        result, client = self.send({**self.args, 'trade_number': None, 'legacy_journal_number': 2})
        self.assertEqual(result['photo_sent_count'], 1)
        self.assertIn('legacy-only', self.images(client)[0].kwargs['files']['files[0]'][0])
        self.assertNotIn('Synthetic journal', self.texts(client))

    def test_text_only_explicit_override_has_zero_image_posts(self):
        result, client = self.send({**self.args, 'include_photos': False})
        self.assertTrue(result['ok'])
        self.assertFalse(result['include_photos'])
        self.assertEqual(result['photo_sent_count'], 0)
        self.assertEqual(self.images(client), [])
        self.assertEqual(result['photo_count'], 1)
        self.assertNotEqual(canonical_arguments('send_journal_history', {'include_photos': False}),
                            canonical_arguments('send_journal_history', {}))

    def test_partial_photo_failure_reports_components_and_cannot_repeat(self):
        self.photo('second', thesis=25)
        photo_attempts = 0
        def post(path, **kw):
            nonlocal photo_attempts
            if path == '/users/@me/channels': return self.channel
            if 'files' in kw:
                photo_attempts += 1
                if photo_attempts == 2: return NS(status_code=403)
            return self.message
        result, client = self.send(side_effect=post)
        self.assertEqual((result['status'], result['photo_sent_count']), ('partial', 1))
        self.assertGreater(result['text_sent_count'], 0)
        again, client = self.send()
        self.assertEqual(again['receipt_id'], result['receipt_id'])
        client.post.assert_not_called()
        status = delivery_status(self.db, 10, 20, {'receipt_id': result['receipt_id']})
        self.assertEqual(status['receipts'][0]['photo_sent_count'], 1)
        self.assertEqual(delivery_status(self.db, 10, 30, {'receipt_id': result['receipt_id']})['receipts'], [])

    def test_photo_timeout_keeps_uncertainty_without_success_claim(self):
        def post(path, **kw):
            if path == '/users/@me/channels': return self.channel
            if 'files' in kw: raise httpx.ReadTimeout('Synthetic')
            return self.message
        result, _ = self.send(side_effect=post)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['delivery_uncertain'])
        self.assertEqual(result['photo_sent_count'], 0)

    def test_photo_lookup_failure_is_unknown_and_bundle_is_partial(self):
        with patch('gbop_voice_web.journal_bundle.attach_photos', side_effect=RuntimeError('Private DB error')):
            result, client = self.send()
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['photo_count'])
        self.assertIn('photo count is unknown', self.texts(client))
        self.assertNotIn('Private DB error', json.dumps(result))

    def test_wrong_member_binding_and_invalid_record_never_post(self):
        self.context.owner = (10,30,'discord')
        result, client = self.send()
        self.assertFalse(result['ok']); client.post.assert_not_called()
        self.context.owner = (10,20,'discord')
        result, client = self.send({**self.args, 'trade_number': 999})
        self.assertFalse(result['ok']); client.post.assert_not_called()

    def test_revocation_after_context_lookup_stops_delivery(self):
        original = recall.history
        def revoked(*args, **kw):
            result = original(*args, **kw)
            self.conn.execute('UPDATE members SET revoked=1 WHERE guild_id=10 AND user_id=20')
            return result
        with patch.object(recall, 'history', side_effect=revoked):
            result, client = self.send()
        self.assertFalse(result['ok']); client.post.assert_not_called()

    def test_repeat_same_bundle_recovers_and_changed_selected_content_is_new(self):
        result, _ = self.send()
        again, client = self.send()
        self.assertEqual(result['receipt_id'], again['receipt_id']); client.post.assert_not_called()
        self.context.generation = 2
        self.photo('new-photo', thesis=25)
        changed, client = self.send({'trade_number': 1, '_delivery_binding': DeliveryBinding(self.context, 2)})
        self.assertNotEqual(result['receipt_id'], changed['receipt_id'])
        self.assertEqual(changed['photo_sent_count'], 2)

    def test_unrelated_records_do_not_create_duplicate_delivery(self):
        original, _ = self.send()
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'Unrelated new journal','',NULL,'','2026-10-05')")
        again, client = self.send()
        self.assertEqual(original['receipt_id'], again['receipt_id'])
        client.post.assert_not_called()

    def test_partial_scope_cannot_be_automatically_repeated_after_content_change(self):
        def post(path, **kw):
            if path == '/users/@me/channels': return self.channel
            if 'files' in kw: return NS(status_code=403)
            return self.message
        original, _ = self.send(side_effect=post)
        self.assertEqual(original['status'], 'partial')
        self.photo('later', thesis=25)
        self.context.generation = 2
        again, client = self.send({'trade_number': 1, '_delivery_binding': DeliveryBinding(self.context, 2)})
        self.assertEqual(original['receipt_id'], again['receipt_id'])
        client.post.assert_not_called()

    def test_zero_photos_does_not_turn_text_timeout_into_no_photos(self):
        self.conn.execute('DELETE FROM trade_photos')
        result, _ = self.send(side_effect=[self.channel, httpx.ReadTimeout('Synthetic')])
        self.assertEqual(result['status'], 'error')
        self.assertTrue(result['delivery_uncertain'])
        self.assertEqual(result['photo_count'], 0)

    def test_photo_status_includes_bundle_receipt(self):
        original, _ = self.send()
        receipts = delivery_status(self.db, 10, 20, {'kind': 'photos'})['receipts']
        self.assertEqual(receipts[0]['receipt_id'], original['receipt_id'])
        self.assertEqual(receipts[0]['photo_sent_count'], 1)

    def test_metadata_only_update_is_latest_saved_journal(self):
        self.conn.execute("INSERT INTO journals VALUES(110,40,10,20,'Newer creation','',NULL,'','2026-10-02')")
        self.detail(110, metadata={})
        self.conn.execute("UPDATE journal_details SET updated_at='2026-10-05' WHERE journal_id=100")
        result = recall.history(self.db, 10, 20, {'latest': 'journal'})
        self.assertEqual(result['journals'][0]['trade_number'], 1)
        self.assertEqual(result['journals'][0]['saved_at'], '2026-10-05')

    def test_complete_read_context_pages_never_send_and_retain_early_feelings(self):
        self.conn.execute('UPDATE journals SET study_note=? WHERE id=100', ('Ω' * 35000,))
        text, offset = '', 0
        with patch('httpx.Client', side_effect=AssertionError('Reads must not send')):
            while True:
                raw = recall.history(self.db, 10, 20, {'trade_number': 1, 'detail_offset': offset})
                shown = journal_tool_payload('get_journal_history', raw)
                self.assertLessEqual(len(json.dumps(shown)), JOURNAL_PRESENTATION_MAX_CHARS)
                text += shown['context_text']
                if not shown['has_more_details']: break
                self.assertGreater(shown['next_detail_offset'], offset)
                offset = shown['next_detail_offset']
        self.assertEqual(text.count('Ω'), 35000)
        self.assertIn('Feeling 1', text); self.assertIn('Feeling 9', text)

    def test_actual_trade_date_ignores_later_import_and_journal_update(self):
        self.conn.execute("INSERT INTO journals VALUES(110,40,10,20,'Older occurrence uploaded later','',NULL,'','2026-10-05')")
        self.detail(110, metadata={'trade_date': '2026-09-01'})
        self.event('journal_canonical_v1', {'journal_id': 110}, thesis=40)
        self.conn.execute("INSERT INTO journals VALUES(120,NULL,10,20,'Latest legacy journal','',NULL,'','2026-10-06')")
        actual = recall.history(self.db, 10, 20, {'latest': 'trade', 'date_basis': 'trade'})
        self.assertEqual(actual['journals'][0]['trade_number'], 1)
        saved = recall.history(self.db, 10, 20, {'latest': 'trade', 'date_basis': 'saved'})
        self.assertEqual(saved['journals'][0]['trade_number'], 2)
        journal = recall.history(self.db, 10, 20, {'latest': 'journal'})
        self.assertEqual(journal['journals'][0]['legacy_journal_number'], 3)
        self.assertIn('does not establish', saved['selection_note'])

    def test_missing_conflicting_or_tied_dates_require_clarification(self):
        self.assertEqual(recall.history(self.db, 10, 20, {'latest': 'trade', 'date_basis': 'trade'})['status'], 'trade_date_ambiguous')
        self.conn.execute("INSERT INTO journals VALUES(110,40,10,20,'Other trade','',NULL,'','2026-10-02')")
        self.detail(110, metadata={'trade_date': '2026-10-02'})
        self.event('journal_canonical_v1', {'journal_id': 110}, thesis=40)
        self.assertEqual(recall.history(self.db, 10, 20, {'latest': 'trade', 'date_basis': 'trade'})['status'], 'trade_date_ambiguous')
        result, client = self.send({'latest': 'trade', 'date_basis': 'trade'})
        self.assertFalse(result['ok']); client.post.assert_not_called()

    def test_market_review_date_is_not_a_reported_trade_date(self):
        self.conn.execute("UPDATE theses SET status='IDEA' WHERE id=40")
        meta = {'trade_date': '2026-10-05', 'provenance': {'context_defaults': ['trade_date'], 'member_reported': []}}
        self.conn.execute('UPDATE journal_details SET metadata=? WHERE journal_id=100', (json.dumps(meta),))
        self.assertEqual(recall.history(self.db, 10, 20, {'latest': 'trade', 'date_basis': 'trade'})['status'], 'trade_date_ambiguous')
        text = recall.history(self.db, 10, 20, {'trade_number': 1})['context_text']
        self.assertIn('not a reported execution date', text)
        self.assertNotIn('Reported trade date:', text)

    def test_day_only_and_exact_timestamp_same_day_are_ambiguous(self):
        self.conn.execute("INSERT INTO journals VALUES(110,40,10,20,'Same day other trade','',NULL,'','2026-10-02')")
        self.detail(110, metadata={'reported_entry_at': '2026-10-02T10:00:00-04:00'})
        self.event('journal_canonical_v1', {'journal_id': 110}, thesis=40)
        self.assertEqual(recall.history(self.db, 10, 20, {'latest': 'trade', 'date_basis': 'trade'})['status'], 'trade_date_ambiguous')

    def test_resolved_latest_is_bound_across_new_save_and_display_number_change(self):
        context = MarketConversation(owner=(10,20,'discord'))
        context.begin_turn('Send my latest journal')
        runner = lambda name, args: recall.history(self.db, 10, 20, args)
        first = context.run('get_journal_history', {'latest': 'journal'}, runner)
        self.assertEqual(first['journals'][0]['record_key'], 'trade:25')
        self.conn.execute("INSERT INTO journals VALUES(111,NULL,10,20,'Newer record','',NULL,'','2026-10-06')")
        self.conn.execute("INSERT INTO theses VALUES(1,10,20,'Earlier number','Synthetic','IDEA')")
        next_page = context.run('get_journal_history', {'trade_number': 1, 'detail_offset': 12}, runner)
        self.assertEqual(next_page['journals'][0]['record_key'], 'trade:25')
        delivered = context.run('send_journal_history', {'trade_number': 1}, runner)
        self.assertEqual(delivered['journals'][0]['record_key'], 'trade:25')

    def test_general_record_pagination_is_not_bound_by_date_basis(self):
        context = MarketConversation(owner=(10,20,'discord'))
        context.begin_turn('List my journals')
        runner = lambda name, args: recall.history(self.db, 10, 20, args)
        first = context.run('get_journal_history', {'limit': 1, 'offset': 0, 'date_basis': 'saved'}, runner)
        second = context.run('get_journal_history', {'limit': 1, 'offset': 1, 'date_basis': 'saved'}, runner)
        self.assertNotEqual(first['journals'][0]['record_key'], second['journals'][0]['record_key'])

    def test_studies_are_not_selected_as_latest_trade(self):
        self.conn.execute("UPDATE theses SET status='IDEA' WHERE id=40")
        result = recall.history(self.db, 10, 20, {'latest': 'trade', 'date_basis': 'trade'})
        self.assertEqual(result['journals'][0]['trade_number'], 1)


class RecallIntentTests(unittest.TestCase):
    def test_summary_does_not_authorize_delivery(self):
        for text in ('Tell me about my last trade', 'Review journal #1', 'How did my trade go?',
                     "Don't send my journal", 'Summarize my journal'):
            with self.subTest(text=text):
                _, denial = recall.bind_recall_intent('send_journal_history', {}, text)
                self.assertIsNotNone(denial)
                _, denial = recall.bind_recall_intent('get_journal_history', {}, text)
                self.assertIsNone(denial)

    def test_last_trade_cannot_be_flattened_into_unfiltered_latest_legacy(self):
        args, denial = recall.bind_recall_intent('send_journal_history',
            {'legacy_journal_number': 6, 'limit': 1}, 'Send my last trade')
        self.assertIsNone(denial)
        self.assertEqual((args['latest'], args['date_basis']), ('trade', 'trade'))
        self.assertIsNone(args['legacy_journal_number'])
        journal, _ = recall.bind_recall_intent('get_journal_history', {}, 'My last journal entry')
        self.assertEqual((journal['latest'], journal['date_basis']), ('journal', None))
        recorded, _ = recall.bind_recall_intent('get_journal_history', {}, 'My latest recorded trade')
        self.assertIsNone(recorded['date_basis'])

    def test_text_only_and_exact_identity_are_preserved(self):
        args, denial = recall.bind_recall_intent('send_journal_history', {'trade_number': 2},
                                               'Send trade #2 text only, without photos')
        self.assertIsNone(denial)
        self.assertEqual(args['trade_number'], 2)
        self.assertFalse(args['include_photos'])

    def test_status_question_cannot_authorize_delivery_and_default_photos_are_enforced(self):
        for text in ('Did you send my journal?', 'Are you going to send my last trade?',
                     'Have you sent my journal?', 'Why did you send trade #1?', 'Summarize my journals',
                     'Tell me about my trades', 'Did you send it?', 'How many photos do I have?'):
            _, denial = recall.bind_recall_intent('send_journal_history', {}, text)
            self.assertIsNotNone(denial, text)
        args, denial = recall.bind_recall_intent('send_journal_history', {'include_photos': False}, 'Send trade #2')
        self.assertIsNone(denial)
        self.assertTrue(args['include_photos'])
        for text in ('Can you send trade #2?', 'Please send my last journal', 'I want you to send my journal'):
            _, denial = recall.bind_recall_intent('send_journal_history', {}, text)
            self.assertIsNone(denial, text)

    def test_contextual_dispatch_blocks_summary_send_and_routes_latest_read(self):
        context = MarketConversation()
        context.begin_turn('Summarize my last trade')
        runner = Mock(return_value={'ok': True})
        self.assertFalse(context.run('send_journal_history', {}, runner)['ok'])
        runner.assert_not_called()
        context.run('get_journal_history', {'limit': 1}, runner)
        self.assertEqual(runner.call_args.args[1]['latest'], 'trade')
        self.assertIsNone(runner.call_args.args[1]['date_basis'])


if __name__ == '__main__':
    unittest.main()
