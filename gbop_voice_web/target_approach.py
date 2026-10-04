"""Target-relative proximity measurements, independent of trading classification.

CE, a PD array, a named high/low and other liquidity/key levels can all be
measured. Calling something relatively close is contextual GTOP terminology;
no numeric cutoff here creates an inducement, intent or probability claim.
"""
from copy import deepcopy
from decimal import Decimal
import math


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite price.')
    return Decimal(str(value))


def target_approach_measurement(target, closest_price, direction, *, reference_low=None,
                                reference_high=None, reference_boundary=None):
    """Measure a verified or observed extreme; the caller retains evidence status.

    Percent of full width and percent of the boundary-to-target path are
    different denominators. Neither percentage is an inducement threshold.
    Source window, completeness and touch-order verification stay with the
    enclosing evidence; this function cannot establish any of them.
    """
    if not isinstance(target, dict) or not isinstance(target.get('kind'), str) or not target['kind'].strip():
        raise ValueError('A named target identity is required.')
    if direction not in ('bullish', 'bearish'):
        raise ValueError('Approach direction must be bullish or bearish.')
    level = _number(target.get('level'), 'Target level')
    price = _number(closest_price, 'Closest observed price')
    gap = max(Decimal(0), level - price if direction == 'bullish' else price - level)
    out = {'target': deepcopy(target), 'direction': direction, 'closest_observed_price': closest_price,
           'gap_price_points': float(gap), 'distance_unit': 'provider_price_points',
           'touch_relation': 'no_touch_observed' if gap > 0 else 'at_or_through_target_observed',
           'inducement_classification': 'requires_qualitative_context',
           'numeric_inducement_threshold': None}
    if (reference_low is None) != (reference_high is None):
        raise ValueError('A full-range denominator needs both low and high.')
    if reference_low is not None:
        low, high = _number(reference_low, 'Reference low'), _number(reference_high, 'Reference high')
        if high <= low:
            raise ValueError('Reference high must be greater than low.')
        width = high - low
        out['full_range_reference'] = {'low': reference_low, 'high': reference_high,
            'denominator_price_points': float(width), 'gap_percent': float(gap / width * 100)}
    if reference_boundary is not None:
        boundary = _number(reference_boundary, 'Reference boundary')
        path = level - boundary if direction == 'bullish' else boundary - level
        if path <= 0:
            raise ValueError('Reference boundary must precede the target in the approach direction.')
        progress = max(Decimal(0), min(Decimal(100), (path - gap) / path * 100))
        out['boundary_to_target_reference'] = {'boundary_level': reference_boundary,
            'target_level': target['level'], 'denominator_price_points': float(path),
            'remaining_gap_percent': float(gap / path * 100),
            'progress_percent': float(progress)}
    return out


# Explicit owner-characterized historical examples, not a learned/automatic
# classifier. Require the complete identity and verified closest source bar.
_OWNER_EXAMPLES = (
    {'anchor_start_ny': '2026-10-02T08:00:00-04:00', 'low': 30692.34, 'high': 30951.08,
     'direction': 'bearish', 'level': 30821.71, 'closest_observed_price': 30829.47,
     'bar_open_ny': '2026-10-02T09:32:00-04:00'},
    {'anchor_start_ny': '2026-10-02T09:00:00-04:00', 'low': 30829.47, 'high': 30995.59,
     'direction': 'bullish', 'level': 30912.53, 'closest_observed_price': 30879.33,
     'bar_open_ny': '2026-10-02T11:52:00-04:00'},
)


def owner_inducement_example(asset, anchor, direction, fact):
    """Return an explicit contextual attribution only for a matching verified fact."""
    if asset != 'NAS100' or fact.get('status') not in ('closest_approach_verified',
            'not_observed_by_review_cutoff', 'not_observed_before_range_invalidation'):
        return None
    if fact.get('distance_price_points') is None:
        return None
    source = fact.get('closest_source_interval') or {}
    if source.get('precision_seconds') != 60:
        return None
    if anchor.get('timeframe', 'H1') != 'H1':
        return None
    for example in _OWNER_EXAMPLES:
        if (anchor.get('start_ny') == example['anchor_start_ny']
                and anchor.get('low') == example['low'] and anchor.get('high') == example['high']
                and direction == example['direction'] and fact.get('level') == example['level']
                and fact.get('closest_observed_price') == example['closest_observed_price']
                and source.get('bar_open_ny') == example['bar_open_ny']):
            return {'label': 'inducement', 'basis': 'explicit_owner_characterization',
                    'target': 'midpoint', 'general_numeric_threshold': None}
    return None


def inducement_clause(fact, direction):
    """Short presentation of an attributed, measured non-touch; no glossary."""
    if not fact.get('gtop_context') or direction not in ('bullish', 'bearish'):
        return ''
    gap = fact.get('distance_price_points')
    reference = fact.get('boundary_to_target_reference') or {}
    progress = reference.get('progress_percent')
    if gap is None or gap <= 0 or progress is None:
        return ''
    boundary = 'low' if direction == 'bullish' else 'high'
    return (f'BUT induced 50%: {gap:.2f} points short '
            f'(~{progress:.0f}% of the {boundary}-to-50% path)')
