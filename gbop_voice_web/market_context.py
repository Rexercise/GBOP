"""GTOP paired-context presentation. Preserve physical prices and event chronology."""
from copy import deepcopy
from gbop_voice_web.candle_evidence import parse_time

PAIRINGS = {'XAUUSD': 'XAGUSD', 'XAGUSD': 'XAUUSD',
            'BTCUSD': 'ETHUSD', 'ETHUSD': 'BTCUSD',
            'NAS100': 'SPX', 'SPX': 'NAS100'}

LIFECYCLE_PROMPT = """
Use candle_lifecycle.purge_candles and recap.candle_timeline for local candle
identity, OHLC, wick/body type, separate CSD/Super Soup, retests and objectives.
Follow canonical evidence statuses; legacy entry_confirmed=false or
model1.csd_status=not_assessed cannot negate formation. Name the candle by its
OPENING time and timeframe; give its closing/confirmation time separately.
The 10 AM H1 candle closes at 11 AM, not the 11 AM candle. Keep identity,
confirmation, execution and parent-range invalidation separate.
Use paired_smt plus paired_context for gold/silver, Bitcoin/Ethereum and
NAS100/SPX. Include boneless_asset and each own objective_status in the main
recap BEFORE the overall verdict, not as an optional afterthought. A completed
bearish SMT 9ate8 and a failed local bullish attempt are independent outcomes.
For a boneless Model 1 question, verify the partner's assigned-timeframe body
purge with review_market_crt or inspect_market_candles, then inspect the boneless
candle at the identical opening/closing interval. That is its SMT-aligned Model 1
(identity_basis=smt_time_aligned), without inventing a local physical purge.
Do not substitute a later unrelated local Model 1 or gate identity on CSD.
A source SMT bar or wick alone does not establish an assigned body-purge time.
Missing SPX is not no SMT or US30. Never invent context unavailable at entry.
Preserve the working chronology, Super Soup cleanliness and separate nested
versus parent outcomes. Follow canonical candle naming even when a legacy raw
summary mentions only the closing time. Answer the requested fact first.
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
        'does not erase earlier SMT or delivery. Integrate these paired outcomes into the main shift recap; '
        'a failed local opposite-direction attempt does not erase completed SMT delivery. '
        'For SMT-aligned Model 1 identity, retrieve the partner body-purge candle on the assigned timeframe '
        'and the boneless candle at the identical opening/closing interval. This is identity_basis=smt_time_aligned, '
        'not an invented local body purge, CSD or member fill. Do not infer its time from a coarse SMT bar. '
        'Name all candles by opening time; state closing/confirmation time separately.')
    return review
