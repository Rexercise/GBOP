
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
from db_compat import db
from gbop_voice_web.trade_photos import PHOTO_PROMPT, PHOTO_TOOLS, PHOTO_NAMES, photo_tool
from gbop_voice_web.deletion import delete_trade_records
from gbop_voice_web.journal_numbers import journal_number
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
        AUTH_SESSIONS.pop(sid, None)
        raise HTTPException(
            status_code=403,
            detail="Your Discord account is no longer authorized for the GTOP server.",
        )

    roles = member.get("roles") or []
    if not _member_is_allowed(int(session["user_id"]), roles):
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
        AUTH_SESSIONS.pop(sid, None)
        raise HTTPException(status_code=401, detail="Discord session expired. Sign in again.")

    return await _refresh_member_session(sid, session)

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

        journals = conn.execute(
            """
            SELECT *
            FROM journals
            WHERE guild_id=? AND user_id=?
            ORDER BY id DESC
            LIMIT 8
            """,
            (GTOP_GUILD_ID, user_id),
        ).fetchall()

    lines = ["CURRENT VERIFIED GBOP MEMBER STATE", profile_context(get_profile(db, GTOP_GUILD_ID, user_id))]

    if trades:
        lines.append("Open trades:")
        for row in trades:
            lines.append(
                f"- Trade #{row['id']}: {row['asset']} | {row['direction']} | "
                f"Play {row['play']} | recorded risk {thesis_used_r(row['id']):.2f}R | "
                f"objective {row['objective']}"
            )
    else:
        lines.append("Open trades: none.")

    if journals:
        lines.append("Recent journals:")
        for row in journals:
            lines.append(
                f"- Journal #{journal_number(db, GTOP_GUILD_ID, user_id, row['id'])} (internal journal_id={row['id']}): {format_r(row['result_r'])}; "
                f"adherence {row['rule_adherence'] or 'not specified'}; "
                f"study note {row['study_note'] or 'not specified'}"
            )
    else:
        lines.append("Recent journals: none.")

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
                (int(trade_id), GTOP_GUILD_ID, user_id),
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
                    "trade_id": r["id"],
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
                (int(trade_id), GTOP_GUILD_ID, user_id),
            ).fetchall()

    return {
        "ok": True,
        "trades": [
            {
                "trade_id": r["id"],
                "asset": r["asset"],
                "direction": r["direction"],
                "play": r["play"],
                "status": r["status"],
                "objective": r["objective"],
                "thesis_invalidation": r["thesis_invalidation"],
                "recorded_risk_r": thesis_used_r(r["id"]),
                "final_result_r": r["final_result_r"],
            }
            for r in rows
        ],
    }


