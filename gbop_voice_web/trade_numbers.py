"""Member-local display numbers; database identities remain private and unchanged."""


def trade_number(db, guild_id, user_id, trade_id):
    with db() as conn:
        rows = conn.execute(
            'SELECT id FROM theses WHERE guild_id=? AND user_id=? ORDER BY id',
            (guild_id, user_id),
        ).fetchall()
    return next((n for n, row in enumerate(rows, 1) if row['id'] == trade_id), None)


def trade_record_id(db, guild_id, user_id, number):
    if type(number) is not int or number < 1:
        return None
    with db() as conn:
        row = conn.execute(
            'SELECT id FROM theses WHERE guild_id=? AND user_id=? ORDER BY id LIMIT 1 OFFSET ?',
            (guild_id, user_id, number - 1),
        ).fetchone()
    return row['id'] if row else None



TRADE_NUMBERING_PROMPT = """
Trade numbering: every trade_id in trade tools is the member-local displayed
Trade #, never a database primary key. Use the current verified member state
and tool results over old conversation messages containing obsolete numbers.
Use that displayed number for lookup, entries, events, closing, and deletion.
Photo tools use the same number under trade_number. Never invent a trade number.
"""
