"""Persistent SS structure memory and living member coaching intelligence.

This module keeps GTOP's Weekly Structure Study separate from performance stats,
then turns saved SS, journals, risk flags, and post-shift reflections into a
recency-weighted coaching focus for each member.
"""
from __future__ import annotations

import math
import re
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from gbop_voice_web.trade_photos import schema, STR
from gbop_voice_web.risk_profiles import get_profile
from gbop_voice_web.adherence import adherence_bucket as _adherence_bucket

EASTERN = ZoneInfo("America/New_York")
HALF_LIFE_DAYS = 14.0
SOURCE_LOOKBACK_DAYS = 45

_INIT_LOCK = threading.Lock()
_INITIALIZED = False

TEXT_NULL = {"type": ["string", "null"]}
BOOL_NULL = {"type": ["boolean", "null"]}

THEME_LABELS = {
    "leverage": "over-leverage",
    "trade_limit": "trade-limit discipline",
    "boredom": "boredom trading",
    "fomo": "FOMO/chasing",
    "revenge": "revenge trading",
    "exit_timing_early": "closing too early",
    "exit_timing_late": "exiting too late",
    "plan_adherence": "plan adherence",
    "setup_quality": "A+ setup discipline",
    "risk_protocol": "risk-protocol discipline",
    "weekly_prediction": "weekly prediction process",
}

ISSUE_REMINDERS = {
    "leverage": "Keep leverage inside your saved risk protocol; conviction does not expand the thesis budget.",
    "trade_limit": "Respect your personal trade limit. Extra activity is not a reason to manufacture another trade.",
    "boredom": "No boredom trades. If the setup is not there, staying flat is part of the plan.",
    "fomo": "Do not chase after the move or purge. Wait for your defined execution.",
    "revenge": "Do not take a recovery trade after a loss. The next setup still has to earn entry.",
    "exit_timing_early": "Let the planned objective and invalidation manage the trade instead of cutting it early.",
    "exit_timing_late": "Respect the planned exit and protection rules; do not turn a completed idea into an unnecessary hold.",
    "plan_adherence": "Trade the written plan instead of rewriting it mid-shift.",
    "setup_quality": "Protect your own A+ criteria and do not downgrade the setup just to participate.",
    "risk_protocol": "Keep every execution inside the remaining tier allocation and total thesis risk budget.",
    "weekly_prediction": "Keep the weekly thesis conditional and tied to structure/invalidation; review why the prior read missed.",
}

STRENGTH_REMINDERS = {
    "leverage": "Keep the same leverage discipline you have been recording.",
    "trade_limit": "Keep respecting your personal trade limit.",
    "boredom": "Keep the same discipline around avoiding boredom trades.",
    "fomo": "Keep waiting for your execution instead of chasing.",
    "revenge": "Keep separating the next setup from the previous result.",
    "exit_timing_early": "Keep letting your planned objective/invalidation do the management work.",
    "exit_timing_late": "Keep respecting planned exits and protection.",
    "plan_adherence": "Keep repeating the same plan adherence.",
    "setup_quality": "Keep protecting your own A+ setup criteria.",
    "risk_protocol": "Keep the same risk-protocol discipline.",
    "weekly_prediction": "Keep separating observed weekly structure from the forward-looking hypothesis.",
}

ALIASES = {
    "over leverage": "leverage",
    "over-leverage": "leverage",
    "oversizing": "leverage",
    "size": "leverage",
    "too many trades": "trade_limit",
    "overtrading": "trade_limit",
    "trade limit": "trade_limit",
    "boredom trade": "boredom",
    "boredom": "boredom",
    "fomo": "fomo",
    "chasing": "fomo",
    "revenge": "revenge",
    "revenge trading": "revenge",
    "early exits": "exit_timing_early",
    "closed too early": "exit_timing_early",
    "closing too early": "exit_timing_early",
    "late exits": "exit_timing_late",
    "exited too late": "exit_timing_late",
    "held too long": "exit_timing_late",
    "plan": "plan_adherence",
    "plan adherence": "plan_adherence",
    "a+": "setup_quality",
    "setup quality": "setup_quality",
    "risk": "risk_protocol",
    "risk protocol": "risk_protocol",
    "weekly prediction": "weekly_prediction",
}

SCHEMA_SQL = [
    """CREATE TABLE IF NOT EXISTS gbop_ss_weekly_reviews (
        guild_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        week_start DATE NOT NULL,
        asset TEXT NOT NULL DEFAULT 'General',
        weekly_candle TEXT,
        closure_vs_previous TEXT,
        high_day TEXT,
        high_time TEXT,
        high_launchpad TEXT,
        high_details TEXT,
        low_day TEXT,
        low_time TEXT,
        low_launchpad TEXT,
        low_details TEXT,
        structural_summary TEXT,
        next_week_hypothesis TEXT,
        hypothesis_invalidation TEXT,
        over_leverage BOOLEAN,
        trade_limit_exceeded BOOLEAN,
        boredom_trades BOOLEAN,
        closed_too_early BOOLEAN,
        exited_too_late BOOLEAN,
        prediction_correct BOOLEAN,
        prediction_miss_reason TEXT,
        structure_complete BOOLEAN NOT NULL DEFAULT FALSE,
        execution_review_complete BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (guild_id, user_id, week_start, asset)
    )""",
    """CREATE TABLE IF NOT EXISTS gbop_coaching_observations (
        guild_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        source_key TEXT NOT NULL,
        theme TEXT NOT NULL,
        polarity SMALLINT NOT NULL CHECK (polarity IN (-1, 1)),
        weight DOUBLE PRECISION NOT NULL DEFAULT 1.0 CHECK (weight > 0),
        note TEXT NOT NULL DEFAULT '',
        observed_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (guild_id, user_id, source_key, theme, polarity)
    )""",
    """CREATE TABLE IF NOT EXISTS gbop_coaching_controls (
        guild_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        theme TEXT NOT NULL,
        retired_at TIMESTAMPTZ,
        note TEXT NOT NULL DEFAULT '',
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (guild_id, user_id, theme)
    )""",
    "CREATE INDEX IF NOT EXISTS gbop_ss_owner_recent ON gbop_ss_weekly_reviews(guild_id,user_id,week_start DESC)",
    "CREATE INDEX IF NOT EXISTS gbop_coach_obs_owner_recent ON gbop_coaching_observations(guild_id,user_id,observed_at DESC)",
    "ALTER TABLE gbop_ss_weekly_reviews ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE gbop_coaching_observations ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE gbop_coaching_controls ENABLE ROW LEVEL SECURITY",
    "REVOKE ALL ON gbop_ss_weekly_reviews FROM anon, authenticated",
    "REVOKE ALL ON gbop_coaching_observations FROM anon, authenticated",
    "REVOKE ALL ON gbop_coaching_controls FROM anon, authenticated",
    "GRANT ALL ON gbop_ss_weekly_reviews TO service_role",
    "GRANT ALL ON gbop_coaching_observations TO service_role",
    "GRANT ALL ON gbop_coaching_controls TO service_role",
]


