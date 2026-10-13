import unittest

from gbop_voice_web.text_presentation import clean_ss_report


class SSReportPresentationTests(unittest.TestCase):
    def test_removes_retired_comparison_preserving_range_evidence(self):
        for heading in ('SS Report', '**SS Report**', '## SS Report', '1. **SS Report**'):
            for line in (
                'Compared with the prior observed close: Closed above 100.',
                '- **Compared with the prior observed close**: Closed above 100.',
                '* **Compared with the prior observed close:** Closed above 100.',
            ):
                with self.subTest(heading=heading, line=line):
                    text = heading + '\n\n' + line + '\n\nHigh of the week: 110\nLow of the week: 90'
                    self.assertEqual(clean_ss_report(text), heading + '\n\nHigh of the week: 110\nLow of the week: 90')

    def test_other_journal_text_is_unchanged(self):
        text = 'My journal\nCompared with the prior observed close: my own note.\n\n\nReflection'
        self.assertEqual(clean_ss_report(text), text)

    def test_report_without_retired_line_keeps_facts(self):
        text = 'SS Report\nWick below: higher pricing.\nCoverage unverified.'
        self.assertEqual(clean_ss_report(text), text)
