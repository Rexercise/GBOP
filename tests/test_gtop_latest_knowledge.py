"""Accepted naming and rule distinctions, not live trade/model evidence."""
import json
from pathlib import Path
import sqlite3
import unittest
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, infer_tier, tier_max_r, tier_used_r

class LatestKnowledgeTests(unittest.TestCase):
    def test_h4_names_retain_windows(self):
        for text in ('1 AM–5 AM: RCT — Rice Crown Transition.',
                     '5 AM–9 AM: London Lunch.', '9 AM–1 PM: New York AM / GCT.',
                     '1 PM–5 PM: New York PM.',
                     '5 PM–9 PM: CCAO — CBDR Closure, Asia Open (pronounced Cacao).',
                     '9 PM–1 AM: TCT — True Chow Time.', 'separate CBDR remains 2 PM–8 PM.'):
            self.assertIn(text, CANONICAL_KNOWLEDGE)
        for text in ('1 AM–5 AM: Asia Expansion.', '5 PM–9 PM: CBDR + Early Asia.',
                     '9 PM–1 AM: Asia Open.', 'No new risk tier has been specified'):
            self.assertNotIn(text, CANONICAL_KNOWLEDGE)

    def test_preferred_cisd_retains_shorthand(self):
        self.assertIn('CISD (Change in the State of Delivery) is the standard written term', CANONICAL_KNOWLEDGE)
        self.assertIn('CSD remains accepted shorthand', CANONICAL_KNOWLEDGE)
        self.assertEqual(infer_tier('CSD'), infer_tier('CISD'))

    def test_kod_sequence_does_not_override_entry_nature(self):
        self.assertIsNone(infer_tier('KOD Turtle Soup'))
        self.assertEqual(infer_tier('KOD Turtle Soup', 2), 2)
        self.assertEqual(infer_tier('KOD Souper Soup'), 1)
        self.assertEqual(infer_tier('KOD Super Soup'), 1)
        self.assertEqual(infer_tier('KOD Turtle Body Soup'), 2)
        self.assertEqual(infer_tier('KOD Turtle Wick Soup'), 2)
        self.assertNotIn('KOD is capped at Tier 2', CANONICAL_KNOWLEDGE)

    def test_protection_is_plan_specific_and_objective_based(self):
        self.assertIn('At approximately 80% of the predetermined objective', CANONICAL_KNOWLEDGE)
        self.assertIn('documented management plan, which may include breakeven', CANONICAL_KNOWLEDGE)
        self.assertNotIn('no mechanical breakeven rule replaces', CANONICAL_KNOWLEDGE)
        self.assertNotIn('commonly +0.20R to +0.30R', CANONICAL_KNOWLEDGE)

    def test_revision_ids_unique(self):
        p=Path(__file__).resolve().parents[1]/'gbop_voice_web/gtop_rule_revisions.json'
        rows=json.loads(p.read_text())
        self.assertEqual(len(rows), len({r['rule_id'] for r in rows}))
        fang=next(r for r in rows if r['rule_id']=='gtop-fang2-tier')
        self.assertIn('Tier 1 because it is pre-CISD', fang['accepted'])

    def test_fang_tier_correction_never_rewrites_history_or_allocations(self):
        conn=sqlite3.connect(':memory:')
        conn.execute('CREATE TABLE thesis_executions (thesis_id, entry_model, tier, risk_r)')
        conn.execute("INSERT INTO thesis_executions VALUES (1, 'Fang 2 Imbalance', 3, .1)")
        self.assertEqual(tier_used_r(lambda:conn,1,1),.1)
        self.assertEqual(conn.execute('SELECT tier FROM thesis_executions').fetchone()[0],3)
        self.assertEqual([tier_max_r(i) for i in (1,2,3)],[.6,.3,.1])
        self.assertEqual(infer_tier('Model 1 / Fang 1'),2)
        self.assertIsNone(infer_tier('Soupieror Soup after Super Soup'))

if __name__=='__main__':
    unittest.main()
