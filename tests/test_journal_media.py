"""Photo-backed daily narration, using synthetic SQLite records only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_journal_day as fixture
from gbop_voice_web import journal_media as media, journal_day, trade_photos
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.unified_journal import source_journal_id


class JournalMediaTests(unittest.TestCase):
    tearDown = fixture.JournalDayTests.tearDown
    review = fixture.JournalDayTests.review
    call = fixture.JournalDayTests.call
    stage = fixture.JournalDayTests.stage
    save = fixture.JournalDayTests.save

    def setUp(self):
        fixture.JournalDayTests.setUp(self)
        self.photo = trade_photos.save_upload(self.db, 10, 20, 'synthetic-page', b'\x89PNG\r\n\x1a\nfixture')['photo_id']

    def source(self, index=1, **extra):
        return dict(photo_id=self.photo, entry_index=index, transcription='NAS100 988 Super Soup; remaining text [unclear].', **extra)

    def with_photos(self):
        value = deepcopy(fixture.DAY)
        value['trades'][0]['photo_sources'] = [self.source()]
        value['trades'][1]['photo_sources'] = [self.source(2)]
        return value

    def test_multi_setup_page_atomic_save_and_idempotent_retry(self):
        draft = self.stage(self.with_photos())
        self.assertTrue(draft['ok'], draft)
        self.assertEqual(draft['counts']['filled_entries'], 3)
        result = self.save(draft)
        self.assertTrue(result['ok'], result)
        ids = [source_journal_id(self.conn, 10, 20, self.photo, n) for n in (1, 2)]
        self.assertNotEqual(*ids)
        self.assertNotIn(None, ids)
        self.assertTrue(media.sources_saved(self.conn, 10, 20, ids[0], [self.source()]))
        self.assertTrue(media.sources_saved(self.conn, 10, 20, ids[1], [self.source(2)]))
        before = list(self.conn.iterdump())
        self.assertTrue(self.save(draft)['ok'])
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)

    def test_foreign_photo_never_attaches_or_changes_draft(self):
        draft = self.stage()
        foreign = trade_photos.save_upload(self.db, 10, 30, 'other-page', b'\x89PNG\r\n\x1a\nother')['photo_id']
        before = list(self.conn.iterdump())
        result = self.stage({'trades':[{'trade_key':'morning_988','photo_sources':[{'photo_id':foreign}]}]})
        self.assertFalse(result['ok'], result)
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertTrue(self.save(draft)['ok'])

    def test_same_page_section_cannot_be_assigned_to_two_setups(self):
        self.stage()
        value = self.with_photos()
        value['trades'][1]['photo_sources'] = [self.source()]
        before = list(self.conn.iterdump())
        result = self.stage(value)
        self.assertFalse(result['ok'], result)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_failure_attaching_second_source_rolls_back_whole_save(self):
        draft = self.stage(self.with_photos())
        before = list(self.conn.iterdump())
        attach = media.attach_sources
        def fail(conn, guild, user, journal_id, sources):
            if sources[0]['entry_index'] == 2:
                raise ValueError('Synthetic attachment failure.')
            return attach(conn, guild, user, journal_id, sources)
        with patch.object(media, 'attach_sources', side_effect=fail):
            result = self.save(draft)
        self.assertFalse(result['ok'], result)
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertTrue(self.save(draft)['ok'])

    def test_resume_keeps_photo_transcription_separate_from_member_narration(self):
        draft = self.stage(self.with_photos())
        self.assertTrue(self.save(draft)['ok'])
        fresh = MarketConversation((10,20,'photo-resume'), auth_provider=(self.db,10,20))
        result = self.call('get_journal_day', {'draft_id':draft['draft_id']}, 'Read my day.', context=fresh)
        self.assertTrue(result['saved'])
        self.assertEqual(result['trades'][0]['photo_sources'], [self.source()])
        self.assertNotIn('[unclear]', result['raw_story_text'])
        prompt = journal_day.prompt_context(fresh._journal_story)
        self.assertIn(self.photo, prompt)
        self.assertNotIn(self.source()['transcription'], prompt)
        self.assertIn('transcription_chars', prompt)
        self.assertEqual(fresh._journal_story['day_trades']['morning_988']['photo_sources'], [self.source()])

    def test_existing_trade_photo_preserves_fills_results_and_old_photos(self):
        value = self.with_photos()
        value['trades'][0]['story']['result_r'] = 1.5
        value['trades'][0]['story']['entries'][0]['risk_r'] = .5
        saved = self.save(self.stage(value))
        self.assertTrue(saved['ok'], saved)
        number = saved['receipts'][0]['trade_number']
        new_photo = trade_photos.save_upload(self.db,10,20,'additional-picture',b'\x89PNG\r\n\x1a\nsecond')['photo_id']
        fresh = MarketConversation((10,20,'add-photo'), auth_provider=(self.db,10,20))
        change = {'trades':[{'trade_key':'existing','trade_number':number,'photo_sources':[{'photo_id':new_photo,'transcription':'Additional chart.'}]}]}
        draft = self.call('stage_journal_day', {'day_json':json.dumps(change)}, 'Add this picture to my first trade journal.', context=fresh)
        self.assertTrue(draft['ok'], draft)
        story = draft['trades'][0]['story']
        self.assertEqual(story, value['trades'][0]['story'] | {'trade_date':value['trade_date']})
        self.assertEqual(len(draft['trades'][0]['photo_sources']), 2)
        result = self.call('save_journal_day', {'draft_id':draft['draft_id']}, 'Save it.', context=fresh)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM journals').fetchone()[0], 2)
        journal_id = source_journal_id(self.conn,10,20,new_photo,1)
        self.assertEqual(journal_id, source_journal_id(self.conn,10,20,self.photo,1))
        self.assertEqual(self.conn.execute('SELECT result_r FROM journals WHERE id=?',(journal_id,)).fetchone()[0], 1.5)

    def test_unreadable_photo_does_not_infer_time_date_or_execution(self):
        value = {'trades':[{'trade_key':'photo_story','story':{'asset':'NAS100','play':'9ate8'},'photo_sources':[self.source()]}]}
        draft = self.call('stage_journal_day', {'day_json':json.dumps(value)}, 'Journal this chart without guessing the unreadable parts.')
        self.assertTrue(draft['ok'], draft)
        self.assertEqual(draft['counts']['filled_entries'], 0)
        self.assertEqual(draft['trades'][0]['story'], {'asset':'NAS100','play':'9ate8','entries':[]})
        self.assertIsNone(draft['trade_date'])

    def test_photo_added_to_older_unstructured_journal_preserves_original_story(self):
        saved = self.call('save_journal_entry', {'description':'I took two entries and waited calmly at the 8 AM high.',
                         'result_r':1.5, 'metadata_json':json.dumps({'kind':'trade','asset':'NAS100','play':'9ate8','trade_date':'2026-10-27'})},
                         'Save my NAS100 trade journal.')
        self.assertTrue(saved['ok'], saved)
        fresh = MarketConversation((10,20,'old-format-photo'), auth_provider=(self.db,10,20))
        change = {'trades':[{'trade_key':'older','trade_number':saved['trade_number'],'photo_sources':[self.source()]}]}
        draft = self.call('stage_journal_day', {'day_json':json.dumps(change)}, 'Add this picture to that saved trade.', context=fresh)
        self.assertTrue(draft['ok'], draft)
        result = self.call('save_journal_day', {'draft_id':draft['draft_id']}, 'Save it.', context=fresh)
        self.assertTrue(result['ok'], result)
        row = self.conn.execute('SELECT description,result_r FROM journals').fetchone()
        self.assertIn('I took two entries and waited calmly at the 8 AM high.', row['description'])
        self.assertEqual(row['result_r'], 1.5)

    def test_photo_cannot_relabel_saved_trade_as_a_different_day(self):
        saved = self.save(self.stage(self.with_photos()))
        fresh = MarketConversation((10,20,'different-day-photo'), auth_provider=(self.db,10,20))
        change = {'trade_date':'2026-10-28','trades':[{'trade_key':'older','trade_number':saved['receipts'][0]['trade_number'],'photo_sources':[self.source()]}]}
        draft = self.call('stage_journal_day', {'day_json':json.dumps(change)}, 'Add this picture to that trade.', context=fresh)
        self.assertFalse(draft['ok'], draft)
        self.assertIn('another day', draft['error'])

    def test_transcription_correction_preserves_other_setup(self):
        first = self.stage(self.with_photos())
        corrected = self.source()
        corrected['transcription'] = 'NAS100 Turtle Wick Soup, corrected from Super Soup.'
        result = self.stage({'trades':[{'trade_key':'morning_988','photo_sources':[corrected]}]})
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['trades'][1], first['trades'][1])
        self.assertEqual(result['trades'][0]['photo_sources'], [corrected])
        self.assertTrue(self.save(result)['ok'])

    def test_missing_photo_after_save_prevents_false_receipt(self):
        draft = self.stage(self.with_photos())
        self.assertTrue(self.save(draft)['ok'])
        self.conn.execute('DELETE FROM trade_photos WHERE id=?',(self.photo,))
        self.conn.commit()
        result = self.save(draft)
        self.assertFalse(result['ok'], result)

    def test_existing_page_cannot_be_reassigned_to_another_trade(self):
        saved = self.save(self.stage(self.with_photos()))
        fresh = MarketConversation((10,20,'wrong-photo-trade'), auth_provider=(self.db,10,20))
        change = {'trades':[{'trade_key':'other','trade_number':saved['receipts'][1]['trade_number'],'photo_sources':[self.source()]}]}
        draft = self.call('stage_journal_day', {'day_json':json.dumps(change)}, 'Add this photo to my other trade journal.', context=fresh)
        self.assertTrue(draft['ok'], draft)
        before = list(self.conn.iterdump())
        result = self.call('save_journal_day', {'draft_id':draft['draft_id']}, 'Save it.', context=fresh)
        self.assertFalse(result['ok'], result)
        self.assertEqual(before, list(self.conn.iterdump()))

    def test_existing_journal_change_after_photo_staging_cannot_be_overwritten(self):
        saved = self.save(self.stage(self.with_photos()))
        fresh = MarketConversation((10,20,'concurrent-photo'), auth_provider=(self.db,10,20))
        change = {'trades':[{'trade_key':'existing','trade_number':saved['receipts'][0]['trade_number'],'photo_sources':[self.source()]}]}
        draft = self.call('stage_journal_day', {'day_json':json.dumps(change)}, 'Add this picture to that saved trade.', context=fresh)
        self.assertTrue(draft['ok'], draft)
        journal_id = source_journal_id(self.conn,10,20,self.photo,1)
        self.conn.execute('UPDATE journals SET description=? WHERE id=?',('A newer correction from another conversation.',journal_id))
        self.conn.commit()
        before = list(self.conn.iterdump())
        result = self.call('save_journal_day', {'draft_id':draft['draft_id']}, 'Save it.', context=fresh)
        self.assertFalse(result['ok'], result)
        self.assertIn('changed after', result['error'])
        self.assertEqual(before, list(self.conn.iterdump()))


if __name__ == '__main__':
    unittest.main()
