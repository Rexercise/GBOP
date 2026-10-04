"""GTOP paired-context presentation. Preserve physical prices and event chronology."""
from copy import deepcopy
from gbop_voice_web.candle_evidence import parse_time
from gbop_voice_web.candle_naming import objective_identity
from gbop_voice_web.smt_reference import event_scope_summary

PAIRINGS = {'XAUUSD': 'XAGUSD', 'XAGUSD': 'XAUUSD',
            'BTCUSD': 'ETHUSD', 'ETHUSD': 'BTCUSD',
            'NAS100': 'SPX', 'SPX': 'NAS100'}

LIFECYCLE_PROMPT = """
Name every objective by buy-side, sell-side or 50% of the selected range, with
its opening/timeframe FIRST; optional price follows as this provider's quote.
Broker prices differ. Never use a naked number as the objective's identity.
Own objectives name the Model 1 candle/timeframe; other objectives name the selected range.
Parent/child CRT is only for requested fractal lineage, not a containment requirement.
Use candle_lifecycle/recap.candle_timeline; legacy entry_confirmed=false and
model1.csd_status=not_assessed never negate a verified candle identity.
Give the specific assigned Model 1 OPENING time and timeframe FIRST, then the
precise purge_source_interval/assigned_purge.source_purge M1 time when available.
A containing assigned candle is not automatically a body-purge Model 1 or CSD.
Use verified assigned_purge and model1.candles, never guess a candle opening.
Mappings: Monthly->Daily, Weekly->H4, Daily->H1, H4->M15, H1->M5; honor verified
overrides/CBDR6h->M30. Preserve fine source detail; timestamps identify bars, not ticks.
Say 'the closure of the 10 o’clock candle'. Give the closing timestamp only when requested.
For 'did Super Soup perform?', lead with its performance_summary: own Model 1
buy/sell-side or 50% delivery and timing versus local invalidation, THEN selected-range
objectives. Clean structure is not a win; later delivery cannot restore CRT validity.
Keep formation, CSD, retests, execution and selected-range invalidation distinct.
CISD: assigned close strictly below full LOW bearish, above full HIGH bullish; never body open.
Use paired_smt/paired_context for gold/silver, BTC/ETH and NAS100/SPX. Assess
qualified boneless_asset and each own objective_status BEFORE the overall verdict
is chosen; speak the named directional verdict first, then its supporting evidence.
Same setup-hour purges mean both bones: no boneless/SMT label or minute asynchrony
by default. Use setup_interval.qualified_smt; forming hours are provisional. Minute
detail only when asked. Local/paired objectives are independent outcomes; honor invalidation.
For boneless Model 1, use paired_model1.boneless_reference or verify the partner's
assigned body purge with review_market_crt/inspect_market_candles and the peer's
identical candle interval: identity_basis=smt_time_aligned, not a local purge.
Do not substitute a later unrelated local Model 1 or gate identity on CSD.
A source SMT bar/wick alone cannot establish assigned body-purge time.
Missing SPX is not no SMT or US30. Never invent context unavailable at entry.
Follow recap.evidence_precedence and spoken_summary. Bounded paired events must
not replace the local chronology; preserve earlier verified paired delivery.
""".strip()


