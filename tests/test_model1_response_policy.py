"""Prevent response policy from reintroducing the obsolete Model 1 identity gate."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Model1ResponsePolicyTests(unittest.TestCase):
    def test_market_and_live_policy_do_not_require_candidate_confirmation(self):
        text = (ROOT / 'gbop_voice_web/market_data.py').read_text()
        self.assertNotIn('Model 1 candidate', text)
        self.assertNotIn('confirmed Model 1/CSD', text)
        self.assertIn('entry_confirmed=false never negates an identified Model 1 candle', text)
        self.assertIn('model1.candles identification', text)

    def test_knowledge_refers_to_real_market_tools(self):
        text = (ROOT / 'gbop_voice_web/gtop_knowledge.txt').read_text()
        self.assertIn('review_market_crt or inspect_market_candles', text)
        self.assertNotIn('get_crt_review', text)
        self.assertNotIn('get_candle_data', text)


if __name__ == '__main__':
    unittest.main()
