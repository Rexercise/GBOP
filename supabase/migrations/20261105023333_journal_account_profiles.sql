-- Private server-scoped journal account profiles. No browser grants.
CREATE TABLE IF NOT EXISTS journal_accounts (
        id TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
        name_key TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL,
        selected INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
        UNIQUE(guild_id,user_id,name_key));
CREATE INDEX IF NOT EXISTS journal_accounts_owner ON journal_accounts(guild_id,user_id);
ALTER TABLE journal_accounts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON journal_accounts FROM anon, authenticated;
