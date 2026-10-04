-- Additive, backend-only SS facts and member-authored versioned contributions.
-- No existing table, policy, grant or member eligibility is changed.
-- Application rollback leaves these durable records in place; do not drop data.
CREATE TABLE IF NOT EXISTS public.gbop_ss_reports (
    asset TEXT NOT NULL CHECK (asset IN ('NAS100','SPX','US30','XAUUSD','XAGUSD','BTCUSD','ETHUSD','EURUSD','WTI')),
    week_start DATE NOT NULL CHECK (EXTRACT(ISODOW FROM week_start) = 1),
    report_version TEXT NOT NULL CHECK (length(report_version) BETWEEN 1 AND 100),
    revision INTEGER NOT NULL CHECK (revision > 0),
    generated_at TIMESTAMPTZ NOT NULL,
    payload TEXT NOT NULL CHECK (octet_length(payload) <= 262144 AND jsonb_typeof(payload::jsonb) = 'object'),
    PRIMARY KEY (asset, week_start, report_version),
    UNIQUE (asset, week_start, revision)
);
CREATE INDEX IF NOT EXISTS gbop_ss_reports_latest
    ON public.gbop_ss_reports(asset,week_start,revision DESC);
CREATE TABLE IF NOT EXISTS public.gbop_ss_contributions (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    asset TEXT NOT NULL,
    week_start DATE NOT NULL,
    report_version TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    answers TEXT NOT NULL CHECK (octet_length(answers) <= 262144 AND jsonb_typeof(answers::jsonb) = 'object'),
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (guild_id,user_id,asset,week_start,report_version,revision),
    FOREIGN KEY (asset,week_start,report_version)
        REFERENCES public.gbop_ss_reports(asset,week_start,report_version)
);
CREATE INDEX IF NOT EXISTS gbop_ss_contributions_member_recent
    ON public.gbop_ss_contributions(guild_id,user_id,asset,week_start,created_at DESC,revision DESC);
ALTER TABLE public.gbop_ss_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.gbop_ss_contributions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.gbop_ss_reports FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.gbop_ss_contributions FROM PUBLIC, anon, authenticated, service_role;
-- Only the existing trusted backend role can read/append; browser roles have
-- no grants and no policies. No new identity, credential or persistent access.
GRANT SELECT, INSERT ON public.gbop_ss_reports TO service_role;
GRANT SELECT, INSERT ON public.gbop_ss_contributions TO service_role;
