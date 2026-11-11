"""Owner-defined entry method: explicit Tier 1 correction, unchanged risk allocations."""
import json
from pathlib import Path
import unittest
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, infer_tier
from gbop_voice_web import journal_coach as coach, journal_recall as recall
from gbop_voice_web.market_conversation import MarketConversation
import test_contextual_journals as fixture


class FangKnowledgeTests(unittest.TestCase):
    def test_shared_knowledge_includes_owner_definition_once(self):
        text = (Path(__file__).resolve().parents[1] / 'gbop_voice_web/gtop_fang2.txt').read_text().strip()
        self.assertEqual(CANONICAL_KNOWLEDGE.count(text), 1)
        # Keep the newly requested definition bounded without repeating the
        # existing general journaling policy or truncating earlier GTOP canon.
        self.assertLessEqual(len(text), 1200)
        for phrase in ('after Super Soup, before CISD', 'CREATE an imbalance',
                       'WITHIN that candle', 'Tier 1 because it is pre-CISD',
                       'Concept questions alone never start a journal'):
            self.assertIn(phrase, text)

    def test_explicit_owner_tier_replaces_unspecified_classification(self):
        for model in ('Fang 2 Imbalance after Super Soup', 'Fang two inverse Model 1',
                      'FANG-2 imbalance', 'fang2 imbalance'):
            with self.subTest(model=model):
                self.assertEqual(infer_tier(model), 1)
                self.assertEqual(infer_tier(model, 3), 1)
                self.assertEqual(infer_tier(model, 99), 1)

    def test_existing_models_keep_classification(self):
        self.assertEqual(infer_tier('Super Soup'), 1)
        self.assertEqual(infer_tier('Model 1 / Fang 1'), 2)
        self.assertEqual(infer_tier('Blessed Thief'), 3)


class FangJournalTests(unittest.TestCase):
    setUp = fixture.ContextualJournalTests.setUp
    tearDown = fixture.ContextualJournalTests.tearDown
    review = fixture.ContextualJournalTests.review

    def test_reported_entry_and_internal_imbalance_survive_voice_save_recall(self):
        raw = ('Journal my NAS 988. I entered Fang 2 after the Super Soup, before CISD. '
               'Actually the imbalance was inside Fang 2, not created by that whole candle. '
               'That was one entry, still open; objective pending.')
        story = {'asset': 'NAS100', 'play': '9ate8', 'entries': [
            {'entry_index': 1, 'entry_model': 'Fang 2 Imbalance',
             'notes': 'Imbalance inside Fang 2; after Super Soup, before CISD. '
                      'One filled entry remains open; objective pending.'}]}
        context = MarketConversation((10, 20, 'fang2-test'), auth_provider=(self.db, 10, 20))
        self.addCleanup(context.close)
        context.begin_turn(None)
        staged = context.run('stage_journal_story',
            {'story_json': json.dumps(story), 'raw_story': raw},
            lambda name, values: coach.coach_tool(self.db, 10, 20, name, values))
        self.assertTrue(staged['ok'], staged)
        context.begin_turn(None)
        saved = context.run('save_journal_story',
            {'draft_id': staged['draft_id'], 'confirmation_text': 'Save it.'},
            lambda name, values: coach.coach_tool(self.db, 10, 20, name, values))
        self.assertTrue(saved['ok'], saved)
        self.assertIsNone(saved['result_r'])
        context.begin_turn('Read Trade #1.')
        result = context.run('get_journal_history', {'trade_number': 1},
            lambda name, values: recall.history(self.db, 10, 20, values))
        readback = '\n'.join(recall.messages(result))
        for phrase in (raw, 'Fang 2 Imbalance', 'Imbalance inside Fang 2', 'objective pending'):
            self.assertIn(phrase, readback)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