def init_intelligence(db):
    global _INITIALIZED
    if _INITIALIZED:
        return

    with _INIT_LOCK:
        if _INITIALIZED:
            return

        with db() as conn:
            try:
                conn.execute("SELECT pg_advisory_xact_lock(739204713)")
            except Exception:
                pass
            for sql in SCHEMA_SQL:
                conn.execute(sql)

        _INITIALIZED = True


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _as_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def default_ss_week_start(now_eastern=None) -> str:
    now_eastern = now_eastern or datetime.now(EASTERN)
    today = now_eastern.date()
    current_monday = _monday(today)
    # SS reviews a completed weekly candle. Before the typical Friday 5 PM
    # New York weekly close, the most recently completed week is still the
    # prior one. Saturday/Sunday (and Friday after 5 PM) use the current week.
    week_is_complete = (
        today.weekday() >= 5
        or (today.weekday() == 4 and now_eastern.hour >= 17)
    )
    if week_is_complete:
        return current_monday.isoformat()
    return (current_monday - timedelta(days=7)).isoformat()


def _clean_asset(value):
    text = (value or "General").strip()
    return text[:80] or "General"


def _clip(value, limit=700):
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_theme(value):
    raw = (value or "").strip().lower().replace("_", " ")
    if not raw:
        return None
    if raw in THEME_LABELS:
        return raw
    if raw.replace(" ", "_") in THEME_LABELS:
        return raw.replace(" ", "_")
    if raw in ALIASES:
        return ALIASES[raw]
    for alias, theme in ALIASES.items():
        if alias in raw:
            return theme
    return None


def _contains(text, *patterns):
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def parse_behavior_text(text):
    """Return behavior observations from member-authored text.

    Each item is (theme, polarity, weight, note). Positive polarity records
    disciplined behavior; negative polarity records a member-reported issue.
    """
    original = (text or "").strip()
    t = re.sub(r"\s+", " ", original.lower())
    if not t:
        return []

    # A zero-trade check-in and its optional reason are neutral reflections.
    # "No setup met my criteria" must not become either a setup violation or
    # an inferred strength. Prior, independent coaching evidence is unchanged.
    from gbop_voice_web.no_trade_checkins import no_trade_report
    if no_trade_report(original) is not None:
        return []

    out = []

    def add(theme, polarity, weight=1.0):
        item = (theme, int(polarity), float(weight), _clip(original, 500))
        if item[:2] not in [(x[0], x[1]) for x in out]:
            out.append(item)

    # Leverage / sizing.
    if _contains(t, r"\b(did not|didn't|didnt|no|avoided|stayed within)\b.{0,35}\b(over[- ]?leverag|oversiz|too much risk)"):
        add("leverage", +1)
    elif _contains(t, r"\bover[- ]?leverag", r"\boversiz", r"\btoo much risk\b", r"\bsize(?:d)? too (?:big|large)\b"):
        add("leverage", -1, 1.2)

    # Trade count / overtrading.
    if _contains(t, r"\b(did not|didn't|didnt|never|stayed within|kept to)\b.{0,35}\b(exceed|overtrad|trade limit|too many trades)"):
        add("trade_limit", +1)
    elif _contains(t, r"\bovertrad", r"\btoo many trades\b", r"\bexceed(?:ed)?\b.{0,25}\btrade", r"\bwent over\b.{0,20}\btrade"):
        add("trade_limit", -1, 1.1)

    # A common check-in answer groups boredom + FOMO under one negation:
    # "I did not take any boredom or FOMO trades." Preserve that shared
    # negation so the later bare "FOMO" token is not misread as an issue.
    joint_boredom_fomo_avoided = _contains(
        t,
        r"\b(no|none|did not|didn't|didnt|avoided|without)\b"
        r".{0,90}\b(bored(?:om)?|fomo)\b"
        r".{0,50}\b(bored(?:om)?|fomo)\b",
    )

    # Boredom.
    if joint_boredom_fomo_avoided or _contains(
        t,
        r"\b(no|none|did not|didn't|didnt|avoided|without)\b.{0,35}\b(bored|boredom)",
    ):
        add("boredom", +1)
    elif _contains(t, r"\bboredom trade", r"\btraded? (?:because|out of) boredom\b", r"\bbored and (?:took|entered|traded)"):
        add("boredom", -1, 1.1)

    # FOMO / chasing.
    if joint_boredom_fomo_avoided or _contains(
        t,
        r"\b(no|none|did not|didn't|didnt|avoided|without)\b.{0,35}\b(fomo|chas(?:e|ed|ing))",
    ):
        add("fomo", +1)
    elif _contains(t, r"\bfomo\b", r"\bchas(?:e|ed|ing)\b"):
        add("fomo", -1, 1.0)

    # Revenge trading.
    if _contains(t, r"\b(no|none|did not|didn't|didnt|avoided|without)\b.{0,35}\brevenge"):
        add("revenge", +1)
    elif "revenge" in t:
        add("revenge", -1, 1.2)

    # Exit timing.
    if _contains(t, r"\b(did not|didn't|didnt|never)\b.{0,35}\b(close|exit).{0,20}\bearly"):
        add("exit_timing_early", +1)
    elif _contains(t, r"\b(close|closed|exit|exited).{0,20}\btoo early\b", r"\btook profit too early\b", r"\bcut (?:it|the trade) early\b"):
        add("exit_timing_early", -1, 1.0)

    if _contains(t, r"\b(did not|didn't|didnt|never)\b.{0,35}\b(exit|hold|held).{0,20}\b(late|too long)"):
        add("exit_timing_late", +1)
    elif _contains(t, r"\b(exit|exited|close|closed).{0,20}\btoo late\b", r"\bheld (?:it|the trade) too long\b"):
        add("exit_timing_late", -1, 1.0)

    # Plan adherence.
    if _contains(t, r"\b(followed|stuck to|adhered to|respected)\b.{0,25}\b(plan|rules?)\b", r"\bplan followed\b"):
        add("plan_adherence", +1, 1.0)
    elif _contains(t, r"\b(did not|didn't|didnt)\b.{0,25}\bfollow\b.{0,25}\bplan", r"\b(broke|violated|ignored|deviated from)\b.{0,25}\b(plan|rules?)\b"):
        add("plan_adherence", -1, 1.15)

    # Member-defined A+ criteria, not a universal time-based rule.
    if _contains(t, r"\b(met|followed|waited for|only took)\b.{0,25}\ba\+\b", r"\ba\+ criteria met\b"):
        add("setup_quality", +1)
    elif _contains(t, r"\b(did not|didn't|didnt|failed to)\b.{0,25}\b(a\+|setup criteria)", r"\bnot an a\+\b.{0,20}\btrade"):
        add("setup_quality", -1, 1.0)

    return out


