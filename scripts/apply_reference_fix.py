from pathlib import Path
from textwrap import dedent
ROOT = Path('gbop_voice_web')
def replace(name, old, new):
    p = ROOT / name
    text = p.read_text()
    assert text.count(old) == 1, (name, old, text.count(old))
    p.write_text(text.replace(old, new))
p = ROOT / 'smt_evidence.py'
text = p.read_text()
head, sep, tail = text.rpartition('    return result\n')
assert sep and not tail.strip()
p.write_text(head + '    from gbop_voice_web.smt_reference import attach_paired_model1\n    return attach_paired_model1(result, data, anchors, timeframe)\n' + tail)
replace('smt_reference.py', 'before subsequent invalidation. No local initiating-side purge was required.', 'while the range was valid. No local initiating-side purge was required.')
extra = [
    "        identity = event.get('paired_model1', {})",
    "        if identity.get('status') == 'identified':",
    "            ref = identity['boneless_reference']",
    "            text += (f\" {ref['asset']}'s SMT-inherited Model 1 is its {ref['timeframe']} candle opening \"",
    "                     f\"{ref['bar_open_ny']}, matching {event['swept_asset']}'s body-purge candle. \"",
    "                     'This is a same-time reference, not a visible local purge or inherited CSD confirmation.')",
    "        text += ' These are paired market facts, not proof of a member execution.'",
]
replace('market_context.py', "        text += ' These are paired market facts, not proof of a member execution.'", '\n'.join(extra))
replace('market_context.py', "        'does not erase earlier SMT or delivery. No local body candle or member fill is implied.')", "        'does not erase earlier SMT or delivery. Use paired_model1.boneless_reference for the same-time '\n        'SMT-inherited Model 1 identity; no visible local body purge or member fill is implied.')")
replace('market_context.py', 'Name the candle first. Keep identity, confirmation, execution and parent-range', 'Name the candle by its OPENING time first; separately say when it closed. Use paired_model1.boneless_reference for the same-time inherited Model 1, not a later local opposite-direction candle. Keep identity, confirmation, execution and parent-range')
replace('market_context.py', 'Missing SPX is not no SMT or US30. Never invent context unavailable at entry.', 'Missing SPX is not no SMT or US30. Never invent context unavailable at entry. Prefer recap.paired_interpretation over a local-only failure headline; a verified earlier SMT objective remains delivered. No local purge is required on the boneless leg, but its own target touch must be verified.')
replace('market_data.py', 'from gbop_voice_web.market_watch import WATCH_PROMPT', 'from gbop_voice_web.market_watch import WATCH_PROMPT\nfrom gbop_voice_web.smt_reference import reconcile_paired_recap')
extra = [
    "                step, args.get('confirmation_timeframe')), bars, end, step)",
    "            peer = PAIRINGS.get(result['asset'])",
    "            if peer:",
    "                result['review']['paired_smt'] = paired_market_review(",
    "                    db, result['asset'], peer, start, end, args['anchor_timeframe'])",
    "        if name in ('review_market_session', 'review_market_crt'):",
    "            reconcile_paired_recap(result['review'], result['asset'])",
    "        return result",
]
replace('market_data.py', "                step, args.get('confirmation_timeframe')), bars, end, step)\n        return result", '\n'.join(extra))
replace('shift_narrative.py', 'from gbop_voice_web.candle_evidence import parse_time, summarize', 'from gbop_voice_web.candle_evidence import parse_time, summarize\nfrom gbop_voice_web.smt_reference import closing_candle')
replace('shift_narrative.py', "An hourly close outside invalidated {anchor_name} at {clock(row['invalidated_at_ny'])};", "The range {anchor_name} was invalidated by {closing_candle(row['invalidated_at_ny'])['spoken_label']};")
replace('shift_narrative.py', "{anchor_name} was invalidated by the hourly close at {clock(row['invalidated_at_ny'])}{suffix}.", "{anchor_name} was invalidated by {closing_candle(row['invalidated_at_ny'])['spoken_label']}{suffix}.")
replace('shift_narrative.py', "The later hourly close at {clock(row['invalidated_at_ny'])} invalidated that range after the recorded delivery.", "Later, {closing_candle(row['invalidated_at_ny'])['spoken_label']} invalidated that range after the recorded delivery.")
p = ROOT / 'gtop_boneless.txt'
p.write_text(p.read_text() + '''
OWNER CLARIFICATION — CANDLE NAMES AND SMT-INHERITED MODEL 1 — 3 OCTOBER 2026
Always name a candle by its OPENING time. The 10 AM H1 candle closes at 11 AM: say "the 10 AM candle's close, at 11 AM," not "the 11 AM candle invalidated it." The closing clock time is an event timestamp, not the candle's name. Apply this distinction to Model 1, CSD, invalidation, retests, Super Soup and delivery, across day/night shifts and timeframes. Preserve New York chart time unless another chart timezone is explicitly requested.
In verified, time-aligned SMT, the boneless asset's Model 1 reference is its OWN candle on the SAME assigned timeframe and at the SAME opening timestamp as the correlated partner's qualifying BODY-purge Model 1 candle. Call it the SMT-inherited Model 1 candle. A missing local purge does not disqualify this reference. For example ONLY: a silver 9:15 AM M5 body-purge Model 1 maps to gold's 9:15 AM M5 candle, even though gold did not purge its corresponding high. Use gold's own OHLC and body reference, never silver's prices. An earlier wick divergence alone does not manufacture a body-purge Model 1. Missing same-time candles mean unverified, never a nearest-candle substitute.
Distinguish this inherited reference from any later local Model 1 in the opposite direction. Report the reference candle first; assess CSD, Super Soup, retests and objectives independently on the asset under review. Do not copy the partner's CSD, entry or outcome. The inherited identity exists without those later confirmations.
A local-only reading can appear to fail or lack an initiating purge while the verified paired account is an SMT-supported 9ate8 that delivered. Explicitly reconcile the two: the partner supplied the directional purge context and the boneless leg reached its own opposing liquidity. Do not lead with overall "failed objectives" when the paired evidence verifies earlier delivery. A later opposite-direction local setup failing is a separate event, not failure of the earlier SMT 9ate8. The absence of a local initiating-side purge is not the absence of an objective. Still verify the boneless asset's own target touch and chronology; SMT is not automatic success and the partner delivering does not prove this asset delivered. Keep midpoint-only, full delivery, unresolved ordering, invalidation and missing data distinct.
''')
workflow = Path('.github/workflows/apply-smt-reference-fix.yml').read_text()
tests = workflow.split("cat > tests/test_smt_reference.py <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
Path('tests/test_smt_reference.py').write_text(dedent(tests) + '\n')
