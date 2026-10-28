"""Exercise the production Pine consumer gate and check UI separation contracts."""
import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = (Path(__file__).resolve().parents[1] / 'indicators/GTOP_Butterfly_Tool.pine').read_text()


class ButterflyDisplayTests(unittest.TestCase):
    def test_hourly_focus_leads_with_shift_ranges_without_changing_daily_parent(self):
        match = re.search(r'f_evidence_auto_tf\((.*?)\) =>\n    ([^\n]+)', SOURCE)
        names = [p.strip().split()[-1] for p in match[1].split(',')]
        # Translate this simple right-associated Pine conditional expression,
        # then exercise the production selection rather than a copied selector.
        parts = re.split(r'\s*[?:]\s*', match[2])
        expression = parts[-1]
        for i in range(len(parts)-3, -1, -2):
            expression = f'({parts[i+1]} if {parts[i]} else {expression})'
        compiled = compile(ast.parse(expression, mode='eval'), '<Pine focus>', 'eval')
        for main, focus, shift, parent, expected in [
                ('60','Chart parent detail',True,'1D','60'),
                ('60','Chart parent detail',False,'1D','1D'),
                ('60','Highest relevant timeframe',True,'1D','1M'),
                ('1D','Chart parent detail',True,'1M','1M'),
                ('5','Chart parent detail',True,'60','60'),
                ('15','Chart parent detail',True,'240','240')]:
            values = [main,focus,shift,True,parent,'1M','1W']
            with self.subTest(values=values):
                self.assertEqual(eval(compiled, {'__builtins__': {}}, dict(zip(names, values))), expected)

    def test_visible_developing_parent_is_eligible_without_confirmation(self):
        expression = re.search(r'f_evidence_visible\(ActiveRange r\) =>\n    ([^\n]+)', SOURCE)[1]
        compiled = compile(ast.parse(expression, mode='eval'), '<Pine visible parent>', 'eval')
        for retired, forming, confirmed, expected in [
                (0, True, False, True), (0, False, True, True),
                (0, False, False, False), (1, True, False, False),
                (2, False, True, False), (3, True, True, False)]:
            r = SimpleNamespace(retiredReason=retired, forming=forming, closedBackInside=confirmed)
            with self.subTest(retired=retired, forming=forming, confirmed=confirmed):
                self.assertEqual(eval(compiled, {'__builtins__': {}},
                    {'r': r, 'f_relevant': lambda row: row.closedBackInside}), expected)
                self.assertEqual(r.closedBackInside, confirmed)
        for start, end in [('f_has_active_evidence(', '// Snapshot references'),
                           ('f_evidence_ranges(', 'f_evidence_clock('),
                           ('f_context_overview(', 'var table evidencePanel')]:
            self.assertIn('f_evidence_visible(r)', SOURCE[SOURCE.index(start):SOURCE.index(end)])

    def test_each_consumer_can_keep_evidence_alive_independently(self):
        # This pure Pine boolean expression is also valid Python. Evaluate the
        # actual source, not a separately maintained copy of its implementation.
        match = re.search(r'f_evidence_needed\((.*?)\) =>\n    ([^\n]+)', SOURCE)
        names = [p.strip().split()[-1] for p in match[1].split(',')]
        expression = ast.parse(match[2], mode='eval')
        self.assertTrue(all(isinstance(n, (ast.Expression, ast.BoolOp, ast.Or, ast.And, ast.Name, ast.Load))
                            for n in ast.walk(expression)))
        compiled = compile(expression, '<actual Pine evidence gate>', 'eval')
        cases = [([False]*7, False),
                 ([False, False, False, False, False, False, True], True),
                 ([True, False, False, False, False, False, False], True),
                 ([False, True, False, False, False, False, False], False),
                 ([False, False, True, True, True, True, False], False)]
        for event in range(2, 6):
            inputs = [False]*7; inputs[1] = inputs[event] = True
            cases.append((inputs, True))
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(eval(compiled, {'__builtins__': {}}, dict(zip(names, values))), expected)

    def test_panel_visibility_does_not_gate_transport_or_shared_scan(self):
        self.assertIn('if evidenceNeeded and requestParentEndpoint', SOURCE)
        self.assertIn('if evidenceNeeded and array.size(pool)', SOURCE)
        self.assertIn('(evidenceNeeded and record.sourceTF == evidenceTFChoice)', SOURCE)
        body = SOURCE[SOURCE.index('f_show_evidence() =>'):SOURCE.index('// RANGE ENGINE')]
        self.assertEqual(body.count('= f_assigned_evidence('), 1)
        self.assertIn('    if evidenceNeeded\n', body)
        self.assertIn('            if contextAlerts and barstate.isrealtime', body)
        self.assertEqual(body.count('array<ActiveRange> parents = f_evidence_ranges(rows)'), 1)

    def test_history_off_excludes_retired_range_labels_including_double_purges(self):
        # Evaluate the actual Pine eligibility expression with a qualified,
        # retired source whose remaining display requirements all pass.
        expression = re.search(r'bool historyEligible = ([^\n]+)', SOURCE)[1]
        compiled = compile(ast.parse(expression, mode='eval'), '<Pine history gate>', 'eval')
        for history in (False, True):
            for double_purge in (None, 150):
                r = SimpleNamespace(closedBackInside=True, sameCandleV2=False,
                                    doublePurgeAt=double_purge, sourceTF='240', retiredAt=100)
                values = dict(archived=True, historyOn=history, r=r, chartTF='15',
                              parentTF='240', evidenceEnd=200, na=lambda value: value is None)
                with self.subTest(history=history, double_purge=double_purge):
                    self.assertEqual(eval(compiled, {'__builtins__': {}}, values), history)

    def test_callouts_use_openings_and_super_soup_does_not_rename_model_identity(self):
        self.assertIn('EvidenceEvent.new(model.s,', SOURCE)
        self.assertIn('"Model 1 · "', SOURCE)
        self.assertIn('EvidenceEvent.new(csd.s,', SOURCE)
        self.assertNotIn('EvidenceEvent.new(modelInvalid.s,', SOURCE)
        self.assertIn('EvidenceEvent.new(soup.s,', SOURCE)
        self.assertIn('"Super Soup · " + evidenceAssignedName', SOURCE)
        self.assertIn('CISD confirmed · ', SOURCE)
        self.assertIn('Model 1 validated', SOURCE)
        self.assertIn('f_evidence_candle_name(model)', SOURCE)
        self.assertIn('time_tradingday[1]', SOURCE)
        self.assertNotIn('"Return · "', SOURCE)
        self.assertIn('Strict-inside return: ', SOURCE)
        self.assertIn('f_json_time(modelInvalid.e)', SOURCE)

    def test_panel_and_capture_share_the_chart_variant_formatter(self):
        panel = SOURCE[SOURCE.index('f_show_evidence() =>'):SOURCE.index('// RANGE ENGINE')]
        self.assertIn('string variantDisplay = f_range_variant_title(r)', panel)
        self.assertIn('string identity = shiftName + variantDisplay', panel)
        self.assertIn('primaryCertain, complete, variantDisplay)', panel)
        self.assertIn('"Other context · "', panel)


if __name__ == '__main__': unittest.main()