def tool_get_journal_history(user_id: int, args: dict):
    limit = max(1, min(int(args.get("limit") or 5), 20))
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM journals
            WHERE guild_id=? AND user_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (GTOP_GUILD_ID, user_id, limit),
        ).fetchall()

    return {
        "ok": True,
        "journals": [
            {
                "journal_id": r["id"],
                "journal_number": journal_number(db, GTOP_GUILD_ID, user_id, r["id"]),
                "trade_id": r["thesis_id"] if "thesis_id" in r.keys() else None,
                "result_r": r["result_r"],
                "rule_adherence": r["rule_adherence"],
                "summary": r["description"],
                "study_note": r["study_note"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


def tool_open_trade(user_id: int, args: dict):
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

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO theses (
                guild_id, user_id, asset, direction, play, session,
                crt_variant, htf_context, liquidity_purged, objective,
                thesis_invalidation, status, max_r, created_at
            )
            VALUES (?, ?, ?, ?, ?, '', '', '', '', ?, ?, 'OPEN', 1.0, ?)
            """,
            (
                GTOP_GUILD_ID,
                user_id,
                str(args["asset"]).strip(),
                str(args["direction"]).strip(),
                str(args["play"]).strip(),
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
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?)
            """,
            (
                trade_id,
                GTOP_GUILD_ID,
                user_id,
                str(args["entry_model"]).strip(),
                tier,
                risk_r,
                now_iso(),
            ),
        )
        execution_id = cur.lastrowid

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
        "ok": True,
        "trade_id": trade_id,
        "execution_id": execution_id,
        "tier": tier,
        "risk_r": risk_r,
        "warnings": warnings,
    }


def tool_add_entry(user_id: int, args: dict):
    row, error = choose_open_trade(user_id, args.get("trade_id"))
    if error:
        return {"ok": False, "error": error}

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
    used_before = thesis_used_r(row["id"])

    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO thesis_executions (
                thesis_id, guild_id, user_id, entry_model, tier, risk_r,
                entry_invalidation, note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, '', '', ?)
            """,
            (
                row["id"],
                GTOP_GUILD_ID,
                user_id,
                str(args["entry_model"]).strip(),
                tier,
                risk_r,
                now_iso(),
            ),
        )
        execution_id = cur.lastrowid

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
        "ok": True,
        "trade_id": row["id"],
        "execution_id": execution_id,
        "risk_r": risk_r,
        "tier": tier,
        "total_recorded_risk": total,
        "warnings": warnings,
    }


def tool_record_trade_event(user_id: int, args: dict):
    row, error = choose_open_trade(user_id, args.get("trade_id"))
    if error:
        return {"ok": False, "error": error}

    result_r = args.get("result_r")
    if result_r is not None:
        result_r = float(result_r)

    with db() as conn:
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
        "trade_id": row["id"],
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

    summary = str(args["summary"]).strip()
    adherence = str(args["rule_adherence"]).strip()
    study_note = str(args["study_note"]).strip()

    with db() as conn:
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

        journal_cols = table_columns("journals")
        if "thesis_id" in journal_cols:
            cur = conn.execute(
                """
                INSERT INTO journals (
                    guild_id, user_id, description, rule_adherence, result_r,
                    study_note, created_at, thesis_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    GTOP_GUILD_ID,
                    user_id,
                    summary,
                    adherence,
                    result_r,
                    study_note,
                    now_iso(),
                    row["id"],
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO journals (
                    guild_id, user_id, description, rule_adherence, result_r,
                    study_note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    GTOP_GUILD_ID,
                    user_id,
                    summary,
                    adherence,
                    result_r,
                    study_note,
                    now_iso(),
                ),
            )

        journal_id = cur.lastrowid

    return {
        "ok": True,
        "trade_id": row["id"],
        "journal_id": journal_id,
        "journal_number": journal_number(db, GTOP_GUILD_ID, user_id, journal_id),
        "final_result_r": result_r,
        "rule_adherence": adherence,
    }


# Confirmation lives on the server, never in model-supplied identity fields.
# A restart safely discards pending requests; the user can preview again.
PENDING_JOURNAL_DELETIONS: dict[int, dict] = {}
JOURNAL_DELETE_TTL = 300


def journal_fingerprint(row):
    return hashlib.sha256(
        json.dumps(dict(row), sort_keys=True, default=str).encode()
    ).hexdigest()


def tool_prepare_journal_delete(user_id: int, args: dict):
    journal_id = args.get("journal_id")
    if type(journal_id) is not int or journal_id <= 0:
        return {"ok": False, "error": "Choose a valid journal ID, not a trade number."}
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM journals WHERE id=? AND guild_id=? AND user_id=?",
            (journal_id, GTOP_GUILD_ID, user_id),
        ).fetchone()
    if row is None:
        return {"ok": False, "error": "That journal was not found in your account."}
    stamp = time.time()
    for uid, pending in list(PENDING_JOURNAL_DELETIONS.items()):
        if pending["expires_at"] <= stamp:
            PENDING_JOURNAL_DELETIONS.pop(uid, None)
    PENDING_JOURNAL_DELETIONS[user_id] = {
        "journal_id": journal_id,
        "token": secrets.token_urlsafe(24),
        "fingerprint": journal_fingerprint(row),
        "journal_number": journal_number(db, GTOP_GUILD_ID, user_id, journal_id),
        "expires_at": stamp + JOURNAL_DELETE_TTL,
    }
    return {
        "ok": True, "requires_confirmation": True, "journal_id": journal_id,
        "journal_number": journal_number(db, GTOP_GUILD_ID, user_id, journal_id),
        "description": row["description"], "created_at": row["created_at"],
        "message": "Ask the user to confirm deleting this journal. Its linked trade, executions, events, risk flags, and all linked journals will also be deleted. Nothing has been deleted.",
    }


def tool_delete_journal(user_id: int, args: dict, confirmation_token=None):
    pending = PENDING_JOURNAL_DELETIONS.get(user_id)
    journal_id = args.get("journal_id")
    if (args.get("confirmed") is not True or type(journal_id) is not int
            or not pending or pending["journal_id"] != journal_id
            or pending["expires_at"] <= time.time()
            or not confirmation_token
            or not hmac.compare_digest(pending["token"], confirmation_token)):
        return {"ok": False, "error": "Preview the journal and get confirmation in a later user turn before deleting."}
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM journals WHERE id=? AND guild_id=? AND user_id=? FOR UPDATE",
            (journal_id, GTOP_GUILD_ID, user_id),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "That journal was not found in your account. Nothing deleted."}
        if journal_fingerprint(row) != pending["fingerprint"]:
            return {"ok": False, "error": "The journal changed. Preview it again and ask for confirmation."}
        if row["thesis_id"] is not None:
            delete_trade_records(conn, GTOP_GUILD_ID, user_id, row["thesis_id"])
            deleted = row
        else:
            deleted = conn.execute(
                "DELETE FROM journals WHERE id=? AND guild_id=? AND user_id=? RETURNING id",
                (journal_id, GTOP_GUILD_ID, user_id),
            ).fetchone()
    if not deleted:
        return {"ok": False, "error": "No journal was deleted."}
    if PENDING_JOURNAL_DELETIONS.get(user_id) is pending:
        PENDING_JOURNAL_DELETIONS.pop(user_id, None)
    return {"ok": True, "deleted": True, "journal_id": deleted["id"], "journal_number": pending["journal_number"], "trade_records_preserved": False}


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
        "description": "Delete the previously previewed journal only after the user explicitly confirms in a later turn. Preserves trades and executions.",
        "parameters": {"type": "object", "properties": {"journal_id": {"type": "integer", "minimum": 1}, "confirmed": {"type": "boolean"}}, "required": ["journal_id", "confirmed"], "additionalProperties": False},
    },
    {
        "type": "function",
        "name": "get_trade_state",
        "description": "Read this member's current/open or specific GTOP trade records.",
        "parameters": {
            "type": "object",
            "properties": {"trade_id": {"type": ["integer", "null"]}},
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
                "limit": {"type": "integer", "minimum": 1, "maximum": 20}
            },
            "required": ["limit"],
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
                "asset": {"type": "string"},
                "direction": {"type": "string", "enum": ["Bullish", "Bearish"]},
                "play": {"type": "string"},
                "entry_model": {"type": "string"},
                "tier": {"type": ["integer", "null"], "enum": [1, 2, 3, None]},
                "risk_r": {"type": "number"},
                "objective": {"type": ["string", "null"]},
                "thesis_invalidation": {"type": ["string", "null"]},
            },
            "required": [
                "asset",
                "direction",
                "play",
                "entry_model",
                "tier",
                "risk_r",
                "objective",
                "thesis_invalidation",
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
                "trade_id": {"type": ["integer", "null"]},
                "entry_model": {"type": "string"},
                "tier": {"type": ["integer", "null"], "enum": [1, 2, 3, None]},
                "risk_r": {"type": "number"},
            },
            "required": ["trade_id", "entry_model", "tier", "risk_r"],
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
                "trade_id": {"type": ["integer", "null"]},
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
                "trade_id": {"type": ["integer", "null"]},
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


