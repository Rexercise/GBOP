import ast
from pathlib import Path
import sqlite3
import unittest
from gbop_voice_web.gtop_protocol import infer_tier, tier_max_r, tier_used_r, CANONICAL_KNOWLEDGE

ROOT = Path(__file__).resolve().parents[1]

class ProtocolTests(unittest.TestCase):
    def test_classification_and_ambiguous_entries(self):
        cases = {'Super Soup of Model 1': 1, 'Turtle Body Soup': 2, 'Model 1 / CSD': 2,
                 'CISD': 2, 'Turtle Wick Soup': 2, 'KOD Turtle Body Soup': 2,
                 'KOD Super Soup': 2, 'Breaker Block': 3, '88.7 OTE': 3, 'Blessed Thief': 3}
        for model, expected in cases.items():
            with self.subTest(model=model):
                self.assertEqual(infer_tier(model), expected)
                self.assertEqual(infer_tier(model, 1), expected)
        self.assertIsNone(infer_tier('Turtle Soup'))
        self.assertIsNone(infer_tier('SMT'))
        self.assertIsNone(infer_tier('Super Soup of a wick'))
        self.assertEqual(infer_tier('custom setup', 3), 3)

    def test_cumulative_budget_counts_misclassified_history(self):
        conn = sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE thesis_executions (thesis_id, entry_model, tier, risk_r)')
        conn.executemany('INSERT INTO thesis_executions VALUES (?,?,?,?)',
                         [(1, 'Model 1', 1, .2), (1, 'Turtle Body Soup', 2, .2),
                          (1, 'Blessed Thief', 3, .1), (2, 'Model 1', 2, .3)])
        self.assertAlmostEqual(tier_used_r(lambda: conn, 1, 2), .4)
        self.assertGreater(tier_used_r(lambda: conn, 1, 2), tier_max_r(2))

    def test_prompt_and_runtime_sources(self):
        # Evaluate actual Discord prompt assignment without starting Discord.
        tree = ast.parse((ROOT / 'bot.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'GTOP_AI_PROMPT' for t in n.targets))
        prompt = eval(compile(ast.Expression(node.value), 'prompt', 'eval'), {'CANONICAL_KNOWLEDGE': CANONICAL_KNOWLEDGE})
        self.assertIn('Young Lefty', prompt)
        self.assertIn('Fixed cumulative tier allocations', prompt)
        self.assertIn('BEFORE CSD', prompt)
        self.assertNotIn('Tier 1: confirmed', prompt)
        for name in ('bot.py', 'gbop_voice_web/server.py'):
            tree = ast.parse((ROOT / name).read_text())
            self.assertFalse(any(isinstance(n, ast.FunctionDef) and n.name in ('tier_max_r', 'ai_infer_tier', 'infer_tier') for n in tree.body))

if __name__ == '__main__':
    unittest.main()
