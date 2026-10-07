
import asyncio
import math
import hashlib
import hmac
import json
import os
import secrets
import time

import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gbop_voice_web.market_watch import WATCH_TOOLS, WATCH_NAMES, WATCH_PROMPT, watch_tool, init_watches
from gbop_voice_web.journal_recall import history as recall_journal_history, send_history as send_journal_history, JOURNAL_RECALL_TOOLS, JOURNAL_RECALL_PROMPT, RECALL_SELECTORS
from gbop_voice_web.delivery_receipts import DELIVERY_TOOLS, DELIVERY_PROMPT, delivery_status
from gbop_voice_web.midpoint_preferences import TOOLS as MIDPOINT_TOOLS, MIDPOINT_PROMPT, LIVE_MIDPOINT_PROMPT
from gbop_voice_web.market_data import MARKET_TOOLS, MARKET_NAMES, MARKET_PROMPT, LIVE_MARKET_PROMPT, market_clock, market_tool, init_market
from gbop_voice_web.market_routes import market_router
from db_compat import db
from gbop_voice_web.member_access import member_access_error
from gbop_voice_web.journal_coach import COACH_PROMPT, COACH_TOOLS, COACH_NAMES, coach_tool, init_coach
from gbop_voice_web.member_intelligence import (
    INTELLIGENCE_PROMPT,
    INTELLIGENCE_TOOLS,
    INTELLIGENCE_NAMES,
    intelligence_tool,
    init_intelligence,
    intelligence_context,
)
from gbop_voice_web.trade_photos import PHOTO_PROMPT, PHOTO_TOOLS, PHOTO_NAMES, photo_tool
from gbop_voice_web.trade_assist import (
    TRADE_ASSIST_PROMPT,
    TRADE_ASSIST_TOOLS,
    TRADE_ASSIST_NAMES,
    trade_assist_tool,
    trade_assist_context,
)
from gbop_voice_web.deletion import delete_trade_records
from gbop_voice_web.journal_numbers import journal_number
from gbop_voice_web.trade_numbers import trade_number, trade_record_id, TRADE_NUMBERING_PROMPT
from gbop_voice_web.gtop_protocol import CANONICAL_KNOWLEDGE, tier_max_r, infer_tier, tier_used_r
from gbop_voice_web.risk_profiles import get_profile, save_profile, tier_limit, profile_context
from typing import Any
from urllib.parse import urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
ENV_PATH = PROJECT_DIR / ".env"


load_dotenv(ENV_PATH)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
VOICE_PIN = os.getenv("GBOP_VOICE_PIN", "").strip()
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI",
    "http://127.0.0.1:8787/auth/discord/callback",
).strip()
GTOP_MEMBER_ROLE_ID = int(
    os.getenv("GTOP_MEMBER_ROLE_ID", "1551154588701163610") or "0"
)
OWNER_USER_ID = int(
    os.getenv(
        "GBOP_VOICE_USER_ID",
        os.getenv("GTOP_OWNER_USER_ID", "0"),
    )
    or "0"
)
GTOP_GUILD_ID = int(os.getenv("GTOP_GUILD_ID", "0") or "0")
BACKEND_MODEL = os.getenv("GBOP_VOICE_BACKEND_MODEL", "gpt-5.6-luna")
LIVE_MODEL = os.getenv("GBOP_LIVE_MODEL", "gpt-live-1")
LIVE_VOICE = os.getenv("GBOP_LIVE_VOICE", "marin")
SHIFT_DM_DELIVERY_VERSION = "per-member-retry-v2"

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing from ../.env")
if not OWNER_USER_ID:
    raise RuntimeError(
        "Set GTOP_OWNER_USER_ID or GBOP_VOICE_USER_ID in ../.env"
    )
if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET:
    raise RuntimeError(
        "DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET are required in ../.env"
    )
if not GTOP_GUILD_ID:
    raise RuntimeError("GTOP_GUILD_ID is required in ../.env")
if not GTOP_MEMBER_ROLE_ID:
    raise RuntimeError("GTOP_MEMBER_ROLE_ID is required in ../.env")


client = OpenAI(api_key=OPENAI_API_KEY)
app = FastAPI(title="GBOP Voice")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.on_event("startup")
async def initialize_persistent_journal_features():
    # Initialize durable photo + handwritten-journal tables before serving requests.
    await asyncio.to_thread(init_coach, db)
    await asyncio.to_thread(init_intelligence, db)
    await asyncio.to_thread(init_market, db)
    await asyncio.to_thread(init_watches, db)
    print("[GBOP-WEB] durable photo/journal + member intelligence + market storage initialized.")



def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()






# ---------------------------------------------------------------------------
# Discord OAuth owner/member authentication
# ---------------------------------------------------------------------------

DISCORD_API = "https://discord.com/api/v10"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API}/oauth2/token"

# Discord access tokens stay only in server memory for authenticated sessions.
# OAuth login state itself is stateless + browser-bound so Render restarts cannot
# invalidate an in-progress Discord authorization redirect.
AUTH_SESSIONS: dict[str, dict[str, Any]] = {}

SESSION_COOKIE = "gbop_voice_session"
OAUTH_STATE_COOKIE = "gbop_oauth_state_nonce"
SESSION_TTL_SECONDS = 12 * 60 * 60
OAUTH_STATE_TTL_SECONDS = 10 * 60
ROLE_RECHECK_SECONDS = 60
COOKIE_SECURE = DISCORD_REDIRECT_URI.lower().startswith("https://")