def run_tool(user_id: int, name: str, args: dict, confirmation_token=None):
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
the journal and its linked trade, executions, events, risk flags, and journals. Report success
only when the tool returns deleted=true.

Journal numbering: speak/display journal_number, never internal journal_id. Resolve displayed numbers using current journal history and pass the matching internal journal_id to tools.

GTOP protocol:
- 9ate8 is written exactly 9ate8.
- Model 1 / CSD is an execution-confirmation mechanism, not a standalone play.
- One directional thesis has a 1R risk budget across executions.
- Tier classifications follow canonical knowledge; saved member allocations override the default 60/30/10 split.
- A stopped execution does not reset the thesis budget.
- Risk violations are WARN + SAVE. Do not refuse to record a real trade merely
  because protocol was broken.
- Turtle Wick Soup is commonly managed toward roughly 50% of the range.
- Approximately 80% objective delivery triggers GTOP profit-protection awareness.
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


BACKEND_PROMPT += "\n\n" + PHOTO_PROMPT


def run_backend(history: list[dict[str, str]], user_id: int) -> str:
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

    response = client.responses.create(
        model=BACKEND_MODEL,
        instructions=BACKEND_PROMPT,
        input=user_input,
        tools=TOOLS,
        store=False,
    )

    items = [{"role": "user", "content": user_input}]
    for _ in range(6):
        calls = [
            item
            for item in response.output
            if getattr(item, "type", None) == "function_call"
        ]

        if not calls:
            return (response.output_text or "").strip() or "I completed the backend check."

        items += response.output

        for call in calls:
            try:
                args = json.loads(call.arguments)
                result = run_tool(user_id, call.name, args, confirmation_token)
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result),
                }
            )

        response = client.responses.create(
            model=BACKEND_MODEL,
            instructions=BACKEND_PROMPT,
            input=items,
            tools=TOOLS,
            store=False,
        )

    return "The backend hit its internal action limit. Ask me to continue."


