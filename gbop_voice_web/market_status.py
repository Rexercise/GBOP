"""Describe observed feed freshness without inventing broker-session evidence."""


def broker_session_status():
    """The current collector sends no verified, dated quote/trade calendar.

    Keep evidence absent instead of turning a weekday, old tick, trade_mode,
    connected terminal, or a link to broker documentation into a closure claim.
    """
    return {'status': 'unknown', 'source': None, 'closure_reason': None,
            'reason': 'no_verified_session_calendar'}


def feed_health(capture_age=None, tick_age=None):
    """Freshness applies to this asset's latest snapshot, not historical gaps."""
    def state(age):
        if age is None:
            return 'absent'
        return 'future_timestamp' if age < 0 else 'fresh' if age <= 120 else 'stale'

    capture, quote = state(capture_age), state(tick_age)
    if 'future_timestamp' in (capture, quote):
        status = 'timestamp_ahead'
        message = 'A feed timestamp is ahead of server time; live status is unverified.'
    elif capture == 'absent':
        status = 'no_snapshot'
        message = 'No broker snapshot has been received for this asset; the cause is unverified.'
    elif capture == 'stale':
        status = 'stale_snapshot'
        message = 'No recent broker snapshot was received for this asset; the cause is unverified.'
    elif quote != 'fresh':
        status = 'recent_snapshot_stale_quote'
        message = ('A recent broker snapshot was received, but its last quote is old. '
                   'This does not prove market closure or identify a feed failure.')
    else:
        status = 'recent_snapshot_and_quote'
        message = 'The latest broker snapshot and quote are recent.'
    return {'status': status, 'capture_status': capture, 'quote_status': quote,
            'message': message}
