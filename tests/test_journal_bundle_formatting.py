"""Offline presentation checks; original records and provenance stay intact."""
from copy import deepcopy
import json
import unittest

from gbop_voice_web import journal_recall as recall
from tests import test_unified_journal_recall as fixtures


def units(text):
    return len(text.encode('utf-16-le')) // 2


class ChunkTests(unittest.TestCase):
    def test_paragraphs_then_lines_then_words_preserve_every_character(self):
        for text in (
            'A paragraph. ' * 80 + '\n\n' + 'A second paragraph. ' * 90,
            'First line. ' * 90 + '\n' + 'Second line. ' * 90,
            'Price moved outside the range by 0.2 points. ' * 150,
            'Photo tier 2\n' * 400,
            'Complete thought\twith tabs ' * 150,
        ):
            with self.subTest(text=text[:30]):
                chunks = list(recall._text_chunks(text))
                self.assertEqual(''.join(chunks), text)
                self.assertTrue(all(0 < units(chunk) <= 1800 for chunk in chunks))
                self.assertTrue(all(chunk[-1].isspace() for chunk in chunks[:-1]))

    def test_prefers_paragraph_boundary_over_later_word(self):
        first = 'First paragraph. ' * 80 + '\n\n'
        text = first + 'Second paragraph. ' * 100
        self.assertEqual(list(recall._text_chunks(text))[0], first)

    def test_long_tokens_and_non_bmp_characters_never_lose_content(self):
        text = 'Ω' * 3700 + '🚀' * 2000 + ' final sentence'
        chunks = list(recall._text_chunks(text))
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(0 < units(chunk) <= 1800 for chunk in chunks))

    def test_continuation_labels_fit_without_losing_source(self):
        title = 'Trade #2 journal'
        source = title + '\n' + 'Price moved outside the range by 0.2. ' * 170
        chunks = list(recall._record_chunks(source, title))
        prefix = title + ' (continued)\n'
        self.assertTrue(all(chunk.startswith(prefix) for chunk in chunks[1:]))
        rebuilt = chunks[0] + ''.join(chunk[len(prefix):] for chunk in chunks[1:])
        self.assertEqual(rebuilt, source)
        self.assertTrue(all(units(chunk) <= 1800 for chunk in chunks))

    def test_nested_provenance_keeps_parent_and_child_distinct(self):
        nested = {'before': {'feeling': 'Unsure', 'source': 'image'},
                  'source': 'member_reported'}
        other = {'before': {'feeling': 'Unsure'}, 'source': 'image',
                 'extra': 'member_reported'}
        rendered = recall._display_value(nested)
        self.assertEqual(rendered,
                         'before:\n  feeling: Unsure\n  source: image\nsource: member_reported')
        self.assertNotEqual(rendered, recall._display_value(other))

    def test_serialized_event_details_never_drop_duplicate_keys_or_fail_on_depth(self):
        for source in (
            '{"note":"first saved fact","note":"second saved fact"}',
            '[' * 600 + '"deep note"' + ']' * 600,
            'null', 'true', '101.250',
        ):
            with self.subTest(source=source[:50]):
                self.assertEqual(recall._display_value(source, serialized=True), source)


class JournalFormattingTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.UnifiedRecallTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.conn, self.db = fixture.conn, fixture.db
        self.fixture = fixture
        fixture.detail(100, 'shared', {'reported_outcome': 'unknown'})
        fixture.event('journal_canonical_v1', {'journal_id': 100})

    def test_near_duplicate_events_keep_distinct_times_and_full_details(self):
        for clock, detail in (
            ('15:21:52', 'Moved outside the range by 0.2 points.'),
            ('15:23:38', 'Moved outside the range by 0.2 points; exit unreported.'),
        ):
            self.conn.execute('''INSERT INTO thesis_events
                (thesis_id,guild_id,user_id,event,details,created_at)
                VALUES(25,10,20,'invalidation',?,?)''',
                (json.dumps({'note': detail, 'source': 'member_reported'}),
                 '2026-10-02T' + clock + '+00:00'))
        before = self.conn.total_changes
        result = recall.history(self.db, 10, 20, {'trade_number': 1})
        original = deepcopy(result)
        text = '\n'.join(recall.messages(result))
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(result, original)
        self.assertEqual(result['journals'][0]['update_count'], 2)
        for value in ('Saved update #1', 'Saved update #2', '15:21:52', '15:23:38',
                      '0.2 points.', '0.2 points; exit unreported.',
                      'source: member_reported'):
            self.assertIn(value, text)
        self.assertNotIn('{"note"', text)

    def test_nested_corrections_are_readable_and_preserve_every_report(self):
        self.fixture.event('journal_audit_v1', {
            'journal_id': 100,
            'before': {'details': {'metadata': {'feeling_history': [
                {'id': 1, 'feeling': 'Unsure', 'stage': 'during'}]}}},
            'after': {'details': {'metadata': {'feeling_history': [
                {'id': 1, 'feeling': 'Unsure', 'stage': 'during'},
                {'id': 2, 'feeling': 'Calmer', 'stage': 'after', 'correction_of': 1}]}}},
        })
        result = recall.history(self.db, 10, 20, {'trade_number': 1})
        text = '\n'.join(recall.messages(result))
        self.assertIn('Before: 1. id: 1', text)
        self.assertIn('After: 1. id: 1', text)
        self.assertIn('2. id: 2', text)
        self.assertIn('correction of: 1', text)
        self.assertIn('feeling: Calmer', text)
        self.assertNotIn("{'id':", text)

    def test_photo_exit_observation_does_not_become_member_execution_fact(self):
        analysis = 'The image suggests an exit at 101.25; handwriting is unclear.'
        self.conn.execute('UPDATE trade_photos SET analysis=? WHERE id=?',
                          (analysis, 'shared'))
        before = self.conn.total_changes
        result = recall.history(self.db, 10, 20, {'trade_number': 1})
        source = deepcopy(result)
        text = '\n'.join(recall.messages(result))
        self.assertIn('Reported outcome: unknown', text)
        self.assertIn('execution details unconfirmed', text)
        self.assertIn(analysis, text)
        self.assertNotIn('Reported exit price: 101.25', text)
        self.assertEqual(result, source)
        self.assertEqual(self.conn.total_changes, before)

    def test_header_and_all_sections_fit_even_for_unicode_long_content(self):
        result = recall.history(self.db, 10, 20, {'trade_number': 1})
        result['selection_note'] = 'Selection note 🚀 ' * 300
        result['journals'][0]['summary'] = 'Saved word range by 0.2 🚀. ' * 400
        result['journals'][0]['metadata']['labels'] = ['First label', 'Second label']
        before = deepcopy(result)
        chunks = recall.messages(result)
        text = ''.join(chunk.removeprefix('Trade #1 journal (continued)\n') for chunk in chunks)
        self.assertEqual(result, before)
        self.assertTrue(all(0 < units(chunk) <= 1800 for chunk in chunks))
        self.assertIn('1. First label\n2. Second label', text)
        self.assertEqual(text.count('Saved word range by 0.2 🚀.'), 400)


if __name__ == '__main__':
    unittest.main()