LIVE_INSTRUCTIONS = """
You are GBOP, the live voice AI for Greatest Traders On Planet (GTOP).

Speak like a natural, fast voice assistant: concise, conversational, interruptible,
and calm. Usually answer in 1-3 short sentences. Do not read markdown, headings,
tables, or long lists aloud. Preserve GTOP terminology such as 9ate8, Model 1,
CSD, CRT, Turtle Soup, Super Soup, Blessed Thief, GCT, CBDR, SMT, and 88.7.

For photo searches or requests to send trade pictures by DM, delegate to the backend.\nYou may answer ordinary conversation and general GTOP concepts directly.
For ANY request that depends on the member's private records or stored state
(trades, journals, risk used, history, profile) OR asks to create/update/close/delete a
trade or journal, delegate the task to the client backend. Never guess private
state and never claim a database action succeeded without backend confirmation.
You can delete journal entries through the backend. Delegate deletion requests
and subsequent confirmations; never say deletion is unavailable.
Delegate risk-profile setup, changes, and confirmations to the backend. Saved
member allocations override default 60/30/10; do not override their chosen split. Read the
backend's entry preview and ask the user to confirm before deletion. Journal
deletion also removes its linked trade and execution records.

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
    + "\n\nYou are also a GTOP coach. For GTOP concepts, definitions, plays, "
      "entry models, CRT variants, candle science, invalidation, risk protocol, "
      "and trade management, answer directly from the canonical GTOP knowledge below. "
      "Do not substitute generic trading definitions when GTOP defines the concept.\n\n"
    + GTOP_CANONICAL_KNOWLEDGE
)


class DelegateRequest(BaseModel):
    delegation_id: str
    history: list[dict[str, str]] = []


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
        AUTH_SESSIONS.pop(sid, None)

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

    offer_sdp = (await request.body()).decode("utf-8", errors="strict")
    if not offer_sdp.strip():
        raise HTTPException(status_code=400, detail="Missing WebRTC SDP offer.")

    risk_profile = await asyncio.to_thread(get_profile, db, GTOP_GUILD_ID, user_id)
    member_instructions = LIVE_INSTRUCTIONS + "\n\n" + profile_context(risk_profile)
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

    return JSONResponse(
        {
            "session_id": data["session"]["id"],
            "sdp": data["transport"]["sdp"],
        }
    )


@app.post("/api/delegate")
async def delegate(request: Request, body: DelegateRequest):
    session = await require_authenticated_user(request)
    user_id = int(session["user_id"])

    try:
        result = await asyncio.to_thread(
            run_backend,
            body.history,
            user_id,
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