def _upsert_observation(conn, guild, user, source_key, theme, polarity, weight, note, observed_at):
    conn.execute(
        """INSERT INTO gbop_coaching_observations
        (guild_id,user_id,source_key,theme,polarity,weight,note,observed_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(guild_id,user_id,source_key,theme,polarity)
        DO UPDATE SET weight=excluded.weight,note=excluded.note,observed_at=excluded.observed_at""",
        (guild, user, source_key, theme, polarity, weight, note or "", observed_at),
    )


def _ingest_text(conn, guild, user, source_key, text, observed_at, weight_scale=1.0):
    for theme, polarity, weight, note in parse_behavior_text(text):
        _upsert_observation(
            conn, guild, user, source_key, theme, polarity,
            weight * weight_scale, note, observed_at,
        )


def refresh_coaching_sources(db, guild, user, *, now_utc=None):
    init_intelligence(db)
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(days=SOURCE_LOOKBACK_DAYS)).isoformat()

    with db() as conn:
        # Serialize source reads and derived writes with confirmed deletions.
        # A refresh that was waiting must not recreate evidence from old rows.
        conn.execute('SELECT pg_advisory_xact_lock(?)', (user,))
        checkins = conn.execute(
            """SELECT id,response,responded_at
            FROM post_shift_checkins
            WHERE guild_id=? AND user_id=? AND response IS NOT NULL
              AND responded_at IS NOT NULL AND responded_at>=?
            ORDER BY id""",
            (guild, user, cutoff),
        ).fetchall()
        journals = conn.execute(
            """SELECT id,description,rule_adherence,study_note,created_at
            FROM journals
            WHERE guild_id=? AND user_id=? AND created_at>=?
            ORDER BY id""",
            (guild, user, cutoff),
        ).fetchall()
        flags = conn.execute(
            """SELECT id,rule_code,message,created_at
            FROM risk_flags
            WHERE guild_id=? AND user_id=? AND created_at>=?
            ORDER BY id""",
            (guild, user, cutoff),
        ).fetchall()

        for row in checkins:
            _ingest_text(
                conn, guild, user, f"checkin:{row['id']}",
                row["response"] or "", row["responded_at"], 1.25,
            )

        for row in journals:
            source = f"journal:{row['id']}"
            adherence = _adherence_bucket(row["rule_adherence"])
            if adherence != "unknown":
                followed = adherence == "followed"
                _upsert_observation(
                    conn, guild, user, source, "plan_adherence",
                    +1 if followed else -1, 0.9 if followed else 1.0,
                    row["rule_adherence"], row["created_at"],
                )
            combined = " ".join(
                x for x in (
                    row["description"] or "",
                    row["study_note"] or "",
                ) if x
            )
            _ingest_text(conn, guild, user, source, combined, row["created_at"], 0.85)

        for row in flags:
            _upsert_observation(
                conn, guild, user, f"risk:{row['id']}", "risk_protocol", -1, 1.35,
                f"{row['rule_code']}: {row['message']}", row["created_at"],
            )


def ingest_checkin_by_id(db, guild, user, checkin_id):
    init_intelligence(db)
    with db() as conn:
        row = conn.execute(
            """SELECT id,response,responded_at
            FROM post_shift_checkins
            WHERE id=? AND guild_id=? AND user_id=?""",
            (checkin_id, guild, user),
        ).fetchone()
        if not row or not (row["response"] or "").strip():
            return {"ok": False, "error": "Check-in not found or has no response."}
        _ingest_text(
            conn, guild, user, f"checkin:{row['id']}",
            row["response"], row["responded_at"] or stamp(), 1.25,
        )
    return {"ok": True}


def _ss_source_key(row):
    return f"ss:{row['week_start']}:{row['asset']}:execution"


def _sync_ss_observations(db, guild, user, row, *, connection=None):
    source_key = _ss_source_key(row)
    observed_at = row.get("updated_at") or stamp()
    mappings = [
        ("over_leverage", "leverage"),
        ("trade_limit_exceeded", "trade_limit"),
        ("boredom_trades", "boredom"),
        ("closed_too_early", "exit_timing_early"),
        ("exited_too_late", "exit_timing_late"),
    ]

    from contextlib import nullcontext
    with nullcontext(connection) if connection is not None else db() as conn:
        conn.execute(
            "DELETE FROM gbop_coaching_observations WHERE guild_id=? AND user_id=? AND source_key=?",
            (guild, user, source_key),
        )
        for field, theme in mappings:
            value = row.get(field)
            if value is None:
                continue
            _upsert_observation(
                conn, guild, user, source_key, theme,
                -1 if bool(value) else +1, 1.3,
                f"SS execution review: {field}={bool(value)}", observed_at,
            )

        pred = row.get("prediction_correct")
        if pred is not None:
            note = (
                "SS execution review: prior weekly prediction correct."
                if bool(pred)
                else "SS execution review: prior weekly prediction missed. "
                     + (row.get("prediction_miss_reason") or "Reason not recorded.")
            )
            _upsert_observation(
                conn, guild, user, source_key, "weekly_prediction",
                +1 if bool(pred) else -1, 0.9, note, observed_at,
            )


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _rowdict(row):
    if row is None:
        return None
    return {key: _json_value(value) for key, value in dict(row).items()}


