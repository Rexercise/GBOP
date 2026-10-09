"""Recursive member narration and shared prompts; synthetic local data only."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from gbop_voice_web import journal_coach as coach, journal_recall as recall
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, infer_tier
from gbop_voice_web.market_conversation import MarketConversation
from gbop_voice_web.voice_policy import build_voice_instructions
import test_contextual_journals as fixture

ROOT = Path(__file__).resolve().parents[1]
CANON = (ROOT / 'gbop_voice_web/gtop_soupier_soup.txt').read_text().strip()
RAW = ('Journal my 9ate8 entry story. I entered the Super Soup of the Model 1. '
       'Then I entered Soupieror Soup, treating that Super Soup candle as its CRT. '
       'I later entered what I called Superior Soup using the Soupier candle as '
       'another CRT. I also reported a separate Soupier Soup entry using the old name. '
       'These layers belong to the original 9ate8 range.')
STORY = {
    'play': '9ate8',
    'context_notes': 'Every nested layer remains tied to the original 9ate8 range.',
    'entries': [
        {'entry_index': 1, 'entry_model': 'Super Soup',
         'candle_label': 'Reported Super Soup candle',
         'notes': 'Member reported entry on the Super Soup of Model 1.'},
        {'entry_index': 2, 'entry_model': 'Soupieror Soup',
         'candle_label': 'Reported Soupieror Soup candle',
         'notes': 'Immediate parent CRT is the Super Soup candle from entry 1; '
                  'original parent is the 9ate8 range.'},
        {'entry_index': 3, 'entry_model': 'Superior Soup',
         'notes': 'Literal reported term. Immediate parent CRT is the Soupieror '
                  'Soup candle from entry 2; original parent is the 9ate8 range.'},
        {'entry_index': 4, 'entry_model': 'Soupier Soup',
         'notes': 'Literal former name retained for a separately reported fill; '
                  'original parent is the 9ate8 range.'},
    ],
}


class SoupierNarrativeTests(unittest.TestCase):
    setUp = fixture.ContextualJournalTests.setUp
    tearDown = fixture.ContextualJournalTests.tearDown
    review = fixture.ContextualJournalTests.review

    def journal_roundtrip(self, *, voice):
        # No market evidence is needed to preserve a member's reported identity.
        self.context.close()
        context = MarketConversation((10, 20, 'soupier-narration'),
                                    auth_provider=(self.db, 10, 20))
        self.addCleanup(context.close)
        context.begin_turn(None if voice else RAW)
        args = {'story_json': json.dumps(STORY)}
        if voice:
            args['raw_story'] = RAW
        staged = context.run('stage_journal_story', args,
            lambda name, values: coach.coach_tool(self.db, 10, 20, name, values))
        self.assertTrue(staged['ok'], staged)
        self.assertTrue(staged['persisted'])
        self.assertEqual(staged['story']['play'], '9ate8')
        self.assertEqual(staged['story']['entries'], STORY['entries'])
        self.assertIsNone(infer_tier(staged['story']['entries'][1]['entry_model']))
        context.begin_turn(None if voice else 'Save my journal.')
        args = {'draft_id': staged['draft_id']}
        if voice:
            args['confirmation_text'] = 'Save my journal.'
        saved = context.run('save_journal_story', args,
            lambda name, values: coach.coach_tool(self.db, 10, 20, name, values))
        self.assertTrue(saved['ok'], saved)
        self.assertEqual(saved['trade_number'], 1)
        self.assertIsNone(saved['result_r'])
        self.assertFalse(saved['execution_records_changed'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM thesis_executions').fetchone()[0], 0)
        raw = self.conn.execute('SELECT metadata FROM journal_details').fetchone()[0]
        stored = json.loads(json.loads(raw)['journal_story'])
        self.assertEqual(stored['entries'], STORY['entries'])
        self.assertEqual(stored['context_notes'], STORY['context_notes'])
        self.assertNotIn('result_r', stored)
        self.assertNotIn('market_review', json.loads(raw))
        context.close()
        fresh = MarketConversation((10, 20, 'soupier-recall'), auth_provider=(self.db, 10, 20))
        self.addCleanup(fresh.close)
        fresh.begin_turn('Read Trade #1.')
        result = fresh.run('get_journal_history', {'trade_number': 1},
            lambda name, values: recall.history(self.db, 10, 20, values))
        text = '\n'.join(recall.messages(result))
        for reported in ('9ate8', 'Entry 2: Soupieror Soup', 'Entry 3: Superior Soup',
                         'Entry 4: Soupier Soup',
                         'Immediate parent CRT', RAW, 'R unknown'):
            self.assertIn(reported, text)

    def test_typed_recursive_story_saves_with_unknown_risk_and_tier(self):
        self.journal_roundtrip(voice=False)

    def test_voice_literal_term_and_nested_lineage_survive_save_and_recall(self):
        self.journal_roundtrip(voice=True)


class SoupierPromptCompatibilityTests(unittest.TestCase):
    def test_discord_text_and_browser_backend_and_live_voice_include_canon(self):
        tree = ast.parse((ROOT / 'bot.py').read_text())
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'GTOP_AI_PROMPT'
                    for target in node.targets))
        prompt = eval(compile(ast.Expression(assignment.value), 'bot.py', 'eval'),
                      {'CANONICAL_KNOWLEDGE': CANONICAL_KNOWLEDGE})
        self.assertEqual(prompt.count(CANON), 1)
        tree = ast.parse((ROOT / 'gbop_voice_web/server.py').read_text())
        env = {'CANONICAL_KNOWLEDGE': CANONICAL_KNOWLEDGE, 'BACKEND_PROMPT': '',
               'LIVE_INSTRUCTIONS': '', 'LIVE_MARKET_PROMPT': '', 'LIVE_MIDPOINT_PROMPT': ''}
        # Evaluate the actual shared include assignments without starting a server.
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and any(isinstance(part, ast.Name) and part.id in
                            ('CANONICAL_KNOWLEDGE', 'GTOP_CANONICAL_KNOWLEDGE')
                            for part in ast.walk(node.value))):
                exec(compile(ast.Module(body=[node], type_ignores=[]), 'server.py', 'exec'), env)
        for name in ('BACKEND_PROMPT', 'LIVE_INSTRUCTIONS'):
            with self.subTest(path=name):
                self.assertEqual(env[name].count(CANON), 1)

    def test_primary_and_helper_voice_slots_share_recursive_journaling_prompt(self):
        tree = ast.parse((ROOT / 'bot.py').read_text())
        session = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == 'GBOPRealtimeSession')
        instructions = next(node for node in session.body if isinstance(node, ast.FunctionDef)
                            and node.name == 'instructions')
        env = {'CANONICAL_KNOWLEDGE': CANONICAL_KNOWLEDGE,
               'build_voice_instructions': build_voice_instructions, 'MARKET_PROMPT': '',
               'get_profile': lambda *args: {}, 'profile_context': lambda value: '',
               'market_clock': lambda: '', 'db': object(), 'GTOP_GUILD_ID': 10}
        exec(compile(ast.Module(body=[instructions], type_ignores=[]), 'bot.py', 'exec'), env)
        with patch('gbop_voice_web.midpoint_preferences.preference_context', return_value=''):
            for slot in ('primary', 'helper'):
                with self.subTest(slot=slot):
                    value = NS(member=NS(id=20), voice_client=NS(slot=slot), market_context=None)
                    self.assertEqual(env['instructions'](value).count(CANON), 1)
        manager = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == 'GBOPRealtimeManager')
        get_session = next(node for node in manager.body if isinstance(node, ast.AsyncFunctionDef)
                           and node.name == 'get_session')
        constructors = [node for node in ast.walk(get_session) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name) and node.func.id == 'GBOPRealtimeSession']
        self.assertEqual(len(constructors), 1)

    def test_prompt_preserves_reported_wording_and_saves_before_optional_clarification(self):
        for text in ('Stage literal entry_model', 'via stage_journal_story',
                     'parent play, candle_label', 'context_notes/entry notes',
                     'Finalize on request', 'Preserve raw terms',
                     '"Superior Soup" is a spoken alias only in clear recursive context',
                     'Naming/risk/tier never gate save',
                     'unknown risk/R/tier stay unknown'):
            self.assertIn(text, CANON)


if __name__ == '__main__':
    unittest.main()