def enrich_smt(review):
    review = deepcopy(review)
    if not review.get('ok') or not review.get('events'):
        return review
    invalidations = review.get('invalidating_closes_ny', {})
    step = review['precision_seconds']
    passages = []
    for event in review['events']:
        qualified = event.get('setup_interval', {}).get('qualified_smt', True)
        potential = event.get('setup_interval', {}).get('potential_smt', False)
        event['boneless_asset'] = event['nonconfirming_asset'] if qualified else None
        event['potential_boneless_asset'] = event['nonconfirming_asset'] if potential else None
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
                if review.get('anchor_start_ny'):
                    states[name].update(objective_identity(name, event['direction'], {
                        'start_ny': review['anchor_start_ny'], 'timeframe': review.get('anchor_timeframe', 'H1')}))
            event['objective_status'][asset] = states
        own = event['objective_status'].get(event['nonconfirming_asset'], {})
        complete = own.get('opposing_liquidity', {}).get('status') == 'objective_complete_while_range_valid'
        full_status = own.get('opposing_liquidity', {}).get('status', '')
        event['boneless_status'] = ('disqualified' if not qualified and not potential else
            'potential_setup' if potential else 'completed_delivery' if complete else
            'delivery_not_completed_before_invalidation' if full_status == 'not_completed_before_invalidation' else
            'delivery_pending' if full_status == 'pending_at_review_cutoff' else 'delivery_unverified')
        if not qualified and not potential:
            # Preserve raw observed prices/timing for deliberate inspection,
            # but do not turn minute asynchrony into recap/confluence evidence.
            continue
        bounded = event.get('scope', {}).get('status') in ('peer_caught_up', 'execution_candle_invalidated_anchor')
        context = 'early boundary divergence' if bounded else event['play_context']
        qualifier = 'potential boneless' if potential else 'boneless'
        observed_through = event.get('setup_interval', {}).get('observed_through_ny') or review.get('paired_through_ny')
        text = (f"Observed {event['direction']} {context}: {event['swept_asset']} visibly purged "
                f"its {event['side'].replace('_', ' ')} in the candle opening {event['bar_open_ny']}; "
                f"{event['nonconfirming_asset']} is the {qualifier} leg and had not taken its matching level "
                + (f"through {observed_through}." if observed_through else 'in the evaluated setup evidence.'))
        if potential:
            text += ' The setup interval is unfinished or incompletely covered; its final qualification remains unverified.'
        if bounded:
            text += ' ' + event_scope_summary(event)
            text += ' This later change bounds the nonpurging period; it does not erase an earlier qualified setup or delivery.'
        for asset, states in event['objective_status'].items():
            full = states.get('opposing_liquidity', {})
            mid = states.get('midpoint', {})
            if full.get('status') == 'objective_complete_while_range_valid':
                text += f" {asset} completed its {full.get('spoken_label', 'own opposing-liquidity objective')} in the candle opening {full['touch_bar_open_ny']}."
            elif mid.get('status') == 'objective_complete_while_range_valid':
                text += f" {asset} reached its {mid.get('spoken_label', 'own midpoint')}; its {full.get('spoken_label', 'full objective')} is {full.get('status', 'unverified').replace('_', ' ')}."
            else:
                text += f" {asset}'s {full.get('spoken_label', 'full objective')} is {full.get('status', 'unverified').replace('_', ' ')}."
        identity = event.get('paired_model1', {})
        if identity.get('status') == 'identified':
            ref = identity['boneless_reference']
            text += (f" {ref['asset']}'s SMT-inherited Model 1 is its {ref['timeframe']} candle opening "
                     f"{ref['bar_open_ny']}, matching {event['swept_asset']}'s body-purge candle. "
                     'This is a same-time reference, not a visible local purge or inherited CSD confirmation.')
        passages.append(text)
    review['spoken_summary'] = ' '.join(passages)
    review['response_contract'] = ('Use boneless_asset when setup_interval.qualified_smt is true; '
        'use potential_boneless_asset only as provisional when setup_interval.potential_smt is true. '
        'The nonpurging leg may be potential/pending boneless from setup; full delivery is a separate outcome. '
        'If both assets purge corresponding liquidity within the same setup interval, both have bones; '
        'different minutes do not establish boneless SMT. Forming/incomplete intervals are provisional. '
        'Do not mention disqualified minute timing in ordinary recaps or use it as confluence. Use objective_status '
        'for valid-thesis delivery; raw later touches may occur after invalidation. Later invalidation '
        'or peer catch-up never erases an earlier qualified setup or delivery. Keep provisional '
        'nonpurge evidence explicitly scoped to observed continuous candles, not missing future bars. '
        'Integrate these paired outcomes into the main shift recap; '
        'a failed local opposite-direction attempt does not erase completed SMT delivery. '
        'For SMT-aligned Model 1 identity, retrieve the partner body-purge candle on the assigned timeframe '
        'and the boneless candle at the identical opening/closing interval. This is identity_basis=smt_time_aligned, '
        'not an invented local body purge, CSD or member fill. Do not infer its time from a coarse SMT bar. '
        'Name candles by opening time; say the closure of that candle. Give closing timestamps only when requested.')
    return review
