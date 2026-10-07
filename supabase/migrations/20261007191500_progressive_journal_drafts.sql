-- Prepared additive backend-only migration. Never backfill historical dates or
-- change journals, executions, member access, risk profiles or results here.
-- App rollback leaves durable unfinished narration intact; do not drop it.
CREATE TABLE IF NOT EXISTS public.journal_story_drafts (
    id TEXT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('unfinished','finalized')),
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    journal_id INTEGER REFERENCES public.journals(id) ON DELETE CASCADE,
    thesis_id INTEGER REFERENCES public.theses(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (guild_id,user_id) REFERENCES public.members(guild_id,user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS journal_story_drafts_owner
    ON public.journal_story_drafts(guild_id,user_id,status,updated_at);
ALTER TABLE public.journal_story_drafts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.journal_story_drafts FROM PUBLIC, anon, authenticated;
-- No browser-facing policies; the existing trusted backend connection owns access.
