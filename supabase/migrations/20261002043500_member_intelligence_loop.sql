-- Persistent SS weekly structure + living member coaching profile.
-- Backend-only tables: RLS enabled and browser roles revoked.

create table if not exists public.gbop_ss_weekly_reviews (
    guild_id bigint not null,
    user_id bigint not null,
    week_start date not null,
    asset text not null default 'General',
    weekly_candle text,
    closure_vs_previous text,
    high_day text,
    high_time text,
    high_launchpad text,
    high_details text,
    low_day text,
    low_time text,
    low_launchpad text,
    low_details text,
    structural_summary text,
    next_week_hypothesis text,
    hypothesis_invalidation text,
    over_leverage boolean,
    trade_limit_exceeded boolean,
    boredom_trades boolean,
    closed_too_early boolean,
    exited_too_late boolean,
    prediction_correct boolean,
    prediction_miss_reason text,
    structure_complete boolean not null default false,
    execution_review_complete boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id, week_start, asset)
);

create table if not exists public.gbop_coaching_observations (
    guild_id bigint not null,
    user_id bigint not null,
    source_key text not null,
    theme text not null,
    polarity smallint not null check (polarity in (-1, 1)),
    weight double precision not null default 1.0 check (weight > 0),
    note text not null default '',
    observed_at timestamptz not null,
    created_at timestamptz not null default now(),
    primary key (guild_id, user_id, source_key, theme, polarity)
);

create table if not exists public.gbop_coaching_controls (
    guild_id bigint not null,
    user_id bigint not null,
    theme text not null,
    retired_at timestamptz,
    note text not null default '',
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id, theme)
);

create index if not exists gbop_ss_owner_recent
    on public.gbop_ss_weekly_reviews (guild_id, user_id, week_start desc);

create index if not exists gbop_coach_obs_owner_recent
    on public.gbop_coaching_observations (guild_id, user_id, observed_at desc);

alter table public.gbop_ss_weekly_reviews enable row level security;
alter table public.gbop_coaching_observations enable row level security;
alter table public.gbop_coaching_controls enable row level security;

revoke all on table public.gbop_ss_weekly_reviews from anon, authenticated;
revoke all on table public.gbop_coaching_observations from anon, authenticated;
revoke all on table public.gbop_coaching_controls from anon, authenticated;

grant all on table public.gbop_ss_weekly_reviews to service_role;
grant all on table public.gbop_coaching_observations to service_role;
grant all on table public.gbop_coaching_controls to service_role;
