"""Member-local display numbers; database identities remain private and unchanged."""


def journal_number(db, guild_id, user_id, journal_id):
    with db() as conn:
        rows = conn.execute(
            'SELECT id FROM journals WHERE guild_id=? AND user_id=? ORDER BY id',
            (guild_id, user_id),
        ).fetchall()
    return next((n for n, row in enumerate(rows, 1) if row['id'] == journal_id), None)


def journal_record_id(db, guild_id, user_id, number):
    if type(number) is not int or number < 1:
        return None
    with db() as conn:
        row = conn.execute(
            'SELECT id FROM journals WHERE guild_id=? AND user_id=? ORDER BY id LIMIT 1 OFFSET ?',
            (guild_id, user_id, number - 1),
        ).fetchone()
    return row['id'] if row else None

