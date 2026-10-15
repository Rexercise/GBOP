"""Generated from rules/gtop_rules.json; edit the rule source, then rebuild."""
RULESET_VERSION = '1.3.0'
RULESET_SHA256 = '2a9f4ec9b85b58f1c8103d8782ac4e696cee4277237195578148bc59a0af9980'
TIMEZONE = 'America/New_York'
ASSIGNED = {'MN1': 'D1', 'W1': 'H4', 'D1': 'H1', 'H4': 'M15', 'H1': 'M5'}
VARIANT_NAMES = {'V1': 'Textbook', 'V2': 'Pattern Trader’s Kryptonite', 'V3': 'extended distribution', 'V4': 'one inside bar', 'V5': 'multiple inside bars', 'V6': 're-soup'}
RULESET = {"version": RULESET_VERSION, "sha256": RULESET_SHA256}


def chart_contiguous(expected, starts, previous_close, projected_open):
    """GTOP-EVIDENCE-005; caller must establish candle/timeframe/coverage eligibility."""
    return starts == expected or (previous_close == expected and projected_open == starts and (starts > previous_close))


def inside(c, hi, lo):
    """GTOP-RANGE-002; caller must establish candle/timeframe/coverage eligibility."""
    return lo < c < hi


def outside(c, hi, lo):
    """GTOP-RANGE-003; caller must establish candle/timeframe/coverage eligibility."""
    return c > hi or c < lo


def at_boundary(c, hi, lo):
    """GTOP-RANGE-002; caller must establish candle/timeframe/coverage eligibility."""
    return c == hi or c == lo


def purged(h, l, level, buy_side):
    """GTOP-RANGE-001; caller must establish candle/timeframe/coverage eligibility."""
    return h > level if buy_side else l < level


def body_purge(o, h, l, c, level, buy_side):
    """GTOP-MODEL1-001; caller must establish candle/timeframe/coverage eligibility."""
    return o <= level < c or l < level < o < c if buy_side else c < level <= o or c < o < level < h


def cisd(c, model_hi, model_lo, bearish):
    """GTOP-CISD-001; caller must establish candle/timeframe/coverage eligibility."""
    return c < model_lo if bearish else c > model_hi


def inside_bar(h, l, hi, lo):
    """GTOP-VARIANT-004; caller must establish candle/timeframe/coverage eligibility."""
    return h <= hi and l >= lo


def v6_before_full(initial_return, swept_extreme, full_before, full_same_bar):
    """GTOP-VARIANT-V6-EXCLUSIVE; caller must establish candle/timeframe/coverage eligibility."""
    return initial_return and swept_extreme and (not full_before) and (not full_same_bar)


def soup_confirmation(o, h, l, c, hi, lo, bearish):
    """GTOP-SOUP-001; caller must establish candle/timeframe/coverage eligibility."""
    return h > hi and (lo < c < hi and l >= lo or (lo < o < hi and c < lo)) if bearish else l < lo and (lo < c < hi and h <= hi or (lo < o < hi and c > hi))


def adverse_extension(h, l, hi, lo, bearish):
    """GTOP-SOUP-003; caller must establish candle/timeframe/coverage eligibility."""
    return (h - hi if h > hi else 0.0) if bearish else lo - l if l < lo else 0.0


def extension_percent(points, hi, lo):
    """GTOP-SOUP-003; caller must establish candle/timeframe/coverage eligibility."""
    return 100.0 * points / (hi - lo) if hi > lo else 0.0
