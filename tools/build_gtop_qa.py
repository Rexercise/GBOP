"""Generate native Pine checks from shared cases and the actual Butterfly engine."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def literal(value):
    return json.dumps(value, ensure_ascii=False)


def qa_source(indicator, block):
    cases = json.loads((ROOT / 'rules/gtop_cases.json').read_text())
    engine = indicator[indicator.index('type ActiveRange\n'):indicator.index('// Return only a bounded snapshot')]
    lines = ['//@version=6', 'indicator("GTOP Shared Rules QA", overlay=true)',
             '// Generated. Uses the actual Butterfly lifecycle functions and shared cases.',
             'int historyKeep = 24', 'int autoLookback = 120', block, engine]
    checks = []
    for case in cases['scalar']:
        call = f'f_gtop_{case["predicate"]}({", ".join(map(literal, case["args"]))})'
        checks.append((case['id'], f'{call} == {literal(case["expected"])}'))
    for case in cases['lifecycle']:
        for mirrored in (False, True):
            name = case['id'] + ('_bullish' if mirrored else '_bearish')
            lines += [f'f_case_{name}() =>', '    ActiveRange r = ActiveRange.new(110, 90, 0, 1, 0, "QA")',
                      '    array<ActiveRange> history = array.new<ActiveRange>()']
            for n, (o, h, l, c) in enumerate(case['candles'], 2):
                if mirrored:
                    o, h, l, c = 200-o, 200-l, 200-h, 200-c
                binding = 'int reason = ' if n == len(case['candles']) + 1 else ''
                lines.append(f'    {binding}f_shift_observe(r, {o}, {h}, {l}, {c}, {n}, history)')
            # GBOP exposes observed structural labels, whereas Butterfly marks these
            # codes Potential until delivery. Compare V6 identity and qualification.
            lines += [f'    f_relevant(r) == {literal(case["qualified"])} and (reason == 1) == {literal(case["invalid"])} and r.resoup == {literal("V6" in case["codes"])} and r.resoupPending == {literal(case["v6_pending"])}', '']
            checks.append((name, f'f_case_{name}()'))
    lines += ['f_case_own_timeframe() =>',
              '    ActiveRange r = ActiveRange.new(110, 90, 0, 1, 0, "H4", upperPurged=true, highTaken=true, closedBackInside=true, retiredReason=2, retiredAt=2, h1Delivery=true, deliverySourceClosed=false)',
              '    array<ActiveRange> history = array.from(r)',
              '    f_follow_retired(history, 109, 89, 106, 3, true)',
              '    bool h1Ignored = na(r.doublePurgeAt) and not r.deliverySourceClosed',
              '    f_follow_retired(history, 112, 88, 111, 4, true)',
              '    bool h1CannotInvalidate = not r.followStopped',
              '    f_follow_retired(history, 112, 88, 90, 5)',
              '    bool edgePending = na(r.doublePurgeAt) and not r.followStopped',
              '    f_follow_retired(history, 109, 94, 100, 6)',
              '    h1Ignored and h1CannotInvalidate and edgePending and r.doublePurgeAt == 6 and r.deliverySourceClosed', '',
              'f_case_delivery_candle_double_purge() =>',
              '    ActiveRange r = ActiveRange.new(110, 90, 0, 1, 0, "H1")',
              '    array<ActiveRange> history = array.new<ActiveRange>()',
              '    f_shift_observe(r, 106, 112, 104, 106, 2, history)',
              '    f_shift_observe(r, 106, 109, 89, 100, 3, history)',
              '    array.size(history) == 1 and array.get(history, 0).doublePurgeAt == 3', '']
    checks += [('own_timeframe', 'f_case_own_timeframe()'),
               ('delivery_candle_double_purge', 'f_case_delivery_candle_double_purge()')]
    for source, target in json.loads((ROOT / 'rules/gtop_rules.json').read_text())['assigned_timeframes'].items():
        spec = json.loads((ROOT / 'rules/gtop_rules.json').read_text())
        checks.append(('mapping_' + source, f'f_gtop_assigned_tf("{spec["pine_timeframes"][source]}") == "{spec["pine_timeframes"][target]}"'))
    # Selection must not mistake a historically qualified invalidated record
    # for an active range. Exercise the production selector with both present.
    eligibility = indicator[indicator.index('f_evidence_visible('):indicator.index('f_has_active_evidence(')]
    selection = indicator[indicator.index('f_evidence_range('):indicator.index('f_evidence_clock(')]
    lines += ['string evidenceTF = "240"', 'int evidenceAnchor = 0',
              'bool evidenceHistory = false', 'bool historyOn = false', 'string evidenceFocus = "Explicit parent layer"',
              'string highestEvidenceTF = "240"', eligibility, selection,
              'f_case_active_before_history() =>',
              '    ActiveRange old = ActiveRange.new(110, 90, 1, 2, 0, "Old", upperPurged=true, closedBackInside=true, sourceTF="240", retiredReason=1)',
              '    ActiveRange active = ActiveRange.new(110, 90, 3, 4, 0, "Active", lowerPurged=true, closedBackInside=true, sourceTF="240")',
              '    array<ActiveRange> rows = array.from(old, active)',
              '    ActiveRange chosen = f_evidence_range(rows)',
              '    chosen.begins == 3 and chosen.retiredReason == 0', '']
    checks.append(('active_before_history', 'f_case_active_before_history()'))
    candle_type = indicator[indicator.index('type EvidenceCandle\n'):indicator.index('f_evidence_bars()')]
    assigned = indicator[indicator.index('f_assigned_evidence('):indicator.index('f_json_time(')]
    lines += [candle_type, assigned,
              'f_case_child_failure_preserves_parent() =>',
              '    ActiveRange parent = ActiveRange.new(110, 90, 0, 10, 0, "H4", upperPurged=true, closedBackInside=true, sourceTF="240")',
              '    array<EvidenceCandle> bars = array.from(EvidenceCandle.new(10, 11, 109, 113, 108, 112), EvidenceCandle.new(11, 12, 112, 112, 107, 107))',
              '    [model, csd, failed, soup, primary, complete, through] = f_assigned_evidence(parent, bars, 12)',
              '    not na(model) and not na(csd) and not na(failed) and model.s == 10 and failed.e == 12 and primary and complete and na(parent.invalidAt) and parent.retiredReason == 0 and f_relevant(parent)', '']
    checks.append(('child_failure_preserves_parent', 'f_case_child_failure_preserves_parent()'))
    lines += ['f_case_shared_model_independent_parents() =>',
              '    ActiveRange a = ActiveRange.new(110, 90, 0, 10, 0, "A", lowerPurged=true, closedBackInside=true, sourceTF="240")',
              '    ActiveRange b = ActiveRange.new(108, 92, 1, 10, 0, "B", lowerPurged=true, closedBackInside=true, sourceTF="240")',
              '    array<EvidenceCandle> bars = array.from(EvidenceCandle.new(10, 11, 95, 96, 88, 89), EvidenceCandle.new(11, 12, 89, 100, 89, 98))',
              '    [ma, ca, sa, soupA, pa, fa, ta] = f_assigned_evidence(a, bars, 12)',
              '    [mb, cb, sb, soupB, pb, fb, tb] = f_assigned_evidence(b, bars, 12)',
              '    array<ActiveRange> both = f_evidence_ranges(array.from(b, a))',
              '    not na(ma) and not na(mb) and ma.s == mb.s and ca.s == cb.s and sa.s == sb.s and a.hi != b.hi and f_relevant(a) and f_relevant(b) and na(a.invalidAt) and na(b.invalidAt) and array.size(both) == 2 and array.first(both).begins == 0', '']
    checks.append(('shared_model_independent_parents', 'f_case_shared_model_independent_parents()'))
    lines += ['f_case_calendar_closure_and_missing_row() =>',
              '    ActiveRange parent = ActiveRange.new(110, 90, 0, 10, 0, "Monthly", upperPurged=true, forming=true, sourceTF="1M")',
              '    array<EvidenceCandle> bars = array.from(EvidenceCandle.new(12, 13, 109, 113, 108, 112, 10, 12), EvidenceCandle.new(15, 16, 112, 112, 107, 107, 13, 15))',
              '    [model, csd, failed, soup, primary, complete, through] = f_assigned_evidence(parent, bars, 16)',
              '    bool closureOK = not na(model) and not na(csd) and not na(failed) and primary and complete and csd.s == 15 and failed.e == 16 and parent.forming and not parent.closedBackInside and na(parent.invalidAt)',
              '    array<EvidenceCandle> brokenBars = array.copy(bars)',
              '    EvidenceCandle broken = array.get(bars, 1).copy()',
              '    broken.previousClose := 14',
              '    array.set(brokenBars, 1, broken)',
              '    [m2, c2, s2, soup2, p2, f2, t2] = f_assigned_evidence(parent, brokenBars, 16)',
              '    closureOK and not na(m2) and p2 and not f2 and na(c2) and na(s2)', '']
    checks.append(('calendar_closure_and_missing_row', 'f_case_calendar_closure_and_missing_row()'))
    # Each scenario owns its candles: mutating the same array between identical
    # Pine function calls can reuse the earlier call result in native execution.
    lines += ['f_case_retained_model_cisd(bool bearish) =>',
              '    ActiveRange parent = ActiveRange.new(110, 90, 0, 10, 0, "Daily", upperPurged=bearish, lowerPurged=not bearish, forming=true, sourceTF="1D")',
              '    EvidenceCandle model = bearish ? EvidenceCandle.new(12, 13, 109, 113, 108, 112) : EvidenceCandle.new(12, 13, 91, 92, 87, 88)',
              '    EvidenceCandle confirm = bearish ? EvidenceCandle.new(13, 14, 112, 112, 106, 107) : EvidenceCandle.new(13, 14, 88, 94, 88, 93)',
              '    array<EvidenceCandle> bars = array.from(model, confirm)',
              '    [m, c, failed, soup, primary, complete, through] = f_assigned_evidence(parent, bars, 14)',
              '    bool observed = not na(c) and not na(failed) and c.s == 13 and not primary and not complete and na(soup) and na(parent.invalidAt)',
              '    [m0, c0, f0, s0, p0, full0, t0] = f_assigned_evidence(parent, bars, 13)',
              '    bool closedOnly = not na(m0) and na(c0) and na(f0)',
              '    EvidenceCandle equal = confirm.copy()',
              '    equal.c := bearish ? model.l : model.h',
              '    array<EvidenceCandle> equalBars = array.from(model.copy(), equal)',
              '    [m1, c1, f1, s1, p1, full1, t1] = f_assigned_evidence(parent, equalBars, 14)',
              '    bool equalityPending = na(c1) and na(f1)',
              '    EvidenceCandle gap = confirm.copy()',
              '    gap.s := 14',
              '    gap.e := 15',
              '    array<EvidenceCandle> gapBars = array.from(model.copy(), gap)',
              '    [m2, c2, f2, s2, p2, full2, t2] = f_assigned_evidence(parent, gapBars, 15)',
              '    observed and closedOnly and equalityPending and na(c2) and na(f2)', '']
    checks.append(('retained_model_cisd_bear', 'f_case_retained_model_cisd(true)'))
    checks.append(('retained_model_cisd_bull', 'f_case_retained_model_cisd(false)'))
    follow_cases = json.loads((ROOT / 'rules/gtop_soup_followthrough_cases.json').read_text())
    fields = dict(cleanliness='cleanliness', function_status='functionStatus',
        max_adverse_points='points', max_adverse_percent='percent', model_boundary_points='modelPoints',
        outside_close_count='outsideCloses', coverage_complete='complete', degraded_at='degradedAt',
        equal_touch_at='equalTouchAt', target_open='targetOpen', window_end='windowEnd', terminal_bar_bound='terminalBound')
    for case in follow_cases['cases']:
        for mirror in (False, True):
            name = 'soup_' + case['id'] + ('_bull' if mirror else '_bear')
            def candle(row):
                s,e,o,h,l,c = row
                if mirror:
                    o,h,l,c = 200-o,200-l,200-h,200-c
                witness = next((r[1:] for r in case.get('calendar', []) if r[0] == s), [])
                return 'EvidenceCandle.new(' + ', '.join(map(literal, [s,e,o,h,l,c] + witness)) + ')'
            lines += [f'f_case_{name}() =>',
                '    ActiveRange r = ActiveRange.new(110, 90, 0, 10, 0, "QA", upperPurged=' + literal(not mirror) + ', lowerPurged=' + literal(mirror) + ', invalidAt=' + str(case.get('invalid_at', 'na')) + ')',
                '    EvidenceCandle model = ' + candle(follow_cases['model']),
                '    EvidenceCandle soup = ' + candle(follow_cases['soup']),
                '    array<EvidenceCandle> bars = array.from(' + ', '.join(candle(r) for r in case['bars']) + ')',
                '    SoupFollow q = f_soup_follow(r, model, soup, bars, ' + str(case['cutoff']) + ', true)',
                '    ' + ' and '.join('q.' + fields[k] + ' == ' + literal(v) for k,v in case['expect'].items()), '']
            checks.append((name, f'f_case_{name}()'))
    lines += ['var table result = table.new(position.top_center, 1, 1)', 'if barstate.islast', '    int passed = 0']
    for name, expression in checks:
        lines += [f'    if not ({expression})', f'        runtime.error("GTOP rule failed: {name}")', '    passed += 1']
    lines += ['    table.cell(result, 0, 0, "GTOP " + GTOP_RULESET_VERSION + " | " + str.tostring(passed) + " checks PASS", text_color=color.white, bgcolor=color.green)']
    return '\n'.join(lines) + '\n'