def save_ss_review(db, guild, user, args):
    init_intelligence(db)
    if not args.get('asset') or not args.get('week_start'):
        raise ValueError('Saving SS requires the exact asset and week_start of the retrieved report.')
    from gbop_voice_web.weekly_structure import week_window
    week_start = week_window(args['week_start'])['week_start']
    asset = _clean_asset(args.get("asset"))

    editable = [
        "weekly_candle", "closure_vs_previous",
        "high_day", "high_time", "high_launchpad", "high_details",
        "low_day", "low_time", "low_launchpad", "low_details",
        "structural_summary", "next_week_hypothesis", "hypothesis_invalidation",
        "over_leverage", "trade_limit_exceeded", "boredom_trades",
        "closed_too_early", "exited_too_late", "prediction_correct",
        "prediction_miss_reason",
    ]

    supplied = {key: value for key, value in args.items() if key in editable
                and value is not None and (not isinstance(value, str) or value.strip())}
    if not supplied:
        return {"ok": False, "saved": False, "error": "No member answers supplied. Blank or skipped answers stay unknown."}
    boolean_fields = {'over_leverage', 'trade_limit_exceeded', 'boredom_trades',
                      'closed_too_early', 'exited_too_late', 'prediction_correct'}
    for key, value in supplied.items():
        if key in boolean_fields:
            if not isinstance(value, bool):
                raise ValueError(f'{key} must be the member-supplied true/false answer, or null.')
        elif not isinstance(value, str):
            raise ValueError(f'{key} must be member-supplied text, or null.')
        else:
            supplied[key] = value.strip()[:12000]
    from gbop_voice_web.journal_context import journal_transaction
    from gbop_voice_web.weekly_structure import read_report
    report_version = args.get('report_version')
    if not isinstance(report_version, str) or not report_version.strip():
        raise ValueError('Read the exact completed SS report first; saving requires its asset, week_start and report_version.')
    if report_version:
        from gbop_voice_web.market_data import asset_name
        asset = asset_name(asset)
    with journal_transaction(db, args, guild, user, serialize=True) as conn:
        if report_version and not read_report(conn, asset, week_start, report_version):
            raise ValueError('The selected SS report does not match this asset, week and report version.')
        conn.execute(
            """INSERT INTO gbop_ss_weekly_reviews
            (guild_id,user_id,week_start,asset,created_at,updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(guild_id,user_id,week_start,asset) DO NOTHING""",
            (guild, user, week_start, asset, stamp(), stamp()),
        )

        updates = []
        values = []
        for key in editable:
            if key in supplied:
                updates.append(f"{key}=?")
                value = supplied[key]
                if isinstance(value, str):
                    value = value.strip()[:12000]
                values.append(value)
        if updates:
            updates.append("updated_at=?")
            values.append(stamp())
            values.extend([guild, user, week_start, asset])
            conn.execute(
                "UPDATE gbop_ss_weekly_reviews SET "
                + ", ".join(updates)
                + " WHERE guild_id=? AND user_id=? AND week_start=? AND asset=?",
                tuple(values),
            )

        row = conn.execute(
            """SELECT * FROM gbop_ss_weekly_reviews
            WHERE guild_id=? AND user_id=? AND week_start=? AND asset=?""",
            (guild, user, week_start, asset),
        ).fetchone()

        required_structure = [
            "weekly_candle", "closure_vs_previous",
            "high_day", "high_launchpad",
            "low_day", "low_launchpad",
            "structural_summary", "next_week_hypothesis",
        ]
        structure_complete = all((row[k] or "").strip() for k in required_structure)

        execution_fields = [
            "over_leverage", "trade_limit_exceeded", "boredom_trades",
            "closed_too_early", "exited_too_late", "prediction_correct",
        ]
        execution_complete = all(row[k] is not None for k in execution_fields)
        if execution_complete and not bool(row["prediction_correct"]):
            execution_complete = bool((row["prediction_miss_reason"] or "").strip())

        conn.execute(
            """UPDATE gbop_ss_weekly_reviews
            SET structure_complete=?, execution_review_complete=?, updated_at=?
            WHERE guild_id=? AND user_id=? AND week_start=? AND asset=?""",
            (
                structure_complete,
                execution_complete,
                stamp(),
                guild, user, week_start, asset,
            ),
        )
        row = conn.execute(
            """SELECT * FROM gbop_ss_weekly_reviews
            WHERE guild_id=? AND user_id=? AND week_start=? AND asset=?""",
            (guild, user, week_start, asset),
        ).fetchone()

        if report_version:
            import json
            latest = conn.execute("""SELECT revision,answers FROM gbop_ss_contributions
                WHERE guild_id=? AND user_id=? AND asset=? AND week_start=? AND report_version=?
                ORDER BY revision DESC LIMIT 1""", (guild,user,asset,week_start,report_version)).fetchone()
            version_answers = json.loads(latest['answers']).get('answers', {}) if latest else {}
            version_answers.update(supplied)
            answers = json.dumps({'answers': version_answers, 'supplied_fields': sorted(supplied),
                                  'source': 'member_reported'}, sort_keys=True, separators=(',', ':'))
            if len(answers.encode('utf-8')) > 262144:
                raise ValueError('These SS answers exceed the saved-report size limit; shorten this reflection before saving.')
            # Repeating identical supplied answers is idempotent; changes append.
            if not latest or latest['answers'] != answers:
                revision = int(latest['revision']) + 1 if latest else 1
                conn.execute("""INSERT INTO gbop_ss_contributions
                    (guild_id,user_id,asset,week_start,report_version,revision,answers,created_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (guild,user,asset,week_start,report_version,revision,answers,stamp()))

        saved = _rowdict(row)
        _sync_ss_observations(db, guild, user, saved, connection=conn)
    return {
        "ok": True,
        "saved": True,
        "review": _versioned_ss_review(version_answers, week_start, asset, report_version),
        "review_scope": "exact_report_version",
        "report_version": report_version,
        "next_step": _next_ss_step(_versioned_ss_review(version_answers, week_start, asset, report_version)),
    }


def _versioned_ss_review(answers, week_start, asset, report_version):
    """Exact-version member view; never carry older report answers implicitly."""
    structure = ['weekly_candle', 'closure_vs_previous', 'high_day', 'high_launchpad',
                 'low_day', 'low_launchpad', 'structural_summary', 'next_week_hypothesis']
    execution = ['over_leverage', 'trade_limit_exceeded', 'boredom_trades',
                 'closed_too_early', 'exited_too_late', 'prediction_correct']
    complete = all(answers.get(key) is not None for key in execution)
    if complete and not answers.get('prediction_correct'):
        complete = bool((answers.get('prediction_miss_reason') or '').strip())
    return {**answers, 'week_start': week_start, 'asset': asset, 'report_version': report_version,
            'structure_complete': all((answers.get(key) or '').strip() for key in structure),
            'execution_review_complete': complete}


def _next_ss_step(row):
    sequence = [
        ("weekly_candle", "Describe what the completed weekly candle did visually."),
        ("closure_vs_previous", "How did it close relative to the previous weekly candle?"),
        ("high_day", "What day formed the High of the Week?"),
        ("high_launchpad", "What key level launched the move into the High of the Week?"),
        ("low_day", "What day formed the Low of the Week?"),
        ("low_launchpad", "What key level launched the move into the Low of the Week?"),
        ("structural_summary", "Summarize how the week structurally formed."),
        ("next_week_hypothesis", "State the conditional next-week hypothesis."),
        ("over_leverage", "Did you over-leverage?"),
        ("trade_limit_exceeded", "Did you exceed the number of permitted trades?"),
        ("boredom_trades", "Did you take any boredom trades?"),
        ("closed_too_early", "Did you close too early?"),
        ("exited_too_late", "Did you exit too late?"),
        ("prediction_correct", "Did you predict last week's candle correctly?"),
    ]
    for field, question in sequence:
        if row.get(field) is None or (isinstance(row.get(field), str) and not row.get(field).strip()):
            return question
    if row.get("prediction_correct") is not None and not bool(row.get("prediction_correct")) and not (row.get("prediction_miss_reason") or "").strip():
        return "Why was the prior weekly prediction not correct?"
    return "SS review is complete."


def get_ss_review(db, guild, user, args=None):
    init_intelligence(db)
    args = args or {}
    week_start = args.get("week_start")
    asset = args.get("asset")

    report_version = args.get('report_version')
    if report_version:
        if not asset or not week_start:
            raise ValueError('A versioned SS read requires its exact asset and week_start.')
        from gbop_voice_web.weekly_structure import read_report, week_window
        from gbop_voice_web.market_data import asset_name
        import json
        asset = asset_name(asset)
        week_start = week_window(week_start)['week_start']
        with db() as conn:
            if not read_report(conn, asset, week_start, report_version):
                raise ValueError('No SS report matches that asset, week and report version.')
            contribution = conn.execute("""SELECT answers FROM gbop_ss_contributions
                WHERE guild_id=? AND user_id=? AND asset=? AND week_start=? AND report_version=?
                ORDER BY revision DESC LIMIT 1""", (guild,user,asset,week_start,report_version)).fetchone()
        review = (_versioned_ss_review(json.loads(contribution['answers']).get('answers', {}),
                  week_start, asset, report_version) if contribution else None)
        return {'ok': True, 'review': review, 'review_scope': 'exact_report_version',
                'report_version': report_version, 'target_week_start': week_start,
                'next_step': _next_ss_step(review) if review else 'Start with the completed weekly candle.'}

    with db() as conn:
        if week_start and asset:
            row = conn.execute(
                """SELECT * FROM gbop_ss_weekly_reviews
                WHERE guild_id=? AND user_id=? AND week_start=? AND asset=?""",
                (guild, user, date.fromisoformat(str(week_start)).isoformat(), _clean_asset(asset)),
            ).fetchone()
        elif week_start:
            row = conn.execute(
                """SELECT * FROM gbop_ss_weekly_reviews
                WHERE guild_id=? AND user_id=? AND week_start=?
                ORDER BY updated_at DESC LIMIT 1""",
                (guild, user, date.fromisoformat(str(week_start)).isoformat()),
            ).fetchone()
        elif asset:
            row = conn.execute(
                """SELECT * FROM gbop_ss_weekly_reviews
                WHERE guild_id=? AND user_id=? AND asset=?
                ORDER BY week_start DESC, updated_at DESC LIMIT 1""",
                (guild, user, _clean_asset(asset)),
            ).fetchone()
        else:
            target_week = default_ss_week_start()
            row = conn.execute(
                """SELECT * FROM gbop_ss_weekly_reviews
                WHERE guild_id=? AND user_id=? AND week_start=?
                ORDER BY updated_at DESC LIMIT 1""",
                (guild, user, target_week),
            ).fetchone()

    target_week = (
        date.fromisoformat(str(week_start)).isoformat()
        if week_start else
        (None if asset else default_ss_week_start())
    )
    if row is None:
        return {
            "ok": True,
            "review": None,
            "target_week_start": target_week,
            "next_step": "Start with the completed weekly candle.",
        }
    review = _rowdict(row)
    return {
        "ok": True,
        "review": review,
        "review_scope": "latest_asset_week_view",
        "version_scope_warning": "This latest asset/week view can include answers from earlier report versions. Pass report_version to assess the current report's answers and completion.",
        "target_week_start": target_week or review.get("week_start"),
        "next_step": _next_ss_step(review),
    }


def set_coaching_theme(db, guild, user, args):
    init_intelligence(db)
    theme = _normalize_theme(args.get("theme"))
    if not theme:
        return {
            "ok": False,
            "error": "Unknown coaching theme.",
            "available_themes": sorted(THEME_LABELS),
        }
    active = bool(args.get("active"))
    note = (args.get("note") or "").strip()[:1000]

    with db() as conn:
        if active:
            conn.execute(
                "DELETE FROM gbop_coaching_controls WHERE guild_id=? AND user_id=? AND theme=?",
                (guild, user, theme),
            )
        else:
            conn.execute(
                """INSERT INTO gbop_coaching_controls
                (guild_id,user_id,theme,retired_at,note,updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(guild_id,user_id,theme)
                DO UPDATE SET retired_at=excluded.retired_at,note=excluded.note,updated_at=excluded.updated_at""",
                (guild, user, theme, stamp(), note, stamp()),
            )
    return {
        "ok": True,
        "theme": theme,
        "active": active,
        "message": (
            f"{THEME_LABELS[theme]} is active again."
            if active else
            f"Prior {THEME_LABELS[theme]} observations are retired. New future evidence can bring the theme back."
        ),
    }


def coaching_profile(db, guild, user, *, now_utc=None):
    init_intelligence(db)
    refresh_coaching_sources(db, guild, user, now_utc=now_utc)
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(days=SOURCE_LOOKBACK_DAYS)).isoformat()

    with db() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(?)', (user,))
        # Historical orphan evidence is ineligible immediately, without a
        # destructive backfill. Studies/reflections with owned sources remain.
        observations = conn.execute(
            """SELECT o.theme,o.polarity,o.weight,o.note,o.observed_at
            FROM gbop_coaching_observations o
            WHERE o.guild_id=? AND o.user_id=? AND o.observed_at>=?
              AND (o.source_key NOT LIKE ? OR EXISTS (
                  SELECT 1 FROM journals j WHERE j.guild_id=o.guild_id AND j.user_id=o.user_id
                    AND o.source_key='journal:' || CAST(j.id AS TEXT)))
              AND (o.source_key NOT LIKE ? OR EXISTS (
                  SELECT 1 FROM risk_flags r WHERE r.guild_id=o.guild_id AND r.user_id=o.user_id
                    AND o.source_key='risk:' || CAST(r.id AS TEXT)))
            ORDER BY o.observed_at DESC""",
            (guild, user, cutoff, 'journal:%', 'risk:%'),
        ).fetchall()
        controls = conn.execute(
            """SELECT theme,retired_at
            FROM gbop_coaching_controls
            WHERE guild_id=? AND user_id=?""",
            (guild, user),
        ).fetchall()

    retired = {r["theme"]: _as_dt(r["retired_at"]) for r in controls if r["retired_at"]}
    scores = defaultdict(lambda: {"positive": 0.0, "negative": 0.0, "latest_note": "", "latest_at": None})

    for row in observations:
        theme = row["theme"]
        observed = _as_dt(row["observed_at"])
        if observed is None:
            continue
        retired_at = retired.get(theme)
        if retired_at is not None and observed <= retired_at:
            continue
        age_days = max(0.0, (now_utc - observed.astimezone(timezone.utc)).total_seconds() / 86400.0)
        decay = math.pow(0.5, age_days / HALF_LIFE_DAYS)
        amount = float(row["weight"] or 1.0) * decay
        key = "positive" if int(row["polarity"]) > 0 else "negative"
        scores[theme][key] += amount
        if scores[theme]["latest_at"] is None or observed > scores[theme]["latest_at"]:
            scores[theme]["latest_at"] = observed
            scores[theme]["latest_note"] = row["note"] or ""

    issues = []
    strengths = []
    for theme, item in scores.items():
        issue_score = item["negative"] - 0.8 * item["positive"]
        strength_score = item["positive"] - 0.8 * item["negative"]
        common = {
            "theme": theme,
            "label": THEME_LABELS.get(theme, theme.replace("_", " ")),
            "latest_note": _clip(item["latest_note"], 300),
            "latest_at": item["latest_at"].isoformat() if item["latest_at"] else None,
        }
        if issue_score >= 0.25:
            issues.append({**common, "score": round(issue_score, 3), "reminder": ISSUE_REMINDERS.get(theme, "Protect this behavior next shift.")})
        if strength_score >= 0.25:
            strengths.append({**common, "score": round(strength_score, 3), "reminder": STRENGTH_REMINDERS.get(theme, "Keep repeating this behavior.")})

    issues.sort(key=lambda x: (x["score"], x["latest_at"] or ""), reverse=True)
    strengths.sort(key=lambda x: (x["score"], x["latest_at"] or ""), reverse=True)

    current_focus = issues[0] if issues else (strengths[0] if strengths else None)
    return {
        "issues": issues[:5],
        "strengths": strengths[:5],
        "current_focus": current_focus,
        "basis": "Recent member reflections, journals, SS execution review, and recorded risk flags; evidence decays over time.",
    }


def _latest_shift_plan(db, guild, user, session_date=None, shift=None):
    try:
        with db() as conn:
            rows = conn.execute(
                """SELECT session_date,shift,plan,updated_at
                FROM gbop_shift_plans
                WHERE guild_id=? AND user_id=?
                ORDER BY session_date DESC, updated_at DESC LIMIT 30""",
                (guild, user),
            ).fetchall()
    except Exception:
        return None

    for row in rows:
        if session_date and row["session_date"] != session_date:
            continue
        if shift and (row["shift"] or "").lower() != shift.lower():
            continue
        return dict(row)
    return None


def get_member_plan(db, guild, user, args=None):
    args = args or {}
    ss = get_ss_review(db, guild, user, {})["review"]
    profile = coaching_profile(db, guild, user)
    shift_plan = _latest_shift_plan(
        db, guild, user,
        session_date=args.get("session_date"),
        shift=args.get("shift"),
    )
    with db() as conn:
        last_checkin = conn.execute(
            """SELECT shift_date,shift,response,responded_at
            FROM post_shift_checkins
            WHERE guild_id=? AND user_id=? AND response IS NOT NULL
            ORDER BY responded_at DESC LIMIT 1""",
            (guild, user),
        ).fetchone()

    return {
        "ok": True,
        "current_ss": ss,
        "coaching_profile": profile,
        "saved_shift_plan": shift_plan,
        "last_checkin": _rowdict(last_checkin),
    }



def get_member_dashboard(db, guild, user, args=None):
    """Evidence-grounded trader profile from this member's own stored records."""
    args = args or {}
    raw_days = args.get("days")
    days = 30 if raw_days is None else int(raw_days)
    if days < 1 or days > 3650:
        raise ValueError("days must be between 1 and 3650.")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    with db() as conn:
        trades = conn.execute(
            """SELECT id,asset,direction,play,session,status,final_result_r,created_at,closed_at
            FROM theses
            WHERE guild_id=? AND user_id=? AND created_at>=?
            ORDER BY id""",
            (guild, user, cutoff),
        ).fetchall()
        executions = conn.execute(
            """SELECT thesis_id,entry_model,tier,risk_r,created_at
            FROM thesis_executions
            WHERE guild_id=? AND user_id=? AND created_at>=?
            ORDER BY id""",
            (guild, user, cutoff),
        ).fetchall()
        journals = conn.execute(
            """SELECT rule_adherence,result_r,study_note,created_at
            FROM journals
            WHERE guild_id=? AND user_id=? AND created_at>=?
            ORDER BY id""",
            (guild, user, cutoff),
        ).fetchall()
        risk_flag_count = conn.execute(
            """SELECT COUNT(*) AS n FROM risk_flags
            WHERE guild_id=? AND user_id=? AND created_at>=?""",
            (guild, user, cutoff),
        ).fetchone()["n"]

    scored = [
        row for row in trades
        if row["status"] == "CLOSED" and row["final_result_r"] is not None
    ]
    results = [float(row["final_result_r"]) for row in scored]

    play_stats = defaultdict(lambda: {"count": 0, "scored": 0, "net_r": 0.0})
    for row in trades:
        play = (row["play"] or "Unspecified").strip() or "Unspecified"
        play_stats[play]["count"] += 1
        if row["status"] == "CLOSED" and row["final_result_r"] is not None:
            play_stats[play]["scored"] += 1
            play_stats[play]["net_r"] += float(row["final_result_r"])

    most_traded_plays = [
        {"play": name, **values}
        for name, values in sorted(
            play_stats.items(),
            key=lambda pair: (pair[1]["count"], pair[1]["net_r"]),
            reverse=True,
        )[:5]
    ]

    result_by_trade = {
        row["id"]: float(row["final_result_r"])
        for row in scored
    }
    model_stats = defaultdict(
        lambda: {
            "execution_count": 0,
            "risk_r": 0.0,
            "scored_trade_ids": set(),
            "associated_net_r": 0.0,
        }
    )
    for row in executions:
        name = (row["entry_model"] or "Unspecified").strip() or "Unspecified"
        model_stats[name]["execution_count"] += 1
        model_stats[name]["risk_r"] += float(row["risk_r"] or 0.0)
        thesis_id = row["thesis_id"]
        if (
            thesis_id in result_by_trade
            and thesis_id not in model_stats[name]["scored_trade_ids"]
        ):
            model_stats[name]["scored_trade_ids"].add(thesis_id)
            model_stats[name]["associated_net_r"] += result_by_trade[thesis_id]

    execution_models = []
    for name, values in model_stats.items():
        execution_models.append({
            "entry_model": name,
            "execution_count": values["execution_count"],
            "risk_r": round(values["risk_r"], 4),
            "scored_trades": len(values["scored_trade_ids"]),
            "associated_net_r": round(values["associated_net_r"], 4),
        })
    execution_models.sort(
        key=lambda item: (
            item["associated_net_r"],
            item["scored_trades"],
            item["execution_count"],
        ),
        reverse=True,
    )

    adherence = defaultdict(int)
    for row in journals:
        adherence[_adherence_bucket(row["rule_adherence"])] += 1
    known_adherence = adherence["followed"] + adherence["partial"] + adherence["violated"]
    adherence_rate = (
        100.0 * adherence["followed"] / known_adherence
        if known_adherence else None
    )

    coaching = coaching_profile(db, guild, user)
    focus = coaching.get("current_focus")
    ss = get_ss_review(db, guild, user, {})["review"]
    risk_profile = get_profile(db, guild, user)

    emphasis = (
        focus["reminder"]
        if focus
        else "No recurring coaching issue has enough evidence yet; keep following the member's written plan and collecting clean journal data."
    )

    return {
        "ok": True,
        "window_days": days,
        "sample": {
            "trades": len(trades),
            "closed_scored_trades": len(scored),
            "executions": len(executions),
            "journals": len(journals),
        },
        "performance": {
            "wins": sum(1 for value in results if value > 0),
            "losses": sum(1 for value in results if value < 0),
            "breakeven": sum(1 for value in results if value == 0),
            "win_rate_pct": (
                100.0 * sum(1 for value in results if value > 0) / len(results)
                if results else None
            ),
            "net_r": round(sum(results), 4),
            "avg_r": round(sum(results) / len(results), 4) if results else None,
        },
        "most_traded_plays": most_traded_plays,
        "execution_models": execution_models[:5],
        "strongest_execution_model": execution_models[0] if execution_models and execution_models[0]["scored_trades"] else None,
        "risk": {
            "profile": risk_profile,
            "flags_in_window": int(risk_flag_count or 0),
        },
        "adherence": {
            "followed": adherence["followed"],
            "partial": adherence["partial"],
            "violated": adherence["violated"],
            "unknown": adherence["unknown"],
            "full_adherence_rate_pct": adherence_rate,
        },
        "coaching": coaching,
        "current_ss": ss,
        "weekly_emphasis": emphasis,
        "evidence_note": (
            "Most-traded plays are frequency-based, not assumed preferences. "
            "Execution-model performance is the closed-trade outcome associated "
            "with trades containing that model; it is not isolated entry attribution."
        ),
    }


def intelligence_context(db, guild, user):
    try:
        state = get_member_plan(db, guild, user, {})
    except Exception as exc:
        return f"MEMBER INTELLIGENCE: unavailable ({type(exc).__name__})."

    lines = ["MEMBER INTELLIGENCE — VERIFIED PERSISTENT CONTEXT"]
    ss = state.get("current_ss")
    if ss:
        lines.append(
            f"Latest SS: week {ss['week_start']} | asset {ss['asset']} | "
            f"structure_complete={bool(ss['structure_complete'])} | "
            f"execution_review_complete={bool(ss['execution_review_complete'])}."
        )
        if ss.get("structural_summary"):
            lines.append("SS observed structure: " + _clip(ss["structural_summary"], 600))
        if ss.get("next_week_hypothesis"):
            lines.append("SS conditional hypothesis: " + _clip(ss["next_week_hypothesis"], 600))
        if ss.get("hypothesis_invalidation"):
            lines.append("SS invalidation/condition: " + _clip(ss["hypothesis_invalidation"], 400))
    else:
        lines.append("Latest SS: none saved yet.")

    profile = state["coaching_profile"]
    focus = profile.get("current_focus")
    if focus:
        kind = "issue" if any(x["theme"] == focus["theme"] for x in profile["issues"]) else "strength"
        lines.append(
            f"Current coaching focus ({kind}): {focus['label']}. "
            f"Reminder: {focus['reminder']}"
        )
    else:
        lines.append("Current coaching focus: no reliable recurring theme yet.")

    if state.get("saved_shift_plan"):
        p = state["saved_shift_plan"]
        lines.append(f"Latest saved shift plan ({p['session_date']} {p['shift']}): {_clip(p['plan'], 600)}")

    if state.get("last_checkin"):
        c = state["last_checkin"]
        lines.append(
            f"Latest post-shift reflection ({c['shift_date']} {c['shift']}): "
            + _clip(c["response"], 500)
        )

    return "\n".join(lines)


def build_pre_shift_message(db, guild, user, shift, session_date=None):
    shift = (shift or "").lower()
    label = "Day Shift" if shift == "day" else "Night Shift"
    clock = "9:00 AM" if shift == "day" else "9:00 PM"
    state = get_member_plan(
        db, guild, user,
        {"shift": shift, "session_date": session_date},
    )
    ss = state.get("current_ss")
    profile = state["coaching_profile"]
    focus = profile.get("current_focus")

    if ss and ss.get("next_week_hypothesis"):
        structural = _clip(ss["next_week_hypothesis"], 480)
        if ss.get("hypothesis_invalidation"):
            structural += " | Condition/invalidation: " + _clip(ss["hypothesis_invalidation"], 220)
    elif ss and ss.get("structural_summary"):
        structural = _clip(ss["structural_summary"], 560)
    else:
        structural = "No saved SS weekly structure plan yet. Use the chart and your saved shift plan; do not invent structure."

    if focus:
        execution = focus["reminder"]
    else:
        execution = "Protect your own A+ criteria, follow your written plan, and keep risk inside your saved protocol."

    return (
        f"⏱️ **{label} begins in 5 minutes** ({clock} Eastern).\n\n"
        f"**SS structure:** {structural}\n"
        f"**Your execution focus:** {execution}"
    )


INTELLIGENCE_PROMPT = """
# PERSISTENT SS + MEMBER INTELLIGENCE

GBOP has persistent tools for Weekly Structure Study (SS) and member-specific
coaching. SS is price-structure work, not a statistics report.

When a member says "tell me SS", "let's do SS", or "do SS":
1. Use get_weekly_structure_study to read the completed quantitative report for
   the requested asset/week, then get_ss_review with that exact asset/week/report_version
   to resume their private reflection. The unversioned latest view may span versions.
   For the latest completed study, pass week_start=null and report_version=null.
   Do not invent a week label or version, or use the string "latest" as a version.
   Bitcoin/BTC is BTCUSD; Ethereum/ETH is ETHUSD. Only pass an exact week/version
   when the member selected it or it came from a retrieved report. Preserve an
   explicit selection on a miss; do not substitute another week or version.
   If asset is missing, offer the returned asset choices. Never silently reuse a
   different asset/week or claim incomplete observed extremes are definitive.
   SS combines quantitative facts and the member's human launchpad/PDA/structural
   analysis in ONE report. A missing report is not permission to invent candles.
2. Guide the canonical SS sequence conversationally and ask only for the next
   missing fact. Use chart/image evidence when available.
3. Offer optional recording of the member's answers in their SS journal. Only
   call save_ss_review when they want their supplied answers saved; pass the exact
   report_version, asset and week_start returned by get_weekly_structure_study.
   Null/blank means unchanged. Never auto-complete unanswered reflection fields,
   turn SS into a trade record, or capture an unrelated DM as an SS answer.
   Automated market facts stay in the versioned report, separate from human input.
4. Finish the weekly candle, closure, High of Week, Low of Week, launching-pad
   levels, structural synthesis, and conditional hypothesis BEFORE the execution
   review.
5. During the execution review save the member's own answers for over-leverage,
   trade-limit breach, boredom trades, early exit, late exit, and whether the
   prior weekly prediction was correct. If it was not correct, save why.
6. Keep observed structure separate from the next-week hypothesis and never state
   the hypothesis as certainty.

For "what's my plan?", "what do I need to remember?", or "what did we conclude
in SS?", use get_member_plan. Combine the saved SS structure with the member's
current coaching focus; do not substitute generic advice.

For "what do you know about me as a trader?", "show my trader profile/dashboard",
"what are my strongest setups?", or similar member-profile questions, use
get_member_dashboard. Treat most-traded plays as measured frequency, not an
assumed personal preference. Explain sample size and do not overclaim model
performance when only a few scored trades exist.

The coaching profile is evidence-based and recency-weighted. Old issues decay.
A member can correct a stale theme by saying it is no longer an issue; use
set_coaching_theme with active=false. This retires prior evidence, but genuinely
new future evidence may bring the theme back. Use active=true if the member asks
to restore a theme. Do not permanently label or diagnose a member.
""".strip()


INTELLIGENCE_TOOLS = [
    schema(
        "get_weekly_structure_study",
        "Read the versioned completed SS quantitative report and this member's optional contributions. For latest completed, pass week_start=null and report_version=null. Never invent a week or version.",
        {"week_start": {**TEXT_NULL, "description": "Exact Monday reporting key YYYY-MM-DD only when selected; null for latest completed. This label is not the broker W1 opening date."},
         "asset": {**TEXT_NULL, "description": "Requested asset; Bitcoin/BTC maps to BTCUSD and Ethereum/ETH maps to ETHUSD. Null lists available assets."},
         "report_version": {**TEXT_NULL, "description": "Exact version returned by a report or explicitly selected; null for the latest revision. Never use the string latest."}},
    ),
    schema(
        "get_ss_review",
        "Read the member's SS answers. Pass asset/week/report_version for exact-version completion; omit version only for the explicitly labeled latest asset/week view.",
        {"week_start": TEXT_NULL, "asset": TEXT_NULL, "report_version": TEXT_NULL},
    ),
    schema(
        "save_ss_review",
        "Optionally save only the member-supplied SS answers for the exact asset/week/report version. Null and blank fields remain unchanged.",
        {
            "week_start": TEXT_NULL,
            "asset": TEXT_NULL,
            "report_version": TEXT_NULL,
            "weekly_candle": TEXT_NULL,
            "closure_vs_previous": TEXT_NULL,
            "high_day": TEXT_NULL,
            "high_time": TEXT_NULL,
            "high_launchpad": TEXT_NULL,
            "high_details": TEXT_NULL,
            "low_day": TEXT_NULL,
            "low_time": TEXT_NULL,
            "low_launchpad": TEXT_NULL,
            "low_details": TEXT_NULL,
            "structural_summary": TEXT_NULL,
            "next_week_hypothesis": TEXT_NULL,
            "hypothesis_invalidation": TEXT_NULL,
            "over_leverage": BOOL_NULL,
            "trade_limit_exceeded": BOOL_NULL,
            "boredom_trades": BOOL_NULL,
            "closed_too_early": BOOL_NULL,
            "exited_too_late": BOOL_NULL,
            "prediction_correct": BOOL_NULL,
            "prediction_miss_reason": TEXT_NULL,
        },
    ),
    schema(
        "get_member_plan",
        "Read the member's saved SS structure, living coaching focus, latest shift plan, and latest post-shift reflection.",
        {"session_date": TEXT_NULL, "shift": TEXT_NULL},
    ),
    schema(
        "get_member_dashboard",
        "Read an evidence-grounded trader dashboard: recent performance, most-traded plays, execution models, adherence, risk profile, SS, and living coaching focus.",
        {"days": {"type": ["integer", "null"]}},
    ),
    schema(
        "set_coaching_theme",
        "Retire or restore a coaching theme when the member explicitly says it is or is not still relevant.",
        {
            "theme": {"type": "string"},
            "active": {"type": "boolean"},
            "note": TEXT_NULL,
        },
    ),
]

INTELLIGENCE_NAMES = {tool["name"] for tool in INTELLIGENCE_TOOLS}


def intelligence_tool(db, guild, user, name, args):
    from gbop_voice_web.weekly_structure import get_weekly_structure_study
    handlers = {
        "get_weekly_structure_study": get_weekly_structure_study,
        "get_ss_review": get_ss_review,
        "save_ss_review": save_ss_review,
        "get_member_plan": get_member_plan,
        "get_member_dashboard": get_member_dashboard,
        "set_coaching_theme": set_coaching_theme,
    }
    fn = handlers.get(name)
    if fn is None:
        return {"ok": False, "error": f"Unknown intelligence tool: {name}"}
    try:
        return fn(db, guild, user, args)
    except (ValueError, TypeError, KeyError) as exc:
        return {"ok": False, "error": str(exc)}
