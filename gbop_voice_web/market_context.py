"""GTOP paired-context presentation. Preserve physical prices and event chronology."""
from copy import deepcopy
from gbop_voice_web.candle_evidence import parse_time

PAIRINGS = {'XAUUSD': 'XAGUSD', 'XAGUSD': 'XAUUSD',
            'BTCUSD': 'ETHUSD', 'ETHUSD': 'BTCUSD',
            'NAS100': 'SPX', 'SPX': 'NAS100'}

LIFECYCLE_PROMPT = """
CANDLE LIFECYCLE — CURRENT BACKEND CONTRACT
Use candle_lifecycle.purge_candles for wick/body identification and exact candle
OHLC. A body_soup is the Model 1 candle; a wick_soup is Turtle Wick Soup, not a
body Model 1. Name its opening interval and the close at which it was identified
BEFORE discussing confirmation. CSD uses a later assigned-timeframe close through
that original body's opposite edge (its open), never a silently substituted wick.
csd, super_soup, body_reference_retest, body_disrespect_close and
objectives_after_formation/objectives_after_csd are separate timestamped facts.
A Super Soup requires a sweep and return inside the identified candle before CSD;
a return on the same confirming candle has unresolved pre-CSD ordering. Do not
upgrade it. A retest here tests the stated body-open price after CSD. The named
body-disrespect observation is a close beyond the original body's far edge; it
is NOT automatically a member stop or parent CRT invalidation. Also report any
post-CSD extreme breach separately. Parent invalidation uses its own timeframe.
Read the stated rule and price if a member asks what was disrespected; do not
invent their personal invalidation level. Report own midpoint versus full opposing
objective and preserve delivery before subsequent invalidation. Sequence gaps or
same-bar ambiguity must remain unverified, not absent. entry_confirmed=false and
legacy model1.csd_status=not_assessed cannot override the explicit lifecycle facts.
For full shifts, fold the relevant candle_timeline chapters into the recap in
range order; do not dump every candle or stop at the first failed range.
Automatic pairings are gold/silver, Bitcoin/Ethereum and NAS100/SPX. paired_smt
covers the opening 9ate8 comparison; paired_context covers later selected ranges.
Both histories must exist. Missing SPX is a missing feed, not no SMT and not US30.
When paired evidence is relevant, volunteer the purging asset, the boneless asset,
and EACH asset's own objective_status. 'Boneless' is an adjective for that asset
at that event, never a separate SMT type. Do not claim a literal local purge.
Only use context already observable at the member's entry when evaluating that
entry. Do not infer a deliberate trap or a guaranteed reversal from SMT.
""".strip()


def enrich_smt(review):
    review = deepcopy(review)
    if not review.get('ok') or not review.get('events'):
        return review
    invalidations = review.get('invalidating_closes_ny', {})
    step = review['precision_seconds']
    passages = []
    for event in review['events']:
        event['boneless_asset'] = event['nonconfirming_asset']
        event['boneless_is_asset_adjective'] = True
        event['local_purge_inferred_for_boneless_asset'] = False
        event['objective_status'] = {}
        for asset, objectives in event.get('objectives_after_divergence', {}).items():
            inv = parse_time(invalidations[asset]) if invalidations.get(asset) else None
            states = {}
            for name, objective in objectives.items():
                value = objective.get('first_later_touch_ny')
                touch = parse_time(value) if value else None
                same = objective.get('same_event_bar_touch_order_unknown', False)
                if not event.get('anchors_valid_at_event'):
                    status = 'context_only_anchor_invalid_at_smt'
                elif inv is not None and parse_time(event['bar_close_ny']) >= inv:
                    status = 'smt_in_invalidating_bar_order_unresolved'
                elif touch is not None and (inv is None or touch + step < inv):
                    status = 'objective_complete_while_range_valid'
                elif touch is not None and inv is not None and touch + step == inv:
                    status = 'touch_in_invalidating_bar_order_unresolved'
                elif same:
                    status = 'same_event_bar_order_unresolved'
                elif inv is not None:
                    status = 'not_completed_before_invalidation'
                elif review.get('paired_coverage_complete'):
                    status = 'pending_at_review_cutoff'
                else:
                    status = 'unverified_incomplete_paired_coverage'
                states[name] = {'status': status, 'level': objective['level'],
                                'touch_bar_open_ny': value,
                                'range_invalidated_at_ny': invalidations.get(asset),
                                'same_event_bar_touch_order_unknown': same}
            event['objective_status'][asset] = states
        text = (f"Observed {event['direction']} {event['play_context']}: {event['swept_asset']} visibly purged "
                f"its {event['side'].replace('_', ' ')} in the candle opening {event['bar_open_ny']}; "
                f"{event['boneless_asset']} was boneless at that point and had not taken its matching level.")
        for asset, states in event['objective_status'].items():
            full = states.get('opposing_liquidity', {})
            mid = states.get('midpoint', {})
            if full.get('status') == 'objective_complete_while_range_valid':
                text += f" {asset} completed its own opposing-liquidity objective at {full['level']} in the candle opening {full['touch_bar_open_ny']}."
            elif mid.get('status') == 'objective_complete_while_range_valid':
                text += f" {asset} reached its own midpoint; its full objective is {full.get('status', 'unverified').replace('_', ' ')}."
            else:
                text += f" {asset}'s full objective is {full.get('status', 'unverified').replace('_', ' ')}."
        text += ' These are paired market facts, not proof of a member execution.'
        passages.append(text)
    review['spoken_summary'] = ' '.join(passages)
    review['response_contract'] = ('Use boneless_asset as an event-specific asset adjective. Use objective_status '
        'for valid-thesis delivery; raw later touches may occur after invalidation. Later invalidation '
        'does not erase earlier SMT or delivery. No local body candle or member fill is implied.')
    return review
