"""Database consent/revocation gate shared by authenticated GBOP transports.

Discord role/OAuth checks remain the responsibility of the caller. This gate
adds fresh GBOP activation and revocation checks, never replaces role checks.
"""

import os
import re

MEMBER_ACCESS_UNAVAILABLE = 'GBOP access could not be verified. Please try again later.'
MAINTENANCE_MESSAGE = 'GBOP is temporarily available only to the owner during maintenance.'
PILOT_MESSAGE = 'GBOP is temporarily limited to the approved pilot.'


def maintenance_access_error(user_id, owner_id):
    """Reversible process configuration; never changes membership or journals.

    Unknown enabled values fail closed. Check before cached authorization and
    before reads/writes, including work already in flight.
    """
    owner_only = os.getenv('GBOP_OWNER_ONLY', '0').strip().lower()
    mode = os.getenv('GBOP_ACCESS_MODE', 'existing').strip().lower()
    if owner_only not in ('0', 'false', 'off', 'no', ''):
        mode = 'owner'
    if mode == 'existing':
        return None
    if mode not in ('owner', 'pilot'):
        return PILOT_MESSAGE
    try:
        trusted_owner = int(os.getenv('GTOP_OWNER_USER_ID') or owner_id)
        if trusted_owner <= 0:
            return PILOT_MESSAGE
        if trusted_owner > 0 and int(user_id) == trusted_owner:
            return None
        if mode == 'pilot':
            configured = os.getenv('GBOP_PILOT_USER_IDS', '').strip()
            allowed = [value.strip() for value in configured.split(',')] if configured else []
            # The list only narrows normal entitlement. Never accept names,
            # wildcard values, partial entries or malformed configuration.
            if not allowed or not all(re.fullmatch(r'[1-9][0-9]{0,19}', value)
                                      and int(value) < 2**64 for value in allowed):
                return PILOT_MESSAGE
            if int(user_id) in {int(value) for value in allowed}:
                return None
    except (TypeError, ValueError):
        pass
    return MAINTENANCE_MESSAGE if mode == 'owner' else PILOT_MESSAGE


def member_access_error(db, guild_id: int, user_id: int, owner_id: int):
    """Return a public-safe denial, or None. Fail closed without leaking SQL."""
    denial = maintenance_access_error(user_id, owner_id)
    if denial:
        return denial
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
