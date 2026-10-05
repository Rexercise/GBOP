"""Database consent/revocation gate shared by authenticated GBOP transports.

Discord role/OAuth checks remain the responsibility of the caller. This gate
adds fresh GBOP activation and revocation checks, never replaces role checks.
"""

MEMBER_ACCESS_UNAVAILABLE = 'GBOP access could not be verified. Please try again later.'


def member_access_error(db, guild_id: int, user_id: int, owner_id: int):
    """Return a public-safe denial, or None. Fail closed without leaking SQL."""
    if int(user_id) == int(owner_id) and int(owner_id) != 0:
        return None
    try:
        with db() as conn:
            row = conn.execute(
                'SELECT activated, leadership_ack, revoked FROM members '
                'WHERE guild_id=? AND user_id=?',
                (guild_id, user_id),
            ).fetchone()
        if row is None:
            return 'Activate GBOP in G.T.O.P with /activate agree:true after reviewing the privacy notice.'
        if row['revoked']:
            return 'Your GBOP access is revoked. Contact GTOP leadership.'
        if not row['activated'] or not row['leadership_ack']:
            return 'Review the GBOP privacy notice and activate with /activate agree:true in G.T.O.P.'
        return None
    except Exception:
        return MEMBER_ACCESS_UNAVAILABLE