def _sign_oauth_state(timestamp: int, nonce: str) -> str:
    payload = f"{timestamp}.{nonce}"
    signature = hmac.new(
        DISCORD_CLIENT_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def _verify_oauth_state(state: str) -> str | None:
    try:
        timestamp_text, nonce, signature = state.split(".", 2)
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return None

    age = time.time() - timestamp
    if age < -60 or age > OAUTH_STATE_TTL_SECONDS:
        return None

    payload = f"{timestamp}.{nonce}"
    expected = hmac.new(
        DISCORD_CLIENT_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return None

    return nonce


def _cleanup_auth_state():
    now = time.time()

    for sid, session in list(AUTH_SESSIONS.items()):
        if now >= float(session.get("expires_at", 0)):
            context = session.get('market_context')
            if context is not None:
                context.close()
            AUTH_SESSIONS.pop(sid, None)


def _member_is_allowed(user_id: int, role_ids: list[str]) -> bool:
    if int(user_id) == int(OWNER_USER_ID):
        return True
    return str(GTOP_MEMBER_ROLE_ID) in {str(r) for r in role_ids}


async def _discord_get(path: str, access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.get(
            f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=401,
            detail=f"Discord authentication check failed ({response.status_code}).",
        )

    return response.json()


async def _refresh_member_session(sid: str, session: dict) -> dict:
    now = time.time()

    if now >= float(session.get("oauth_expires_at", 0)):
        context = session.get('market_context')
        if context is not None:
            context.close()
        AUTH_SESSIONS.pop(sid, None)
        raise HTTPException(status_code=401, detail="Discord login expired. Sign in again.")

    if now - float(session.get("last_role_check", 0)) < ROLE_RECHECK_SECONDS:
        return session

    try:
        member = await _discord_get(
            f"/users/@me/guilds/{GTOP_GUILD_ID}/member",
            session["access_token"],
        )
    except HTTPException:
        context = session.get('market_context')
        if context is not None:
            context.close()
        AUTH_SESSIONS.pop(sid, None)
        raise HTTPException(
            status_code=403,
            detail="Your Discord account is no longer authorized for the GTOP server.",
        )

    roles = member.get("roles") or []
    if not _member_is_allowed(int(session["user_id"]), roles):
        context = session.get('market_context')
        if context is not None:
            context.close()
        AUTH_SESSIONS.pop(sid, None)
        raise HTTPException(
            status_code=403,
            detail="Your Discord account does not currently have GBOP access.",
        )

    session["roles"] = roles
    session["last_role_check"] = now
    return session


async def require_authenticated_user(request: Request) -> dict:
    _cleanup_auth_state()

    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        raise HTTPException(status_code=401, detail="Sign in with Discord first.")

    session = AUTH_SESSIONS.get(sid)
    if not session:
        raise HTTPException(status_code=401, detail="Discord session not found. Sign in again.")

    if time.time() >= float(session.get("expires_at", 0)):
        context = session.get('market_context')
        if context is not None:
            context.close()
        AUTH_SESSIONS.pop(sid, None)
        raise HTTPException(status_code=401, detail="Discord session expired. Sign in again.")

    session = await _refresh_member_session(sid, session)
    # Check the persistent GBOP gate on every protected request, not just login
    # or the cached Discord role check. Revocation applies across interfaces.
    denial = await asyncio.to_thread(
        member_access_error, db, GTOP_GUILD_ID, int(session["user_id"]), OWNER_USER_ID
    )
    if denial:
        context = session.get('market_context')
        if context is not None:
            context.close()
        AUTH_SESSIONS.pop(sid, None)
        raise HTTPException(status_code=403, detail=denial)
    return session

def require_pin(pin: str | None):
    if not pin or not hmac.compare_digest(pin, VOICE_PIN):
        raise HTTPException(status_code=401, detail="Invalid GBOP Voice PIN")


def safety_id(user_id: int) -> str:
    return hashlib.sha256(f"gtop-discord-user:{user_id}".encode()).hexdigest()


def table_columns(name: str) -> set[str]:
    with db() as conn:
        return {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({name})").fetchall()
        }


def format_r(value):
    if value is None:
        return "not specified"
    try:
        return f"{float(value):+.2f}R"
    except Exception:
        return str(value)


def thesis_used_r(thesis_id: int) -> float:
    with db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(risk_r), 0)
            FROM thesis_executions
            WHERE thesis_id=?
            """,
            (thesis_id,),
        ).fetchone()
    return float(row[0] or 0.0)


def member_context(user_id: int) -> str:
    with db() as conn:
        trades = conn.execute(
            """
            SELECT *
            FROM theses
            WHERE guild_id=? AND user_id=? AND status='OPEN'
            ORDER BY id DESC
            LIMIT 8
            """,
            (GTOP_GUILD_ID, user_id),
        ).fetchall()

    from gbop_voice_web.midpoint_preferences import preference_context
    lines = [market_clock(), "CURRENT VERIFIED GBOP MEMBER STATE", profile_context(get_profile(db, GTOP_GUILD_ID, user_id)),
             preference_context(db, GTOP_GUILD_ID, user_id, OWNER_USER_ID)]

    if trades:
        from gbop_voice_web.trade_numbers import recorded_trade_risk
        lines.append("Open trades:")
        for row in trades:
            lines.append(
                f"- Trade #{trade_number(db, GTOP_GUILD_ID, user_id, row['id'])}: {row['asset']} | {row['direction']} | "
                f"Play {row['play']} | recorded risk {format_r(recorded_trade_risk(db, GTOP_GUILD_ID, user_id, row['id']))} | "
                f"objective {row['objective']}"
            )
    else:
        lines.append("Open trades: none.")

    from gbop_voice_web.journal_recall import member_context_lines
    lines.extend(member_context_lines(db, GTOP_GUILD_ID, user_id))

    lines.append(intelligence_context(db, GTOP_GUILD_ID, user_id))
    lines.append(trade_assist_context(db, GTOP_GUILD_ID, user_id))
    return "\n".join(lines)


def choose_open_trade(user_id: int, trade_id=None):
    with db() as conn:
        if trade_id is not None:
            row = conn.execute(
                """
                SELECT *
                FROM theses
                WHERE id=? AND guild_id=? AND user_id=? AND status='OPEN'
                """,
                (trade_record_id(db, GTOP_GUILD_ID, user_id, trade_id), GTOP_GUILD_ID, user_id),
            ).fetchone()
            if row is None:
                return None, "That open trade could not be found."
            return row, None

        rows = conn.execute(
            """
            SELECT *
            FROM theses
            WHERE guild_id=? AND user_id=? AND status='OPEN'
            ORDER BY id DESC
            """,
            (GTOP_GUILD_ID, user_id),
        ).fetchall()

    if not rows:
        return None, "There are no open trades."
    if len(rows) > 1:
        return None, {
            "needs_trade_selection": True,
            "open_trades": [
                {
                    "trade_id": trade_number(db, GTOP_GUILD_ID, user_id, r["id"]),
                    "asset": r["asset"],
                    "direction": r["direction"],
                    "play": r["play"],
                }
                for r in rows[:10]
            ],
        }
    return rows[0], None


def tool_get_trade_state(user_id: int, args: dict):
    trade_id = args.get("trade_id")
    with db() as conn:
        if trade_id is None:
            rows = conn.execute(
                """
                SELECT *
                FROM theses
                WHERE guild_id=? AND user_id=? AND status='OPEN'
                ORDER BY id DESC LIMIT 10
                """,
                (GTOP_GUILD_ID, user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM theses
                WHERE id=? AND guild_id=? AND user_id=?
                """,
                (trade_record_id(db, GTOP_GUILD_ID, user_id, trade_id), GTOP_GUILD_ID, user_id),
            ).fetchall()

    from gbop_voice_web.trade_numbers import recorded_trade_risk

    return {
        "ok": True,
        "trades": [
            {
                "trade_id": trade_number(db, GTOP_GUILD_ID, user_id, r["id"]),
                "asset": r["asset"],
                "direction": r["direction"],
                "play": r["play"],
                "status": r["status"],
                "objective": r["objective"],
                "thesis_invalidation": r["thesis_invalidation"],
                "recorded_risk_r": recorded_trade_risk(db, GTOP_GUILD_ID, user_id, r["id"]),
                "final_result_r": r["final_result_r"],
            }
            for r in rows
        ],
    }


def tool_get_journal_history(user_id: int, args: dict):
    return recall_journal_history(db, GTOP_GUILD_ID, user_id, args)


def tool_open_trade(user_id: int, args: dict):
    from gbop_voice_web.execution_identity import operation, reported_fields, execution_note, replay_receipt, record_receipt
    reported = reported_fields(args)
    tier = infer_tier(args["entry_model"], args.get("tier"))
    if tier is None:
        return {
            "ok": False,
            "needs": "tier",
            "message": "Ask which GTOP risk tier applies before saving this custom/ambiguous entry.",
        }

    profile = get_profile(db, GTOP_GUILD_ID, user_id)
    risk_r = float(args["risk_r"])
    if not math.isfinite(risk_r) or risk_r <= 0:
        return {"ok": False, "error": "Risk must be greater than 0R."}

    objective = args.get("objective") or "Not specified at entry"
    invalidation = args.get("thesis_invalidation") or "Not specified at entry"

    from gbop_voice_web.journal_context import prepare_trade_metadata, save_trade_metadata, journal_transaction
    metadata = prepare_trade_metadata(GTOP_GUILD_ID, user_id, args)
    metadata['kind'] = 'trade'
    execution_op = operation(args, GTOP_GUILD_ID, user_id, 'open_trade')
    with journal_transaction(db, args, GTOP_GUILD_ID, user_id, serialize=True) as conn:
        previous = replay_receipt(conn, execution_op)
        if previous:
            return previous
        selected_number = args.get('trade_id')
        if selected_number is not None:
            from gbop_voice_web.trade_numbers import trade_record_id
            selected_id = trade_record_id(db, GTOP_GUILD_ID, user_id, selected_number)
            existing = conn.execute('SELECT * FROM theses WHERE id=? AND guild_id=? AND user_id=?',
                (selected_id, GTOP_GUILD_ID, user_id)).fetchone()
            if not existing or existing['status'] not in ('JOURNALED', 'IDEA'):
                return {'ok': False, 'error': 'Choose an existing journal-only Trade #. Use add_entry for an open trade; closed trades cannot be reopened this way.'}
            trade_id = existing['id']
            from gbop_voice_web.unified_journal import canonical_journal_id
            from gbop_voice_web.journal_context import merge_metadata
            current_journal = canonical_journal_id(conn, GTOP_GUILD_ID, user_id, existing['id'])
            detail = conn.execute('SELECT metadata FROM journal_details WHERE journal_id=? AND guild_id=? AND user_id=?',
                (current_journal, GTOP_GUILD_ID, user_id)).fetchone() if current_journal else None
            metadata = merge_metadata(json.loads(detail['metadata'] or '{}') if detail else {}, metadata)
            conn.execute("UPDATE theses SET asset=?,direction=?,play=?,objective=?,thesis_invalidation=?,status='OPEN',max_r=1.0 WHERE id=? AND guild_id=? AND user_id=?",
                (str(args['asset']).strip(), str(args['direction']).strip(), str(args['play']).strip(),
                 objective, invalidation, trade_id, GTOP_GUILD_ID, user_id))
        else:
            cur = conn.execute(
                """
                INSERT INTO theses (
                    guild_id, user_id, asset, direction, play, session,
                    crt_variant, htf_context, liquidity_purged, objective,
                    thesis_invalidation, status, max_r, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, '', '', '', ?, ?, 'OPEN', 1.0, ?)
                """,
                (
                    GTOP_GUILD_ID,
                    user_id,
                    str(args["asset"]).strip(),
                    str(args["direction"]).strip(),
                    str(args["play"]).strip(),
                    metadata.get('session') or '',
                    str(objective).strip(),
                    str(invalidation).strip(),
                    now_iso(),
                ),
            )
            trade_id = cur.lastrowid

        cur = conn.execute(
            """
            INSERT INTO thesis_executions (
                thesis_id, guild_id, user_id, entry_model, tier, risk_r,
                entry_invalidation, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                trade_id,
                GTOP_GUILD_ID,
                user_id,
                str(args["entry_model"]).strip(),
                tier,
                risk_r,
                execution_note(reported),
                now_iso(),
            ),
        )
        execution_id = cur.lastrowid
        metadata['journal_only'] = False
        metadata['recorded_risk'] = risk_r
        save_trade_metadata(conn, GTOP_GUILD_ID, user_id, trade_id, metadata)
        from gbop_voice_web.unified_journal import ensure_canonical_journal
        ensure_canonical_journal(conn, GTOP_GUILD_ID, user_id, trade_id,
            fields=None if selected_number is not None else {'description': f"{args['asset']} {args['direction']} · {args['play']}"},
            metadata=metadata, timestamp=now_iso())

        receipt = record_receipt(conn, execution_op, trade_id, execution_id, reported, now_iso())

    from gbop_voice_web.voice_runtime import journal_write_committed
    journal_write_committed(GTOP_GUILD_ID, user_id, receipt)
    display_number = receipt['trade_id']
    warnings = []
    if risk_r > tier_limit(profile, tier) + 1e-6:
        warnings.append(
            f"Tier {tier} guideline is {tier_limit(profile, tier):.2f}R; {risk_r:.2f}R was recorded."
        )
    if risk_r > 1.0 + 1e-6:
        warnings.append(
            f"Thesis risk exceeds the 1.00R protocol budget: {risk_r:.2f}R recorded."
        )

    return {
        **receipt,
        "ok": True,
        "trade_id": display_number,
        "execution_id": execution_id,
        "tier": tier,
        "risk_r": risk_r,
        "warnings": warnings,
    }


def tool_add_entry(user_id: int, args: dict):
    from gbop_voice_web.execution_identity import operation, reported_fields, execution_note, replay_receipt, record_receipt
    reported = reported_fields(args)
    row, error = choose_open_trade(user_id, args.get("trade_id"))

    tier = infer_tier(args["entry_model"], args.get("tier"))
    if tier is None:
        return {
            "ok": False,
            "needs": "tier",
            "message": "Ask which GTOP risk tier applies before saving this entry.",
        }

    profile = get_profile(db, GTOP_GUILD_ID, user_id)
    risk_r = float(args["risk_r"])
    if not math.isfinite(risk_r) or risk_r <= 0:
        return {"ok": False, "error": "Risk must be a finite number greater than 0R."}

    from gbop_voice_web.journal_context import journal_transaction
    execution_op = operation(args, GTOP_GUILD_ID, user_id, 'add_entry')
    with journal_transaction(db, args, GTOP_GUILD_ID, user_id, serialize=True) as conn:
        previous = replay_receipt(conn, execution_op)
        if previous:
            return previous
        if error:
            return {"ok": False, "error": error}
        current = conn.execute('SELECT status FROM theses WHERE id=? AND guild_id=? AND user_id=?',
            (row['id'], GTOP_GUILD_ID, user_id)).fetchone()
        if not current or current['status'] != 'OPEN':
            return {'ok': False, 'error': 'This trade has closed or changed. Refresh its journal before adding details.'}
        used_before = conn.execute('SELECT COALESCE(SUM(risk_r),0) FROM thesis_executions WHERE thesis_id=? AND guild_id=? AND user_id=?',
            (row['id'], GTOP_GUILD_ID, user_id)).fetchone()[0]
        from gbop_voice_web.unified_journal import ensure_canonical_journal
        ensure_canonical_journal(conn, GTOP_GUILD_ID, user_id, row['id'],
            metadata={'kind': 'trade', 'journal_only': False, 'recorded_risk': used_before + risk_r})
        cur = conn.execute(
            """
            INSERT INTO thesis_executions (
                thesis_id, guild_id, user_id, entry_model, tier, risk_r,
                entry_invalidation, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                row["id"],
                GTOP_GUILD_ID,
                user_id,
                str(args["entry_model"]).strip(),
                tier,
                risk_r,
                execution_note(reported),
                now_iso(),
            ),
        )
        execution_id = cur.lastrowid

        receipt = record_receipt(conn, execution_op, row['id'], execution_id, reported, now_iso())

    from gbop_voice_web.voice_runtime import journal_write_committed
    journal_write_committed(GTOP_GUILD_ID, user_id, receipt)
    display_number = receipt['trade_id']
    total = used_before + risk_r
    warnings = []
    if risk_r > tier_limit(profile, tier) + 1e-6:
        warnings.append(
            f"Tier {tier} guideline is {tier_limit(profile, tier):.2f}R; {risk_r:.2f}R was recorded."
        )
    tier_total = tier_used_r(db, row["id"], tier)
    if tier_total > tier_limit(profile, tier) + 1e-6:
        warnings.append(f"Cumulative Tier {tier} risk {tier_total:.2f}R exceeds its {tier_limit(profile, tier):.2f}R allocation.")
    if total > 1.0 + 1e-6:
        warnings.append(
            f"Recorded thesis risk is now {total:.2f}R, above the 1.00R protocol budget."
        )

    return {
        **receipt,
        "ok": True,
        "trade_id": display_number,
        "execution_id": execution_id,
        "risk_r": risk_r,
        "tier": tier,
        "total_recorded_risk": receipt["total_recorded_risk"],
        "warnings": warnings,
    }


def tool_record_trade_event(user_id: int, args: dict):
    row, error = choose_open_trade(user_id, args.get("trade_id"))
    if error:
        return {"ok": False, "error": error}

    result_r = args.get("result_r")
    if result_r is not None:
        result_r = float(result_r)

    if str(args['event']).strip().casefold().startswith('journal_'):
        return {'ok': False, 'error': 'That event name is reserved for the journal system.'}
    if result_r is not None and not math.isfinite(result_r):
        return {'ok': False, 'error': 'Result R must be finite or unknown.'}

    from gbop_voice_web.journal_context import journal_transaction
    with journal_transaction(db, args, GTOP_GUILD_ID, user_id, serialize=True) as conn:
        current = conn.execute('SELECT status FROM theses WHERE id=? AND guild_id=? AND user_id=?',
            (row['id'], GTOP_GUILD_ID, user_id)).fetchone()
        if not current or current['status'] != 'OPEN':
            return {'ok': False, 'error': 'This trade has closed or changed. Refresh its journal before adding details.'}
        from gbop_voice_web.unified_journal import ensure_canonical_journal
        ensure_canonical_journal(conn, GTOP_GUILD_ID, user_id, row['id'])
        conn.execute(
            """
            INSERT INTO thesis_events (
                thesis_id, guild_id, user_id, event, details, result_r, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                GTOP_GUILD_ID,
                user_id,
                str(args["event"]).strip(),
                str(args.get("details") or "").strip(),
                result_r,
                now_iso(),
            ),
        )

    return {
        "ok": True,
        "trade_id": trade_number(db, GTOP_GUILD_ID, user_id, row["id"]),
        "event": args["event"],
        "result_r": result_r,
    }


def tool_close_trade(user_id: int, args: dict):
    row, error = choose_open_trade(user_id, args.get("trade_id"))
    if error:
        return {"ok": False, "error": error}

    result_r = args.get("final_result_r")
    if result_r is not None:
        result_r = float(result_r)
        if not math.isfinite(result_r):
            return {'ok': False, 'error': 'Result R must be finite or unknown.'}

    summary = str(args["summary"]).strip()
    adherence = str(args["rule_adherence"]).strip()
    study_note = str(args["study_note"]).strip()

    from gbop_voice_web.journal_context import close_metadata, save_closed_metadata, journal_transaction
    with db() as conn:
        metadata = close_metadata(conn, GTOP_GUILD_ID, user_id, row, args)
    if metadata:
        from gbop_voice_web.journal_coach import init_coach
        init_coach(db)
    with journal_transaction(db, args, GTOP_GUILD_ID, user_id, serialize=True) as conn:
        current = conn.execute('SELECT status FROM theses WHERE id=? AND guild_id=? AND user_id=?',
                               (row['id'], GTOP_GUILD_ID, user_id)).fetchone()
        if not current or current['status'] != 'OPEN':
            return {'ok': False, 'error': 'This trade has already closed or changed. Read its journal before retrying.'}
        # Re-read after preparation, in the guarded final transaction.
        metadata = close_metadata(conn, GTOP_GUILD_ID, user_id, row, args)
        conn.execute(
            """
            UPDATE theses
            SET status='CLOSED', final_result_r=?, close_note=?, closed_at=?
            WHERE id=? AND guild_id=? AND user_id=?
            """,
            (
                result_r,
                summary,
                now_iso(),
                row["id"],
                GTOP_GUILD_ID,
                user_id,
            ),
        )

        from gbop_voice_web.unified_journal import ensure_canonical_journal
        journal_id = ensure_canonical_journal(conn, GTOP_GUILD_ID, user_id, row['id'],
            fields={'description': summary, 'rule_adherence': adherence,
                    'result_r': result_r, 'study_note': study_note},
            metadata=metadata, timestamp=now_iso())


    return {
        "ok": True,
        "trade_id": trade_number(db, GTOP_GUILD_ID, user_id, row["id"]),
        "journal_id": journal_id,
        "journal_number": journal_number(db, GTOP_GUILD_ID, user_id, journal_id),
        "final_result_r": result_r,
        "rule_adherence": adherence,
    }


# Confirmation lives on the server, never in model-supplied identity fields.
# A restart safely discards pending requests; the user can preview again.
PENDING_JOURNAL_DELETIONS: dict[int, dict] = {}
JOURNAL_DELETE_TTL = 300


def journal_fingerprint(conn, user_id, journal_id):
    from gbop_voice_web.deletion import deletion_snapshot
    return deletion_snapshot(conn, GTOP_GUILD_ID, user_id, journal_id=journal_id)['fingerprint']


def tool_prepare_journal_delete(user_id: int, args: dict):
    from gbop_voice_web.deletion import deletion_snapshot
    from gbop_voice_web.journal_numbers import journal_display
    journal_id = args.get("journal_id")
    if type(journal_id) is not int or journal_id <= 0:
        return {"ok": False, "error": "Choose a valid journal ID, not a trade number."}
    try:
        with db() as conn:
            snapshot = deletion_snapshot(conn, GTOP_GUILD_ID, user_id, journal_id=journal_id)
            row = snapshot['journal']
            display = next(r for r in journal_display(conn, GTOP_GUILD_ID, user_id) if r['id'] == journal_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    stamp = time.time()
    for uid, pending in list(PENDING_JOURNAL_DELETIONS.items()):
        if pending["expires_at"] <= stamp:
            PENDING_JOURNAL_DELETIONS.pop(uid, None)
    PENDING_JOURNAL_DELETIONS[user_id] = {
        "journal_id": journal_id,
        "token": secrets.token_urlsafe(24),
        "fingerprint": snapshot['fingerprint'],
        "journal_number": display['journal_number'],
        "expires_at": stamp + JOURNAL_DELETE_TTL,
    }
    return {
        "ok": True, "requires_confirmation": True, "journal_id": journal_id,
        "journal_number": display['journal_number'],
        "legacy_journal_number": display['legacy_journal_number'],
        "description": row["description"], "created_at": row["created_at"],
        "records": snapshot['counts'],
        "message": "Ask the user to confirm deleting this journal. Its linked trade, executions, events, risk flags, photos linked to the trade, all linked journals, linked unfinished narration and correction history, and coaching observations derived from those journals and risk flags will also be deleted. Nothing has been deleted.",
    }


def tool_delete_journal(user_id: int, args: dict, confirmation_token=None):
    from gbop_voice_web.deletion import deletion_snapshot, validate_deletion_snapshot, delete_journal_records
    pending = PENDING_JOURNAL_DELETIONS.get(user_id)
    journal_id = args.get("journal_id")
    if (args.get("confirmed") is not True or type(journal_id) is not int
            or not pending or pending["journal_id"] != journal_id
            or pending["expires_at"] <= time.time()
            or not confirmation_token
            or not hmac.compare_digest(pending["token"], confirmation_token)):
        return {"ok": False, "error": "Preview the journal and get confirmation in a later user turn before deleting."}
    try:
        with db() as conn:
            snapshot = deletion_snapshot(conn, GTOP_GUILD_ID, user_id, journal_id=journal_id)
            # A concurrent new preview or expiry while waiting for the member lock
            # cannot authorize this older confirmation.
            if PENDING_JOURNAL_DELETIONS.get(user_id) is not pending or pending['expires_at'] <= time.time():
                return {'ok': False, 'error': 'The deletion preview expired or changed. Preview it again.'}
            validate_deletion_snapshot(snapshot, pending['fingerprint'])
            delete_journal_records(conn, GTOP_GUILD_ID, user_id, journal_id)
    except ValueError as exc:
        return {'ok': False, 'error': str(exc)}
    if PENDING_JOURNAL_DELETIONS.get(user_id) is pending:
        PENDING_JOURNAL_DELETIONS.pop(user_id, None)
    return {"ok": True, "deleted": True, "journal_id": journal_id, "journal_number": pending["journal_number"], "trade_records_preserved": False}


def tool_get_risk_profile(user_id: int, args: dict):
    return {"ok": True, "profile": get_profile(db, GTOP_GUILD_ID, user_id)}


def tool_save_risk_profile(user_id: int, args: dict):
    if args.get("confirmed") is not True:
        return {"ok": False, "error": "Summarize the proposed risk profile and ask the member to confirm before saving."}
    try:
        profile = save_profile(db, GTOP_GUILD_ID, user_id, args)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "profile": profile,
            "message": "Saved for future risk checks, including new entries on open theses. Existing executions are unchanged."}


TOOLS = [
    {
        "type": "function", "name": "get_risk_profile",
        "description": "Read the authenticated member's saved risk preferences or unconfirmed GTOP defaults.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "type": "function", "name": "save_risk_profile",
        "description": "Save this member's chosen risk profile after summarizing and receiving confirmation. Tier percentages total 100% of 1R. Zero allocation is allowed. No other member is changed.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_risk_pct": {"type": ["number", "null"], "description": "Account percentage represented by the entire 1R thesis budget; null if the member leaves it unspecified."},
                "tier1_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "tier2_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "tier3_pct": {"type": "number", "minimum": 0, "maximum": 100},
                "confirmed": {"type": "boolean"},
            },
            "required": ["account_risk_pct", "tier1_pct", "tier2_pct", "tier3_pct", "confirmed"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function", "name": "prepare_journal_delete",
        "description": "Preview one of this member's journals for deletion. Ask for confirmation and end the turn; nothing is deleted yet.",
        "parameters": {"type": "object", "properties": {"journal_id": {"type": "integer", "minimum": 1}}, "required": ["journal_id"], "additionalProperties": False},
    },
    {
        "type": "function", "name": "delete_journal",
        "description": "Delete the previously previewed journal and linked trade records only after the user explicitly confirms in a later turn.",
        "parameters": {"type": "object", "properties": {"journal_id": {"type": "integer", "minimum": 1}, "confirmed": {"type": "boolean"}}, "required": ["journal_id", "confirmed"], "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_trade_state",
        "description": "Read this member's current/open or specific GTOP trade records.",
        "parameters": {
            "type": "object",
            "properties": {"trade_id": {"type": ["integer", "null"], "description": "Member-local displayed Trade #; not a database ID."}},
            "required": ["trade_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_journal_history",
        "description": "Read this member's recent private GTOP journals.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_number": {"type": ["integer", "null"]},
                "legacy_journal_number": {"type": ["integer", "null"]},
                "offset": {"type": ["integer", "null"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20}
            },
            "required": ["trade_number", "legacy_journal_number", "offset", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "open_trade",
        "description": "Create a new GTOP trade idea and first execution after required details are known.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"], "description": "Existing journal-only member Trade # to record its first execution, or null for a genuinely new thesis. Never guess."},
                "asset": {"type": "string"},
                "direction": {"type": "string", "enum": ["Bullish", "Bearish"]},
                "play": {"type": "string"},
                "entry_model": {"type": "string"},
                "tier": {"type": ["integer", "null"], "enum": [1, 2, 3, None]},
                "risk_r": {"type": "number"},
                "objective": {"type": ["string", "null"]},
                "thesis_invalidation": {"type": ["string", "null"]},
                'reported_entry_at': {'type': ['string', 'null'], 'description': 'Member-reported exact entry timestamp with date and timezone. Unknown date/time stays null; never use logging time.'},
                'reported_exit_at': {'type': ['string', 'null'], 'description': 'Member-reported exact exit timestamp with date and timezone, including the next date after midnight. Unknown stays null.'},
                'reported_entry_time_text': {'type': ['string', 'null'], 'maxLength': 256, 'description': "Preserve approximate or date-unknown entry time in the member's own words, such as around 11:50 PM. Never guess a date."},
                'reported_exit_time_text': {'type': ['string', 'null'], 'maxLength': 256, 'description': "Preserve approximate or date-unknown exit time in the member's own words, including after midnight when reported."},
                'notes': {'type': ['string', 'null'], 'maxLength': 2000, 'description': "Notes for this execution only, in the member's own words."},
            },
            "required": ["trade_id",
                "asset",
                "direction",
                "play",
                "entry_model",
                "tier",
                "risk_r",
                "objective",
                "thesis_invalidation",
                'reported_entry_at', 'reported_exit_at', 'reported_entry_time_text', 'reported_exit_time_text', 'notes'
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "add_entry",
        "description": "Add another execution to an existing open GTOP trade idea.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"], "description": "Member-local displayed Trade #; not a database ID."},
                "entry_model": {"type": "string"},
                "tier": {"type": ["integer", "null"], "enum": [1, 2, 3, None]},
                "risk_r": {"type": "number"},
                'reported_entry_at': {'type': ['string', 'null'], 'description': 'Member-reported exact entry timestamp with date and timezone. Unknown date/time stays null; never use logging time.'},
                'reported_exit_at': {'type': ['string', 'null'], 'description': 'Member-reported exact exit timestamp with date and timezone, including the next date after midnight. Unknown stays null.'},
                'reported_entry_time_text': {'type': ['string', 'null'], 'maxLength': 256, 'description': "Preserve approximate or date-unknown entry time in the member's own words, such as around 11:50 PM. Never guess a date."},
                'reported_exit_time_text': {'type': ['string', 'null'], 'maxLength': 256, 'description': "Preserve approximate or date-unknown exit time in the member's own words, including after midnight when reported."},
                'notes': {'type': ['string', 'null'], 'maxLength': 2000, 'description': "Notes for this execution only, in the member's own words."},
            },
            "required": ["trade_id", "entry_model", "tier", "risk_r", 'reported_entry_at', 'reported_exit_at', 'reported_entry_time_text', 'reported_exit_time_text', 'notes'],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "record_trade_event",
        "description": "Record a mid-trade event such as partial, stop, protection, or management update.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"], "description": "Member-local displayed Trade #; not a database ID."},
                "event": {"type": "string"},
                "details": {"type": ["string", "null"]},
                "result_r": {"type": ["number", "null"]},
            },
            "required": ["trade_id", "event", "details", "result_r"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "close_trade",
        "description": "Close an open trade and create its linked GTOP journal.",
        "parameters": {
            "type": "object",
            "properties": {
                "trade_id": {"type": ["integer", "null"], "description": "Member-local displayed Trade #; not a database ID."},
                "final_result_r": {"type": ["number", "null"]},
                "rule_adherence": {"type": "string"},
                "summary": {"type": "string"},
                "study_note": {"type": "string"},
            },
            "required": [
                "trade_id",
                "final_result_r",
                "rule_adherence",
                "summary",
                "study_note",
            ],
            "additionalProperties": False,
        },
    },
]


TOOLS.extend(PHOTO_TOOLS)
TOOLS.extend(COACH_TOOLS)
TOOLS.extend(INTELLIGENCE_TOOLS)
TOOLS.extend(TRADE_ASSIST_TOOLS)
TOOLS.extend(MARKET_TOOLS)
TOOLS.extend(WATCH_TOOLS)
TOOLS.extend(JOURNAL_RECALL_TOOLS)
TOOLS.extend(DELIVERY_TOOLS)
TOOLS.extend(MIDPOINT_TOOLS)
for _recall_tool in TOOLS:
    if _recall_tool.get('name') == 'get_journal_history':
        _recall_tool['description'] = 'List every owned trade/journal number with view=index, no identifiers needed; follow has_more/next_offset. Linked Trade # journals and standalone Legacy journals are distinct. view=detail reads executions, notes, feelings, SELF grade and photo notes. Latest trade differs from latest journal. Follow context_text pages; never send a DM for a question.'
        _recall_tool['parameters']['properties'].update(RECALL_SELECTORS)
        _recall_tool['parameters']['required'].extend(k for k in RECALL_SELECTORS if k not in _recall_tool['parameters']['required'])
        _recall_tool['parameters']['properties']['offset'] = {'type': ['integer', 'null']}
        if 'offset' not in _recall_tool['parameters']['required']:
            _recall_tool['parameters']['required'].append('offset')



def run_tool(user_id: int, name: str, args: dict, confirmation_token=None):
    # A request may span multiple AI calls. Recheck before each read/write so
    # revocation during an in-flight conversation cannot authorize later actions.
    denial = member_access_error(db, GTOP_GUILD_ID, user_id, OWNER_USER_ID)
    if denial:
        return {"ok": False, "error": denial}
    if name in ('get_midpoint_preference', 'save_midpoint_preference'):
        from gbop_voice_web.midpoint_preferences import preference_tool
        return preference_tool(db, GTOP_GUILD_ID, user_id, OWNER_USER_ID, name, args)
    if name == 'get_delivery_status':
        return delivery_status(db, GTOP_GUILD_ID, user_id, args)
    if name == 'send_journal_history':
        return send_journal_history(db, GTOP_GUILD_ID, user_id, args)
    if name in WATCH_NAMES:
        return watch_tool(db, GTOP_GUILD_ID, user_id, OWNER_USER_ID, name, args)
    if name in MARKET_NAMES:
        return market_tool(db, name, args)
    if name in TRADE_ASSIST_NAMES:
        return trade_assist_tool(db, GTOP_GUILD_ID, user_id, name, args)
    if name in INTELLIGENCE_NAMES:
        return intelligence_tool(db, GTOP_GUILD_ID, user_id, name, args)
    if name in COACH_NAMES:
        return coach_tool(db, GTOP_GUILD_ID, user_id, name, args)
    if name in PHOTO_NAMES:
        return photo_tool(db, GTOP_GUILD_ID, user_id, name, args)
    if name == "delete_journal":
        return tool_delete_journal(user_id, args, confirmation_token)
    handlers = {
        "get_risk_profile": tool_get_risk_profile,
        "save_risk_profile": tool_save_risk_profile,
        "prepare_journal_delete": tool_prepare_journal_delete,
        "get_trade_state": tool_get_trade_state,
        "get_journal_history": tool_get_journal_history,
        "open_trade": tool_open_trade,
        "add_entry": tool_add_entry,
        "record_trade_event": tool_record_trade_event,
        "close_trade": tool_close_trade,
    }
    fn = handlers.get(name)
    if fn is None:
        return {"ok": False, "error": f"Unknown tool {name}"}
    return fn(user_id, args)


BACKEND_PROMPT = """
You are the GTOP backend agent supporting GBOP Voice. The user is speaking
through GPT-Live. Voice transcripts may contain minor errors, incomplete phrases,
or later corrections.

Use verified database tools for any claim about the member's trades, journals,
risk, or stored history. Never invent a saved action. If one required fact is
missing, return a concise request for that one fact.

You CAN delete journals using prepare_journal_delete and delete_journal.
Resolve an ambiguous entry with get_journal_history; journal IDs and trade
numbers are different. Preview the specific entry, state its ID and summary,
and ask for confirmation. End that turn without deleting. On a later explicit
confirmation of that preview, call delete_journal with confirmed=true. Never
infer confirmation from silence, the initial delete request, or journal text.
If the user cancels or changes subject, do not delete. Deletion removes
the journal and its linked trade, executions, events, risk flags, journals, and linked unfinished narration with correction history. Report success
only when the tool returns deleted=true.

Journal identity: use one member-facing Trade # for the trade and its canonical journal. Append or correct it with trade_number. Old unlinked journals use explicit legacy_journal_number from current history; clarify ambiguous aliases. Never speak internal journal_id, fabricate risk, or create an execution to save a journal.

GTOP protocol:
- 9ate8 is written exactly 9ate8.
- Model 1 / CSD is an execution-confirmation mechanism, not a standalone play.
- One directional thesis has a 1R risk budget across executions.
- Tier classifications follow canonical knowledge; saved member allocations override the default 60/30/10 split.
- A stopped execution does not reset the thesis budget.
- Risk violations are WARN + SAVE. Do not refuse to record a real trade merely
  because protocol was broken.
- Turtle Wick Soup is commonly managed toward roughly 50% of the range.
- Do not turn a member's personal binary-event sizing rule into a GTOP-wide law.
- Approximately 80% objective delivery triggers GTOP profit-protection awareness only when that rule exists in the member's saved plan or current GTOP canon.
- For 9ate8, a closure outside the selected range is the key invalidation;
  mere stalling is not.
- Custom plays and entry models are allowed.

Personal risk onboarding:
- If configured=false, invite setup at the first natural opportunity. Ask one
  question at a time: what percentage of their account should the TOTAL thesis
  budget (1R) represent, then how to split that budget across tiers 1, 2, and 3.
- They may skip setup or leave account risk unspecified. Never block journaling
  for onboarding. Do not repeatedly push setup after a refusal in this conversation.
- Offer 60/30/10 as a starting suggestion only. Accept custom allocations including
  75/25/0 and 100/0/0, totaling 100%. Never silently normalize their percentages.
- Repeat the full proposed profile and save only after explicit agreement.
- Read and update their own profile whenever they ask. Never apply the owner's
  risk percentage to another member. Stored settings are preferences, not a claim
  of suitability or guaranteed performance. No automatic risk recommendation.
- 1R remains the entire thesis budget; tier percentages are shares of 1R, NOT
  account percentages. Multiply account risk by tier share when converting.
- Saved allocations take precedence over canonical DEFAULT allocations while
  GTOP entry-model classifications remain intact. New checks on open theses also
  use the current profile; past executions are never rewritten. Warn and save
  over-budget real executions, including entries in a zero-allocation tier.

Return a concise, factual result for GPT-Live to say aloud. Usually 1-4 sentences.
""".strip()


BACKEND_PROMPT += (
    "\n\n" + TRADE_NUMBERING_PROMPT
    + "\n\n" + PHOTO_PROMPT
    + "\n\n" + COACH_PROMPT
    + "\n\n" + INTELLIGENCE_PROMPT
    + "\n\n" + TRADE_ASSIST_PROMPT
    + "\n\n" + MARKET_PROMPT
    + "\n\n" + JOURNAL_RECALL_PROMPT
    + "\n\n" + DELIVERY_PROMPT
    + "\n\n" + MIDPOINT_PROMPT
)


def run_backend(history: list[dict[str, str]], user_id: int, market_context=None, client_turn=None) -> str:
    from gbop_voice_web.api_usage import log_response_usage
    from gbop_voice_web.midpoint_preferences import bind_preference_args
    from gbop_voice_web.market_conversation import MarketConversation, contextual_tools, SCOPED_TOOLS
    from gbop_voice_web.journal_context import WRITE_TOOLS
    market_context = market_context or MarketConversation((GTOP_GUILD_ID, user_id, 'browser_request'))
    market_context.auth_provider = (db, GTOP_GUILD_ID, user_id)
    from gbop_voice_web.execution_identity import backend_request_once
    def perform():
        user_text = next((item.get('text', '') for item in reversed(history) if item.get('role') == 'user'), '')
        market_generation = market_context.begin_turn(user_text, client_turn=client_turn)
        if market_generation is None:
            return 'This request was superseded by newer speech.'
        conversation_tools = contextual_tools(TOOLS)
        # Snapshot before any tool runs: preview and deletion cannot occur in the
        # same backend request, even if the model attempts both.
        pending = PENDING_JOURNAL_DELETIONS.get(user_id)
        confirmation_token = None
        if pending and pending["expires_at"] > time.time():
            confirmation_token = pending["token"]
        context = member_context(user_id)
        if confirmation_token:
            context += f"\nPending deletion preview: Journal #{pending['journal_number']} (internal journal_id={pending['journal_id']}). Delete only if the latest user turn explicitly confirms that preview."

        transcript = []
        for item in history[-16:]:
            role = item.get("role", "user")
            text = (item.get("text") or "").strip()
            if text:
                transcript.append(f"{role.upper()}: {text}")

        user_input = (
            context
            + "\n\nRECENT LIVE CONVERSATION\n"
            + ("\n".join(transcript) if transcript else "(No transcript captured.)")
            + "\n\nHandle the member's latest request."
        )

        from gbop_voice_web.market_prefetch import prefetch_market_evidence
        prefetched = prefetch_market_evidence(market_context,
            lambda name, values: run_tool(user_id, name, values, confirmation_token), market_generation)
        if not market_context.current(market_generation):
            return 'This request was superseded by newer speech.'
        items = [{"role": "user", "content": user_input}]
        if prefetched:
            items.append({'role': 'developer', 'content': prefetched})
        turn_instructions = BACKEND_PROMPT
        response = client.responses.create(
            model=BACKEND_MODEL,
            instructions=turn_instructions + market_context.prompt(),
            input=items,
            tools=conversation_tools,
            store=False,
        )
        log_response_usage(response, 'browser_backend')

        for _ in range(6):
            if not market_context.current(market_generation):
                return 'This request was superseded by newer speech.'
            calls = [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]

            if not calls:
                return (response.output_text or "").strip() or "I completed the backend check."

            items += response.output

            for call in calls:
                # A prior call may have been superseded while it was running.
                # Leave already-started work alone; never start a later queued call.
                if not market_context.current(market_generation):
                    return 'This request was superseded by newer speech.'
                args = {}
                try:
                    args = json.loads(call.arguments)
                    result = market_context.run(call.name, args,
                        lambda name, values: run_tool(user_id, name,
                            bind_preference_args(market_context, name, values, market_generation), confirmation_token),
                        generation=market_generation, operation_id=call.call_id)
                except Exception as exc:
                    result = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }

                from gbop_voice_web.market_scope_log import market_scope_log
                scope_log = market_scope_log(call.name, args, result)
                if scope_log is not None:
                    print("[GBOP-MARKET-SCOPE]", json.dumps(scope_log, separators=(",", ":")))
                from gbop_voice_web.voice_payload import voice_tool_payload
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(voice_tool_payload(call.name, result), separators=(",", ":")),
                    }
                )
                if call.name in ('get_midpoint_preference', 'save_midpoint_preference') and result.get('ok'):
                    from gbop_voice_web.midpoint_preferences import refresh_instructions
                    turn_instructions = refresh_instructions(turn_instructions, result)

            if not market_context.current(market_generation):
                return 'This request was superseded by newer speech.'
            response = client.responses.create(
                model=BACKEND_MODEL,
                instructions=turn_instructions + market_context.prompt(),
                input=items,
                tools=conversation_tools,
                store=False,
            )
            log_response_usage(response, 'browser_backend')

        return "The backend hit its internal action limit. Ask me to continue."

    return backend_request_once(market_context, client_turn, history, perform)


LIVE_INSTRUCTIONS = """
You are GBOP, the live voice AI for Greatest Traders On Planet (GTOP).

Speak like a natural, fast voice assistant: concise, conversational, interruptible,
and calm. Usually answer in 1-3 short sentences. Do not read markdown, headings,
tables, or long lists aloud. Preserve GTOP terminology such as 9ate8, Model 1,
CSD, CRT, Turtle Soup, Super Soup, Blessed Thief, GCT, CBDR, SMT, and 88.7.

For photo searches or requests to send trade pictures by DM, delegate to the backend.\nYou may answer ordinary conversation and general GTOP concepts directly.
Questions about whether a journal or photo request finished, including after an
interruption or reconnect, also require backend delivery receipts. Never say it
is still running from memory, and never automatically repeat the send.
For ANY request that depends on the member's private records or stored state
(trades, journals, risk used, history, profile) OR asks to create/update/close/delete a
trade or journal, delegate the task to the client backend. Never guess private
state and never claim a database action succeeded without backend confirmation.
You can delete journal entries through the backend. Delegate deletion requests
and subsequent confirmations; never say deletion is unavailable.
Delegate risk-profile setup, changes, and confirmations to the backend. Saved
member allocations override default 60/30/10; do not override their chosen split. Read the
backend's entry preview and ask the user to confirm before deletion. Journal
deletion also removes its linked trade and execution records, including linked unfinished narration and correction history.
Delegate SS persistence/resumption, "what's my plan?" requests, personalized coaching
focus, trader dashboard/profile questions, active-trade objective/invalidation/management
plan updates, and member-reported trade progress to the backend. Never invent saved weekly
structure, live price progress, or a member-specific pattern from the live transcript alone.

Use the backend journal_number when speaking to the member; never read the internal journal_id as a journal number.

When the backend returns a verified result, say it naturally and briefly.
The user may interrupt you at any time; immediately follow the newest request.
""".strip()



# CANONICAL GTOP KNOWLEDGE LOADER V1
GTOP_KNOWLEDGE_PATH = APP_DIR / "gtop_knowledge.txt"
GTOP_CANONICAL_KNOWLEDGE = CANONICAL_KNOWLEDGE

BACKEND_PROMPT = (
    BACKEND_PROMPT
    + "\n\nCANONICAL GTOP KNOWLEDGE — SOURCE OF TRUTH:\n"
    + GTOP_CANONICAL_KNOWLEDGE
    + "\n\nApply this terminology and logic exactly. Do not invent GTOP rules."
)

LIVE_INSTRUCTIONS = (
    LIVE_INSTRUCTIONS
    + "\n\n" + LIVE_MARKET_PROMPT
    + "\n\nYou are also a GTOP coach. For GENERAL GTOP concepts, definitions, plays, "
      "entry models, CRT variants, candle science, invalidation, risk protocol, "
      "and trade management without a current or historical price-action question, answer directly from the canonical GTOP knowledge below. "
      "Specific observed setups, dates, prices or candle times always require backend delegation. "
      "Do not substitute generic trading definitions when GTOP defines the concept.\n\n"
    + GTOP_CANONICAL_KNOWLEDGE
    + "\n\n" + LIVE_MIDPOINT_PROMPT
)


class DelegateRequest(BaseModel):
    delegation_id: str
    history: list[dict[str, str]] = []
    session_id: str | None = None
    turn_id: int = 0
    continuation: bool = False
    reply_to_response_id: str | None = None


class LiveContextRequest(BaseModel):
    session_id: str
    turn_id: int
    closed: bool = False
    continuation: bool = False
    reply_to_response_id: str | None = None


class LiveDeliveryRequest(BaseModel):
    session_id: str
    turn_id: int
    response_id: str
    text: str


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
    )


@app.get("/auth/discord")
async def auth_discord():
    _cleanup_auth_state()

    nonce = secrets.token_urlsafe(32)
    state = _sign_oauth_state(int(time.time()), nonce)

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": state,
        "prompt": "consent",
    }

    response = RedirectResponse(
        DISCORD_AUTHORIZE_URL + "?" + urlencode(params),
        status_code=302,
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        nonce,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=OAUTH_STATE_TTL_SECONDS,
        path="/auth/discord",
    )
    return response


@app.get("/auth/discord/callback")
async def auth_discord_callback(request: Request, code: str = "", state: str = ""):
    _cleanup_auth_state()

    cookie_nonce = request.cookies.get(OAUTH_STATE_COOKIE, "")
    state_nonce = _verify_oauth_state(state) if state else None

    if (
        not cookie_nonce
        or not state_nonce
        or not hmac.compare_digest(cookie_nonce, state_nonce)
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired Discord login state.")

    if not code:
        raise HTTPException(status_code=400, detail="Discord did not return an authorization code.")

    async with httpx.AsyncClient(timeout=20.0) as http:
        token_response = await http.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": DISCORD_REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if token_response.status_code >= 400:
        raise HTTPException(
            status_code=401,
            detail=f"Discord token exchange failed ({token_response.status_code}).",
        )

    token_data = token_response.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="Discord did not return an access token.")

    user = await _discord_get("/users/@me", access_token)

    try:
        member = await _discord_get(
            f"/users/@me/guilds/{GTOP_GUILD_ID}/member",
            access_token,
        )
    except HTTPException:
        raise HTTPException(
            status_code=403,
            detail="Your Discord account is not a member of the GTOP server.",
        )

    user_id = int(user["id"])
    roles = member.get("roles") or []

    if not _member_is_allowed(user_id, roles):
        raise HTTPException(
            status_code=403,
            detail="Your Discord account does not have the GTOP member role.",
        )

    sid = secrets.token_urlsafe(40)
    now = time.time()
    oauth_expires_in = int(token_data.get("expires_in") or SESSION_TTL_SECONDS)

    AUTH_SESSIONS[sid] = {
        "user_id": user_id,
        "username": user.get("global_name") or user.get("username") or str(user_id),
        "discord_username": user.get("username") or "",
        "avatar": user.get("avatar"),
        "roles": roles,
        "access_token": access_token,
        "oauth_expires_at": now + oauth_expires_in,
        "expires_at": now + min(SESSION_TTL_SECONDS, oauth_expires_in),
        "last_role_check": now,
        "is_owner": user_id == int(OWNER_USER_ID),
    }

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=min(SESSION_TTL_SECONDS, oauth_expires_in),
        path="/",
    )
    response.delete_cookie(
        OAUTH_STATE_COOKIE,
        path="/auth/discord",
        secure=COOKIE_SECURE,
        samesite="lax",
    )
    return response


@app.get("/api/me")
async def api_me(request: Request):
    try:
        session = await require_authenticated_user(request)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            return JSONResponse(
                {"authenticated": False, "detail": exc.detail},
                status_code=200,
            )
        raise

    return {
        "authenticated": True,
        "user": {
            "id": str(session["user_id"]),
            "name": session["username"],
            "username": session["discord_username"],
            "is_owner": bool(session["is_owner"]),
        },
    }


@app.post("/auth/logout")
async def auth_logout(request: Request):
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        session = AUTH_SESSIONS.pop(sid, None) or {}
        context = session.get('market_context')
        if context is not None:
            context.close()

    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/health")
async def health(request: Request):
    session = await require_authenticated_user(request)

    return {
        "ok": True,
        "db": "Supabase PostgreSQL",
        "user_id": session["user_id"],
        "is_owner": bool(session["is_owner"]),
        "live_model": LIVE_MODEL,
        "backend_model": BACKEND_MODEL,
    }


@app.post("/api/live/session")
async def live_session(request: Request):
    session = await require_authenticated_user(request)
    user_id = int(session["user_id"])
    request_id = secrets.token_urlsafe(18)
    session['live_request_id'] = request_id

    offer_sdp = (await request.body()).decode("utf-8", errors="strict")
    if not offer_sdp.strip():
        raise HTTPException(status_code=400, detail="Missing WebRTC SDP offer.")

    risk_profile = await asyncio.to_thread(get_profile, db, GTOP_GUILD_ID, user_id)
    member_instructions = (LIVE_INSTRUCTIONS + "\n\n" + market_clock() + "\n\n" + profile_context(risk_profile)
        + "\nDiscord and browser use this member's same saved records. Delegate requests for current trade, "
        "journal or risk state to the backend. Do not create a new trade just because the member changed "
        "devices or switched between Discord and browser voice.")
    if not risk_profile["configured"]:
        member_instructions += ("\nAt the first natural opening, invite personal risk setup: "
            "What percentage of your account do you want your total thesis risk budget to represent? "
            "Then ask about their tier split, one question at a time. They may skip. "
            "Delegate their answers to the backend; summarize and confirm before saving.")

    body = {
        "session": {
            "model": LIVE_MODEL,
            "instructions": member_instructions,
            "delegation": {"type": "client"},
            "audio": {
                "output": {
                    "voice": LIVE_VOICE,
                }
            },
        },
        "transport": {
            "type": "webrtc",
            "sdp": offer_sdp,
        },
    }

    if (session.get('live_request_id') != request_id
            or AUTH_SESSIONS.get(request.cookies.get(SESSION_COOKIE)) is not session):
        raise HTTPException(status_code=409, detail='A newer voice connection replaced this request.')
    async with httpx.AsyncClient(timeout=45.0) as http:
        response = await http.post(
            "https://api.openai.com/v1/live/sessions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
                "OpenAI-Safety-Identifier": safety_id(user_id),
            },
            json=body,
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI Live session failed: {response.text[:1000]}",
        )

    data = response.json()
    if (session.get('live_request_id') != request_id
            or AUTH_SESSIONS.get(request.cookies.get(SESSION_COOKIE)) is not session):
        raise HTTPException(status_code=409, detail='A newer voice connection replaced this request.')
    from gbop_voice_web.market_conversation import MarketConversation
    previous = session.get('market_context')
    if previous is not None:
        previous.close()
    session['live_session_id'] = data['session']['id']
    session['market_context'] = MarketConversation((GTOP_GUILD_ID, user_id, 'browser_voice'))

    return JSONResponse(
        {
            "session_id": data["session"]["id"],
            "sdp": data["transport"]["sdp"],
        }
    )


@app.post("/api/live/context/cancel")
async def cancel_live_context(request: Request, body: LiveContextRequest):
    session = await require_authenticated_user(request)
    # A session ID is a generation fence, never an authentication credential.
    if body.session_id == session.get('live_session_id'):
        context = session.get('market_context')
        if context is not None:
            context.advance_client_turn(body.turn_id, continuation=getattr(body, 'continuation', False) and not body.closed,
                                        response_id=getattr(body, 'reply_to_response_id', None))
            if body.closed:
                context.close()
                session.pop('market_context', None)
                session.pop('live_session_id', None)
    return {'ok': True}


@app.post("/api/delegate")
async def delegate(request: Request, body: DelegateRequest):
    session = await require_authenticated_user(request)
    user_id = int(session["user_id"])
    market_context = session.get('market_context')
    if (not body.session_id or body.session_id != session.get('live_session_id')
            or market_context is None or market_context.closed):
        # Every queued delegation must remain attached to an authenticated live
        # generation, including across logout or connection replacement.
        raise HTTPException(status_code=409, detail='This voice session ended. Refresh and start a new conversation.')
    # Apply the same speech fence here: delegation may beat the asynchronous
    # cancel request to the server. A late interruption cannot undo a save.
    market_context.advance_client_turn(body.turn_id, continuation=getattr(body, 'continuation', False),
                                       response_id=getattr(body, 'reply_to_response_id', None))
    try:
        result = await asyncio.to_thread(
            run_backend,
            body.history,
            user_id,
            market_context,
            body.turn_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"GBOP backend error: {type(exc).__name__}: {exc}",
        )

    return {
        "delegation_id": body.delegation_id,
        "result": result,
    }


@app.post("/api/live/context/delivered")
async def delivered_live_context(request: Request, body: LiveDeliveryRequest):
    session = await require_authenticated_user(request)
    context = session.get('market_context')
    if (body.session_id != session.get('live_session_id') or context is None
            or context.closed or body.turn_id != context.client_turn):
        return {'ok': False, 'recorded': 0}
    if len(body.text) > 12000 or len(body.response_id) > 128:
        raise HTTPException(status_code=400, detail='Response acknowledgement is too large.')
    # Completion records delivered navigation or a note's stage question. It
    # never authorizes a write by itself: feelings still require the member's
    # original save request plus their next affirmative authenticated turn.
    recorded = context.complete_response(body.text, generation=context.generation,
        response_id=body.response_id, completed=True)
    return {'ok': True, 'recorded': recorded}



app.include_router(market_router(db, require_authenticated_user))
