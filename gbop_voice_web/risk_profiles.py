"""Member-owned risk preferences shared by GBOP voice sessions."""
import math
import threading
from datetime import datetime, timezone

_ready = False
_lock = threading.Lock()


def ensure_schema(db):
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        with db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS member_risk_profiles (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    account_risk_pct DOUBLE PRECISION,
                    tier1_pct DOUBLE PRECISION NOT NULL,
                    tier2_pct DOUBLE PRECISION NOT NULL,
                    tier3_pct DOUBLE PRECISION NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
        _ready = True


def get_profile(db, guild_id, user_id):
    ensure_schema(db)
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM member_risk_profiles WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        ).fetchone()
    if row is None:
        return {"configured": False, "account_risk_pct": None,
                "tier1_pct": 60.0, "tier2_pct": 30.0, "tier3_pct": 10.0,
                "source": "GTOP default suggestion; not yet chosen by member"}
    return {"configured": True, **{key: row[key] for key in (
        "account_risk_pct", "tier1_pct", "tier2_pct", "tier3_pct", "updated_at")},
        "source": "member preference"}


def validate_profile(args):
    values = {}
    for key in ("tier1_pct", "tier2_pct", "tier3_pct", "account_risk_pct"):
        raw = args.get(key)
        if key == "account_risk_pct" and raw is None:
            values[key] = None
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{key} must be a number.")
        value = float(raw)
        if not math.isfinite(value) or value < 0 or value > 100:
            raise ValueError(f"{key} must be between 0 and 100.")
        if key == "account_risk_pct" and value == 0:
            raise ValueError("Account risk must be greater than zero, or left unspecified.")
        values[key] = value
    if not math.isclose(sum(values[f"tier{i}_pct"] for i in (1, 2, 3)), 100, abs_tol=1e-6):
        raise ValueError("Tier allocations must total 100% of the thesis risk budget.")
    return values


def save_profile(db, guild_id, user_id, args):
    values = validate_profile(args)
    ensure_schema(db)
    with db() as conn:
        conn.execute("""
            INSERT INTO member_risk_profiles
                (guild_id, user_id, account_risk_pct, tier1_pct, tier2_pct, tier3_pct, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                account_risk_pct=excluded.account_risk_pct,
                tier1_pct=excluded.tier1_pct, tier2_pct=excluded.tier2_pct,
                tier3_pct=excluded.tier3_pct, updated_at=excluded.updated_at
        """, (guild_id, user_id, values["account_risk_pct"], values["tier1_pct"],
              values["tier2_pct"], values["tier3_pct"], datetime.now(timezone.utc).isoformat()))
    return {"configured": True, **values, "source": "member preference"}


def tier_limit(profile, tier):
    return float(profile[f"tier{int(tier)}_pct"]) / 100.0


def profile_context(profile):
    return (
        "MEMBER RISK PREFERENCES (override default allocations, not entry classifications): "
        f"configured={profile['configured']}; 1R account risk={profile['account_risk_pct']}%; "
        f"tier allocations={profile['tier1_pct']}/{profile['tier2_pct']}/{profile['tier3_pct']}% of 1R. "
        "Unspecified account risk is unknown, never assume the owner's percentage. "
        "Allocations are cumulative per thesis. Exceeding them is WARN + SAVE."
    )
