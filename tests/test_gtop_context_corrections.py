"""Regression guard for the owner's Model 1, boneless and TAB terminology."""
import unittest
from pathlib import Path


class GtopContextCorrections(unittest.TestCase):
    def test_canonical_identity_and_accountability_contract(self):
        text = (Path(__file__).resolve().parents[1] / 'gbop_voice_web' / 'gtop_knowledge.txt').read_text()
        self.assertNotIn('MODEL 1 CANDIDATE / BODY-CLOSE LOGIC', text)
        self.assertNotIn('becomes the MODEL 1 CANDIDATE', text)
        self.assertNotIn('Model 1 confirms only', text)
        self.assertIn('An unconfirmed or failed Model 1 is still the Model 1 candle', text)
        self.assertIn('Boneless is an ADJECTIVE', text)
        self.assertIn('TAB — TRADING ACCOUNTABILITY BUDDY', text)
        self.assertIn('A post-entry SMT cannot be described', text)
        self.assertIn('WARN + SAVE', text)
        self.assertIn('NAS100 with SPX', text)
        self.assertIn('H1 -> M5', text)
        self.assertIn('objective completion in the SMT context', text)
        self.assertIn('Missing data does not prove an untaken level', text)
        self.assertIn('do not imply continuous monitoring', text)


if __name__ == '__main__':
    unittest.main()
