
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_compat import db
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
DB_PATH = PROJECT_DIR / "gbop.db"

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

# Local preview sessions. Discord access tokens stay only in server memory.
# Restarting GBOP Voice logs everyone out, which is fine for this local stage.
OAUTH_STATES: dict[str, float] = {}
AUTH_SESSIONS: dict[str, dict[str, Any]] = {}

SESSION_COOKIE = "gbop_voice_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
ROLE_RECHECK_SECONDS = 60


def _cleanup_auth_state():
    now = time.time()

    for state, created_at in list(OAUTH_STATES.items()):
        if now - created_at > 600:
            OAUTH_STATES.pop(state, None)

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

    lines = ["CURRENT VERIFIED GBOP MEMBER STATE"]

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
                f"- Journal #{row['id']}: {format_r(row['result_r'])}; "
                f"adherence {row['rule_adherence'] or 'not specified'}; "
                f"study note {row['study_note'] or 'not specified'}"
            )
    else:
        lines.append("Recent journals: none.")

    return "\n".join(lines)


def tier_max_r(tier: int) -> float:
    return {1: 1.0, 2: 0.5, 3: 0.33}.get(int(tier), 0.33)


def infer_tier(entry_model: str, supplied):
    if supplied in (1, 2, 3):
        return int(supplied)

    name = (entry_model or "").lower()

    if "model 1" in name or "csd" in name:
        return 1
    if "turtle wick" in name or "wick soup" in name:
        return 2
    if "super soup" in name or "blessed thief" in name or "88.7" in name or "ote" in name:
        return 3
    return None


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

    risk_r = float(args["risk_r"])
    if risk_r <= 0:
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
    if risk_r > tier_max_r(tier) + 1e-6:
        warnings.append(
            f"Tier {tier} guideline is {tier_max_r(tier):.2f}R; {risk_r:.2f}R was recorded."
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

    risk_r = float(args["risk_r"])
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
    if risk_r > tier_max_r(tier) + 1e-6:
        warnings.append(
            f"Tier {tier} guideline is {tier_max_r(tier):.2f}R; {risk_r:.2f}R was recorded."
        )
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
        "final_result_r": result_r,
        "rule_adherence": adherence,
    }


TOOLS = [
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


def run_tool(user_id: int, name: str, args: dict):
    handlers = {
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

GTOP protocol:
- 9ate8 is written exactly 9ate8.
- Model 1 / CSD is an execution-confirmation mechanism, not a standalone play.
- One directional thesis has a 1R risk budget across executions.
- Tier 1 confirmed: up to 1.00R.
- Tier 2 early confirmation: up to 0.50R.
- Tier 3 anticipatory/risk entry: up to 0.33R.
- A stopped execution does not reset the thesis budget.
- Risk violations are WARN + SAVE. Do not refuse to record a real trade merely
  because protocol was broken.
- Turtle Wick Soup is commonly managed toward roughly 50% of the range.
- 80%-88.7% objective delivery triggers GTOP profit-protection awareness.
- For 9ate8, a closure outside the selected range is the key invalidation;
  mere stalling is not.
- Custom plays and entry models are allowed.

Return a concise, factual result for GPT-Live to say aloud. Usually 1-4 sentences.
""".strip()


def run_backend(history: list[dict[str, str]], user_id: int) -> str:
    context = member_context(user_id)

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
                result = run_tool(user_id, call.name, args)
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

You may answer ordinary conversation and general GTOP concepts directly.
For ANY request that depends on the member's private records or stored state
(trades, journals, risk used, history, profile) OR asks to create/update/close a
trade or journal, delegate the task to the client backend. Never guess private
state and never claim a database action succeeded without backend confirmation.

When the backend returns a verified result, say it naturally and briefly.
The user may interrupt you at any time; immediately follow the newest request.
""".strip()



# CANONICAL GTOP KNOWLEDGE LOADER V1
GTOP_KNOWLEDGE_PATH = APP_DIR / "gtop_knowledge.txt"
GTOP_CANONICAL_KNOWLEDGE = GTOP_KNOWLEDGE_PATH.read_text().strip()

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

    state = secrets.token_urlsafe(32)
    OAUTH_STATES[state] = time.time()

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds.members.read",
        "state": state,
        "prompt": "consent",
    }

    return RedirectResponse(
        DISCORD_AUTHORIZE_URL + "?" + urlencode(params),
        status_code=302,
    )


@app.get("/auth/discord/callback")
async def auth_discord_callback(code: str = "", state: str = ""):
    _cleanup_auth_state()

    created_at = OAUTH_STATES.pop(state, None)
    if not state or created_at is None or time.time() - created_at > 600:
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
        secure=False,
        max_age=min(SESSION_TTL_SECONDS, oauth_expires_in),
        path="/",
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
        "db": DB_PATH.name,
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

    body = {
        "session": {
            "model": LIVE_MODEL,
            "instructions": LIVE_INSTRUCTIONS,
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
