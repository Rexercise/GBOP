import os
import re
import psycopg


SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL", "").strip()

if not SUPABASE_DB_URL:
    raise RuntimeError(
        "SUPABASE_DB_URL is missing. Add the Supabase Session pooler "
        "connection string to Render Environment."
    )


class RowCompat(dict):
    """SQLite-like row: supports row['column'] and row[0]."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class CursorCompat:
    def __init__(self, cursor, prefetched=None, lastrowid=None):
        self._cursor = cursor
        self._prefetched = list(prefetched or [])
        self.lastrowid = lastrowid

    def _convert(self, raw):
        if raw is None:
            return None

        if isinstance(raw, dict):
            return RowCompat(raw)

        description = self._cursor.description or []
        names = [
            getattr(col, "name", None) or col[0]
            for col in description
        ]
        return RowCompat(zip(names, raw))

    def fetchone(self):
        if self._prefetched:
            return self._prefetched.pop(0)
        return self._convert(self._cursor.fetchone())

    def fetchall(self):
        rows = self._prefetched
        self._prefetched = []
        rows.extend(self._convert(row) for row in self._cursor.fetchall())
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class ConnectionCompat:
    AUTO_ID_TABLES = {
        "journals",
        "theses",
        "thesis_executions",
        "thesis_events",
        "risk_flags",
        "ai_messages",
    }

    def __init__(self):
        # prepare_threshold=None keeps this compatible with either Supabase
        # Session or Transaction pooler URLs.
        self._conn = psycopg.connect(
            SUPABASE_DB_URL,
            sslmode="require",
            prepare_threshold=None,
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False

    @staticmethod
    def _translate_schema(sql):
        # SQLite AUTOINCREMENT -> PostgreSQL serial integer.
        sql = re.sub(
            r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
            "SERIAL PRIMARY KEY",
            sql,
            flags=re.IGNORECASE,
        )

        # Discord IDs require 64-bit integers in PostgreSQL.
        sql = re.sub(
            r"\bguild_id\s+INTEGER\b",
            "guild_id BIGINT",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            r"\buser_id\s+INTEGER\b",
            "user_id BIGINT",
            sql,
            flags=re.IGNORECASE,
        )

        return sql

    @staticmethod
    def _replace_qmarks(sql):
        # This bot's SQL doesn't place literal '?' characters inside strings,
        # so direct placeholder translation is safe here.
        return sql.replace("?", "%s")

    def _pragma_table_info(self, sql):
        match = re.search(
            r"PRAGMA\s+table_info\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
            sql,
            flags=re.IGNORECASE,
        )
        if not match:
            return None

        table_name = match.group(1)

        cur = self._conn.execute(
            """
            SELECT
                column_name AS name,
                ordinal_position - 1 AS cid,
                data_type AS type,
                CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                column_default AS dflt_value,
                0 AS pk
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return CursorCompat(cur)

    def execute(self, sql, params=()):
        sql = str(sql).strip()

        # The surrounding "with db()" already provides one transaction.
        if sql.upper() == "BEGIN":
            cur = self._conn.cursor()
            return CursorCompat(cur)

        pragma = self._pragma_table_info(sql)
        if pragma is not None:
            return pragma

        sql = self._translate_schema(sql)
        sql = self._replace_qmarks(sql)

        # Emulate sqlite cursor.lastrowid for the tables where this bot uses it.
        insert_match = re.match(
            r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)",
            sql,
            flags=re.IGNORECASE,
        )

        wants_lastrowid = False
        if insert_match:
            table = insert_match.group(1).lower()
            wants_lastrowid = table in self.AUTO_ID_TABLES

        if wants_lastrowid and "RETURNING" not in sql.upper():
            sql = sql.rstrip().rstrip(";") + " RETURNING id"

        cur = self._conn.execute(sql, params or ())

        if wants_lastrowid:
            raw = cur.fetchone()
            lastrowid = raw[0] if raw else None
            return CursorCompat(cur, lastrowid=lastrowid)

        return CursorCompat(cur)


def db():
    return ConnectionCompat()