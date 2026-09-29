"""Owned trade deletion; caller supplies a single database transaction."""
def delete_trade_records(conn, guild_id, user_id, trade_id):
    params = (trade_id, guild_id, user_id)
    row = conn.execute(
        "SELECT id FROM theses WHERE id=? AND guild_id=? AND user_id=? FOR UPDATE", params
    ).fetchone()
    if row is None:
        raise ValueError("Linked trade was not found in your account. Nothing deleted.")
    counts = {}
    for table, label in (("risk_flags", "risk_flags"), ("thesis_events", "events"),
                         ("thesis_executions", "executions"), ("journals", "journals")):
        counts[label] = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE thesis_id=? AND guild_id=? AND user_id=?", params
        ).fetchone()[0]
        conn.execute(f"DELETE FROM {table} WHERE thesis_id=? AND guild_id=? AND user_id=?", params)
    conn.execute("DELETE FROM theses WHERE id=? AND guild_id=? AND user_id=?", params)
    return counts
